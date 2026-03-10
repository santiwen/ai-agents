"""
pl_file_parser.py
Parsuje Perl (.pl) súbory, ktoré wrappujú SQL objekty (stored procedures, tables, views).
Extrahuje SQL bloky a identifikuje typ objektu, meno a závislosti.
"""

import re
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from pathlib import Path


@dataclass
class SQLObject:
    """Jeden SQL objekt extrahovaný z PL súboru."""
    name: str
    obj_type: str          # procedure, table, view, trigger, index, function
    sql_body: str
    source_file: str
    line_start: int
    line_end: int
    dependencies: List[str] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    columns: List[Dict] = field(default_factory=list)   # pre tables
    metadata: Dict = field(default_factory=dict)

    def __repr__(self):
        return f"<SQLObject {self.obj_type.upper()} {self.name} ({self.source_file}:{self.line_start})>"


class PLFileParser:
    """
    Parser pre Perl súbory obsahujúce SQL DDL/DML bloky.

    Podporované vzory:
      - Sybase stored procedures (CREATE PROCEDURE ... AS ... GO)
      - Table DDL (CREATE TABLE ... GO)
      - Views (CREATE VIEW ... AS ... GO)
      - Triggers (CREATE TRIGGER ... AS ... GO)
      - Indexes (CREATE [UNIQUE] INDEX ... ON ...)
      - Heredoc SQL v Perlu:  $sql = <<'SQL'; ... SQL
      - qq{} bloky: $sql = qq{ CREATE ... };
      - dbcmd() volania s inline SQL
    """

    # Sybase GO statement - koniec SQL bloku
    GO_PATTERN = re.compile(r'^\s*[Gg][Oo]\s*$', re.MULTILINE)

    # SQL objekt hlavičky
    OBJECT_PATTERNS = {
        'procedure': re.compile(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?PROC(?:EDURE)?\s+([^\s\(]+)',
            re.IGNORECASE
        ),
        'function': re.compile(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([^\s\(]+)',
            re.IGNORECASE
        ),
        'table': re.compile(
            r'CREATE\s+TABLE\s+([^\s\(]+)',
            re.IGNORECASE
        ),
        'view': re.compile(
            r'CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([^\s\(]+)',
            re.IGNORECASE
        ),
        'trigger': re.compile(
            r'CREATE\s+TRIGGER\s+([^\s\(]+)',
            re.IGNORECASE
        ),
        'index': re.compile(
            r'CREATE\s+(?:UNIQUE\s+)?(?:CLUSTERED\s+)?INDEX\s+([^\s]+)\s+ON\s+([^\s\(]+)',
            re.IGNORECASE
        ),
    }

    # Perl heredoc vzory
    HEREDOC_PATTERN = re.compile(
        r'\$\w+\s*=\s*<<[\'"]?(\w+)[\'"]?\s*;(.*?)^\1',
        re.DOTALL | re.MULTILINE
    )

    # Perl qq{} blok
    QQ_PATTERN = re.compile(
        r'\$\w+\s*=\s*qq\s*\{(.*?)\}',
        re.DOTALL
    )

    # dbcmd / ct_command volania
    DBCMD_PATTERN = re.compile(
        r'dbcmd\s*\(\s*\$\w+\s*,\s*["\'](.+?)["\']',
        re.DOTALL
    )

    # Exec stored proc
    EXEC_PATTERN = re.compile(
        r'\bexec(?:ute)?\s+([a-zA-Z_][a-zA-Z0-9_\.]+)',
        re.IGNORECASE
    )

    # Table references v SQL (FROM, JOIN, UPDATE, INSERT INTO)
    TABLE_REF_PATTERN = re.compile(
        r'\b(?:FROM|JOIN|UPDATE|INSERT\s+INTO|INTO)\s+([a-zA-Z_][a-zA-Z0-9_\.]+)',
        re.IGNORECASE
    )

    # EXEC sp volania v kóde
    SP_CALL_PATTERN = re.compile(
        r'\bexec(?:ute)?\s+([a-zA-Z_][a-zA-Z0-9_\.]+)',
        re.IGNORECASE
    )

    def __init__(self):
        self.objects: List[SQLObject] = []

    def parse_file(self, filepath: str) -> List[SQLObject]:
        """Parsuje jeden PL súbor a vráti list SQL objektov."""
        filepath = str(filepath)
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            lines = content.splitlines()

        objects = []

        # Extrahuj SQL bloky z rôznych Perl vzorov
        sql_blocks = self._extract_sql_blocks(content, lines, filepath)

        for sql_text, line_start, line_end in sql_blocks:
            parsed = self._parse_sql_block(sql_text, filepath, line_start, line_end)
            objects.extend(parsed)

        # Fallback: parsuj celý súbor ako SQL ak nie sú Perl wrappers
        if not objects and self._looks_like_pure_sql(content):
            parsed = self._parse_sql_block(content, filepath, 1, len(lines))
            objects.extend(parsed)

        # Deduplikácia - rovnaký objekt môže byť nájdený cez heredoc aj GO-split
        seen = set()
        unique = []
        for obj in objects:
            key = (obj.name.lower(), obj.obj_type)
            if key not in seen:
                seen.add(key)
                unique.append(obj)
        return unique

    def parse_directory(self, directory: str, extensions: List[str] = None) -> List[SQLObject]:
        """Parsuje všetky PL/SQL súbory v adresári rekurzívne."""
        if extensions is None:
            extensions = ['.pl', '.sql', '.ddl', '.sp']

        all_objects = []
        directory = Path(directory)

        for ext in extensions:
            for filepath in directory.rglob(f'*{ext}'):
                try:
                    objects = self.parse_file(str(filepath))
                    all_objects.extend(objects)
                except Exception as e:
                    print(f"[WARN] Chyba pri parsovaní {filepath}: {e}")

        return all_objects

    def _extract_sql_blocks(self, content: str, lines: List[str], filepath: str
                            ) -> List[Tuple[str, int, int]]:
        """Extrahuje SQL bloky zo všetkých Perl vzorov."""
        blocks = []

        # 1. Heredoc bloky
        for m in self.HEREDOC_PATTERN.finditer(content):
            sql = m.group(2).strip()
            if self._contains_sql_create(sql):
                line_start = content[:m.start()].count('\n') + 1
                line_end = content[:m.end()].count('\n') + 1
                blocks.append((sql, line_start, line_end))

        # 2. qq{} bloky
        for m in self.QQ_PATTERN.finditer(content):
            sql = m.group(1).strip()
            if self._contains_sql_create(sql):
                line_start = content[:m.start()].count('\n') + 1
                line_end = content[:m.end()].count('\n') + 1
                blocks.append((sql, line_start, line_end))

        # 3. GO-delimited bloky (Sybase štandard)
        go_blocks = self._split_by_go(content)
        for sql, line_start, line_end in go_blocks:
            if self._contains_sql_create(sql):
                # Avoid duplikáty zo heredoc
                already = any(b[0].strip() == sql.strip() for b in blocks)
                if not already:
                    blocks.append((sql, line_start, line_end))

        # 4. Ak nič iné - cely súbor
        if not blocks:
            blocks.append((content, 1, len(lines)))

        return blocks

    def _split_by_go(self, content: str) -> List[Tuple[str, int, int]]:
        """Rozdelí SQL obsah po GO statementoch."""
        blocks = []
        parts = re.split(r'^\s*[Gg][Oo]\s*$', content, flags=re.MULTILINE)

        line_cursor = 1
        for part in parts:
            part_stripped = part.strip()
            line_count = part.count('\n')
            line_end = line_cursor + line_count
            if part_stripped:
                blocks.append((part_stripped, line_cursor, line_end))
            line_cursor = line_end + 1  # +1 pre GO riadok

        return blocks

    def _parse_sql_block(self, sql: str, filepath: str, line_start: int, line_end: int
                         ) -> List[SQLObject]:
        """Parsuje jeden SQL blok a vráti zoznam SQL objektov."""
        objects = []
        sql_clean = self._strip_sql_comments(sql)

        # Skús každý typ objektu
        for obj_type, pattern in self.OBJECT_PATTERNS.items():
            for m in pattern.finditer(sql_clean):
                if obj_type == 'index':
                    name = m.group(1)
                    metadata = {'table': m.group(2)}
                else:
                    name = m.group(1).strip().strip('[]`"\'')
                    metadata = {}

                # Vypočítaj relatívny line_start pre tento match
                prefix = sql_clean[:m.start()]
                obj_line_start = line_start + prefix.count('\n')

                # Extrahuj telo objektu
                obj_sql = self._extract_object_body(sql_clean, m.start(), obj_type)
                obj_line_end = obj_line_start + obj_sql.count('\n')

                # Extrahuj závislosti
                deps = self._extract_dependencies(obj_sql, name)
                params = self._extract_parameters(obj_sql, obj_type)
                columns = self._extract_columns(obj_sql, obj_type)

                obj = SQLObject(
                    name=name,
                    obj_type=obj_type,
                    sql_body=obj_sql,
                    source_file=filepath,
                    line_start=obj_line_start,
                    line_end=obj_line_end,
                    dependencies=deps,
                    parameters=params,
                    columns=columns,
                    metadata=metadata,
                )
                objects.append(obj)

        return objects

    def _extract_object_body(self, sql: str, start_pos: int, obj_type: str) -> str:
        """Extrahuje kompletné telo SQL objektu od start_pos."""
        # Vezmi od start_pos do konca bloku
        body = sql[start_pos:]

        # Pre Sybase: koniec je GO alebo ďalší CREATE
        go_match = re.search(r'^\s*[Gg][Oo]\s*$', body, re.MULTILINE)
        next_create = re.search(r'\nCREATE\s+', body[10:], re.IGNORECASE)  # skip first

        end_pos = len(body)
        if go_match and go_match.start() < end_pos:
            end_pos = go_match.start()
        if next_create and (next_create.start() + 10) < end_pos:
            end_pos = next_create.start() + 10

        return body[:end_pos].strip()

    def _extract_dependencies(self, sql: str, self_name: str) -> List[str]:
        """Extrahuje všetky objekty, na ktoré SQL závisí."""
        deps = set()

        # EXEC volania (iné stored procedúry)
        for m in self.SP_CALL_PATTERN.finditer(sql):
            dep_name = m.group(1).strip()
            if dep_name.lower() != self_name.lower():
                deps.add(dep_name)

        # FROM/JOIN/UPDATE/INSERT referencie (tabuľky)
        for m in self.TABLE_REF_PATTERN.finditer(sql):
            dep_name = m.group(1).strip()
            # Filtruj SQL keywords
            if dep_name.upper() not in {
                'SELECT', 'WHERE', 'SET', 'VALUES', 'BEGIN', 'END',
                'TRANSACTION', 'ROLLBACK', 'COMMIT', 'TABLE', 'INDEX',
                'PROCEDURE', 'VIEW', 'TRIGGER', 'FUNCTION',
            }:
                if dep_name.lower() != self_name.lower():
                    deps.add(dep_name)

        return sorted(list(deps))

    def _extract_parameters(self, sql: str, obj_type: str) -> List[str]:
        """Extrahuje parametre stored procedúry alebo funkcie."""
        if obj_type not in ('procedure', 'function'):
            return []

        params = []
        # Sybase: @param_name datatype [= default]
        param_pattern = re.compile(
            r'@(\w+)\s+(\w+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)'
            r'(?:\s*=\s*([^,\n\)]+))?',
            re.IGNORECASE
        )
        for m in param_pattern.finditer(sql[:2000]):  # len header
            params.append(f"@{m.group(1)} {m.group(2)}")

        return params

    def _extract_columns(self, sql: str, obj_type: str) -> List[Dict]:
        """Extrahuje stĺpce tabuľky z CREATE TABLE DDL."""
        if obj_type != 'table':
            return []

        columns = []
        # Nájdi CREATE TABLE ... (
        table_body_match = re.search(r'CREATE\s+TABLE\s+\S+\s*\((.*?)\)',
                                     sql, re.DOTALL | re.IGNORECASE)
        if not table_body_match:
            return []

        body = table_body_match.group(1)
        # Každý riadok = stĺpec alebo constraint
        col_pattern = re.compile(
            r'^\s*(\w+)\s+(\w+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)'
            r'(?:\s+(NOT\s+NULL|NULL|IDENTITY|DEFAULT\s+\S+))?',
            re.IGNORECASE | re.MULTILINE
        )
        skip_keywords = {
            'PRIMARY', 'FOREIGN', 'UNIQUE', 'CHECK', 'CONSTRAINT',
            'INDEX', 'KEY', 'GO', 'ALTER', 'CREATE',
        }
        for m in col_pattern.finditer(body):
            col_name = m.group(1)
            if col_name.upper() in skip_keywords:
                continue
            columns.append({
                'name': col_name,
                'datatype': m.group(2),
                'nullable': 'NOT NULL' not in (m.group(3) or '').upper(),
            })

        return columns

    def _strip_sql_comments(self, sql: str) -> str:
        """Odstráni SQL line comments (--) a block comments (/* */)."""
        # Block comments
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        # Line comments - ale zachovaj newlines
        sql = re.sub(r'--[^\n]*', '', sql)
        return sql

    def _contains_sql_create(self, text: str) -> bool:
        """Rýchla kontrola či text obsahuje CREATE statement."""
        return bool(re.search(r'\bCREATE\b', text, re.IGNORECASE))

    def _looks_like_pure_sql(self, content: str) -> bool:
        """Skontroluje či súbor je čistý SQL (nie Perl wrapper)."""
        perl_indicators = [
            r'^\s*use\s+\w+',
            r'^\s*my\s+\$',
            r'^\s*sub\s+\w+',
            r'->\w+\(',
        ]
        for indicator in perl_indicators:
            if re.search(indicator, content[:500], re.MULTILINE):
                return False
        return self._contains_sql_create(content)

    def format_summary(self, objects: List[SQLObject]) -> str:
        """Vypíše prehľad parsovaných objektov."""
        if not objects:
            return "Žiadne objekty nenájdené."

        by_type = {}
        for obj in objects:
            by_type.setdefault(obj.obj_type, []).append(obj.name)

        lines = [f"Celkom objektov: {len(objects)}"]
        for obj_type, names in sorted(by_type.items()):
            lines.append(f"  {obj_type.upper()}: {len(names)}")
            for name in names[:5]:
                lines.append(f"    - {name}")
            if len(names) > 5:
                lines.append(f"    ... a {len(names)-5} ďalších")

        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Použitie: python pl_file_parser.py <súbor.pl|adresár>")
        sys.exit(1)

    parser = PLFileParser()
    target = sys.argv[1]

    if os.path.isdir(target):
        objects = parser.parse_directory(target)
    else:
        objects = parser.parse_file(target)

    print(parser.format_summary(objects))
    print()
    for obj in objects[:10]:
        print(f"[{obj.obj_type.upper()}] {obj.name}")
        print(f"  Súbor: {obj.source_file}:{obj.line_start}")
        if obj.parameters:
            print(f"  Parametre: {', '.join(obj.parameters[:3])}")
        if obj.dependencies:
            print(f"  Závislosti: {', '.join(obj.dependencies[:5])}")
        print()
