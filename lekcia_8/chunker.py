"""
chunker.py
Sémantické chunkovanie kódu po objektoch:
  - 1 stored procedure = 1 chunk
  - 1 table DDL = 1 chunk
  - 1 Python funkcia/trieda = 1 chunk
  - 1 Python modul (ak malý) = 1 chunk

Každý chunk má bohaté metadáta pre retrieval.
"""

import ast
import re
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from pathlib import Path

from pl_file_parser import PLFileParser, SQLObject


@dataclass
class Chunk:
    """Jeden sémantický chunk pre indexovanie."""
    chunk_id: str           # jedinečné ID: "filepath::object_name"
    content: str            # plný kód objektu
    summary: str            # stručný popis pre embedding
    obj_type: str           # procedure, table, function, class, module, view, ...
    language: str           # sql, python, perl
    name: str               # meno objektu
    source_file: str        # relatívna cesta
    line_start: int
    line_end: int
    dependencies: List[str] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    columns: List[Dict] = field(default_factory=list)
    docstring: str = ""
    module: str = ""        # Python modul/package
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        d = asdict(self)
        # ChromaDB metadata musí byť flat strings/ints
        d['dependencies'] = ','.join(self.dependencies)
        d['parameters'] = ','.join(self.parameters)
        d['columns'] = str(self.columns)
        return d

    @property
    def embed_text(self) -> str:
        """Text, ktorý sa použije na embedding - summary + kód."""
        parts = [
            f"[{self.language.upper()} {self.obj_type.upper()}] {self.name}",
        ]
        if self.metadata.get('package_name'):
            parts.append(f"Package: {self.metadata['package_name']}")
        if self.docstring:
            parts.append(f"Description: {self.docstring[:300]}")
        if self.parameters:
            parts.append(f"Parameters: {', '.join(self.parameters[:10])}")
        if self.dependencies:
            parts.append(f"Uses: {', '.join(self.dependencies[:10])}")
        parts.append("")
        parts.append(self.content[:4000])  # limit pre embedding
        return '\n'.join(parts)


class SemanticChunker:
    """
    Sémantický chunker - parsuje súbory a vytvára object-level chunky.
    Podporuje: Python (.py), SQL (.sql), Perl/SQL (.pl), SQL DDL (.ddl)
    """

    def __init__(self, repo_root: str = '.'):
        self.repo_root = Path(repo_root).resolve()
        self.pl_parser = PLFileParser()

    def _extract_package_name(self, filepath: Path) -> str:
        """Extrahuje meno fkinstall balíka z cesty (prvý adresár pod repo root)."""
        try:
            rel = filepath.relative_to(self.repo_root)
            return rel.parts[0] if rel.parts else ""
        except ValueError:
            return ""

    def chunk_repository(self, paths: List[str] = None,
                         extensions: List[str] = None) -> List[Chunk]:
        """Nájde a chunkuje všetky relevantné súbory v repozitári."""
        if extensions is None:
            extensions = ['.py', '.pl', '.sql', '.ddl', '.sp']

        all_chunks = []
        search_roots = [self.repo_root] if paths is None else [Path(p) for p in paths]

        SKIP_DIRS = {'venv', '__pycache__', '.git', 'node_modules', '.tox',
                     'chroma_db', '.venv', 'dist', 'build'}

        # Najprv zozbieraj všetky súbory
        all_files = []
        for root in search_roots:
            for ext in extensions:
                for filepath in sorted(root.rglob(f'*{ext}')):
                    if any(p in SKIP_DIRS for p in filepath.parts):
                        continue
                    all_files.append(filepath)

        total_files = len(all_files)
        print(f"[chunker] Nájdených {total_files} súborov na spracovanie...", flush=True)

        errors = 0
        for idx, filepath in enumerate(all_files, 1):
            # Progress každých 50 súborov alebo pri poslednom
            if idx % 50 == 0 or idx == total_files:
                pct = int(100 * idx / total_files)
                print(f"[chunker] [{pct:3d}%] {idx}/{total_files} súborov"
                      f"  chunks={len(all_chunks)}  err={errors}", flush=True)
            try:
                chunks = self.chunk_file(str(filepath))
                all_chunks.extend(chunks)
            except Exception as e:
                errors += 1
                print(f"[WARN] {filepath}: {e}")

        print(f"[chunker] Hotovo: {len(all_chunks)} chunkov z {total_files} súborov"
              f"  (chyby: {errors})")
        return all_chunks

    def chunk_file(self, filepath: str) -> List[Chunk]:
        """Chunkuje jeden súbor podľa jeho typu."""
        filepath = Path(filepath)
        ext = filepath.suffix.lower()

        if ext == '.py':
            return self._chunk_python(filepath)
        elif ext in ('.pl', '.sql', '.ddl', '.sp'):
            return self._chunk_sql_or_perl(filepath)
        else:
            return self._chunk_as_whole(filepath)

    # ------------------------------------------------------------------
    # Python chunking
    # ------------------------------------------------------------------

    def _chunk_python(self, filepath: Path) -> List[Chunk]:
        """Chunkuje Python súbor: každá funkcia/trieda = 1 chunk."""
        chunks = []
        rel_path = self._rel_path(filepath)

        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            source = f.read()

        lines = source.splitlines()

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            # Fallback: celý súbor ako jeden chunk
            return [self._make_whole_chunk(source, rel_path, 'python', 'module',
                                           filepath.stem)]

        module_docstring = ast.get_docstring(tree) or ""
        module_name = filepath.stem

        extracted = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Iba top-level funkcie (nie metódy tried)
                if self._is_top_level(tree, node):
                    extracted.append(('function', node))
            elif isinstance(node, ast.ClassDef):
                extracted.append(('class', node))

        if not extracted:
            # Malý modul - celý ako 1 chunk
            chunks.append(self._make_whole_chunk(
                source, rel_path, 'python', 'module', module_name,
                docstring=module_docstring
            ))
            return chunks

        used_lines = set()

        for obj_type, node in extracted:
            line_start = node.lineno
            line_end = node.end_lineno
            used_lines.update(range(line_start, line_end + 1))

            obj_source = '\n'.join(lines[line_start - 1:line_end])
            docstring = ast.get_docstring(node) or ""
            name = node.name

            # Extrahuj parametre
            params = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = [
                    arg.arg for arg in node.args.args
                    if arg.arg != 'self'
                ]

            # Extrahuj závislosti (volania funkcií)
            deps = self._extract_python_calls(node, name)

            chunk_id = f"{rel_path}::{name}"
            summary = self._make_python_summary(obj_type, name, docstring, params)

            chunks.append(Chunk(
                chunk_id=chunk_id,
                content=obj_source,
                summary=summary,
                obj_type=obj_type,
                language='python',
                name=name,
                source_file=rel_path,
                line_start=line_start,
                line_end=line_end,
                dependencies=deps,
                parameters=params,
                docstring=docstring,
                module=module_name,
                metadata={
                    'module_docstring': module_docstring[:200],
                    'package_name': self._extract_package_name(filepath),
                },
            ))

        # Imports a module-level kód ako kontext chunk
        module_lines = [
            l for i, l in enumerate(lines, 1)
            if i not in used_lines
        ]
        module_code = '\n'.join(module_lines).strip()
        if module_code and len(module_code) > 20:
            chunks.append(Chunk(
                chunk_id=f"{rel_path}::__module__",
                content=module_code[:3000],
                summary=f"Module {module_name} - imports and module-level code",
                obj_type='module',
                language='python',
                name=f"{module_name}.__module__",
                source_file=rel_path,
                line_start=1,
                line_end=len(lines),
                module=module_name,
                docstring=module_docstring,
            ))

        return chunks

    def _is_top_level(self, tree: ast.Module, node: ast.AST) -> bool:
        """Skontroluje či funkcia je top-level (nie metóda triedy)."""
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef):
                if node in ast.walk(parent) and node is not parent:
                    return False
        return True

    def _extract_python_calls(self, node: ast.AST, self_name: str) -> List[str]:
        """Extrahuje volania funkcií/metód z AST nodu."""
        calls = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    if func.id != self_name:
                        calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.add(func.attr)
        return sorted(list(calls))[:20]

    def _make_python_summary(self, obj_type: str, name: str,
                              docstring: str, params: List[str]) -> str:
        """Vytvorí stručný popis Python objektu."""
        parts = [f"Python {obj_type} `{name}`"]
        if params:
            parts.append(f"Parameters: {', '.join(params[:5])}")
        if docstring:
            first_line = docstring.split('\n')[0].strip()
            parts.append(f"Description: {first_line[:150]}")
        return ' | '.join(parts)

    # ------------------------------------------------------------------
    # SQL / Perl chunking
    # ------------------------------------------------------------------

    def _chunk_sql_or_perl(self, filepath: Path) -> List[Chunk]:
        """Chunkuje PL/SQL súbor pomocou pl_file_parser."""
        rel_path = self._rel_path(filepath)
        objects = self.pl_parser.parse_file(str(filepath))

        if not objects:
            # Fallback: celý súbor
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                source = f.read()
            lang = 'sql' if filepath.suffix in ('.sql', '.ddl') else 'perl'
            return [self._make_whole_chunk(source, rel_path, lang, 'script',
                                           filepath.stem)]

        chunks = []
        pkg_name = self._extract_package_name(filepath)
        for obj in objects:
            lang = 'sql' if filepath.suffix in ('.sql', '.ddl', '.sp') else 'perl/sql'
            chunk_id = f"{rel_path}::{obj.name}"
            summary = self._make_sql_summary(obj)

            metadata = obj.metadata.copy() if obj.metadata else {}
            metadata['package_name'] = pkg_name

            chunks.append(Chunk(
                chunk_id=chunk_id,
                content=obj.sql_body,
                summary=summary,
                obj_type=obj.obj_type,
                language=lang,
                name=obj.name,
                source_file=rel_path,
                line_start=obj.line_start,
                line_end=obj.line_end,
                dependencies=obj.dependencies,
                parameters=obj.parameters,
                columns=obj.columns,
                module=filepath.stem,
                metadata=metadata,
            ))

        return chunks

    def _make_sql_summary(self, obj: SQLObject) -> str:
        """Vytvorí stručný popis SQL objektu."""
        parts = [f"SQL {obj.obj_type.upper()} `{obj.name}`"]
        if obj.parameters:
            parts.append(f"Parameters: {', '.join(obj.parameters[:5])}")
        if obj.columns:
            col_names = [c['name'] for c in obj.columns[:5]]
            parts.append(f"Columns: {', '.join(col_names)}")
        if obj.dependencies:
            parts.append(f"Uses: {', '.join(obj.dependencies[:5])}")
        return ' | '.join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_whole_chunk(self, source: str, rel_path: str, language: str,
                           obj_type: str, name: str, docstring: str = "") -> Chunk:
        """Celý súbor ako jeden chunk (fallback)."""
        lines = source.splitlines()
        return Chunk(
            chunk_id=f"{rel_path}::__all__",
            content=source[:6000],
            summary=f"{language.upper()} {obj_type} {name}",
            obj_type=obj_type,
            language=language,
            name=name,
            source_file=rel_path,
            line_start=1,
            line_end=len(lines),
            docstring=docstring,
            module=name,
        )

    def _chunk_as_whole(self, filepath: Path) -> List[Chunk]:
        """Neznámy typ - celý súbor ako jeden chunk."""
        rel_path = self._rel_path(filepath)
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            source = f.read()
        return [self._make_whole_chunk(source, rel_path, 'text', 'file', filepath.name)]

    def _rel_path(self, filepath: Path) -> str:
        """Vráti relatívnu cestu voči repo rootu."""
        try:
            return str(filepath.relative_to(self.repo_root))
        except ValueError:
            return str(filepath)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    import json

    repo = sys.argv[1] if len(sys.argv) > 1 else '.'
    chunker = SemanticChunker(repo_root=repo)
    chunks = chunker.chunk_repository()

    print(f"\nCelkom chunkov: {len(chunks)}")

    by_type = {}
    by_lang = {}
    for c in chunks:
        by_type[c.obj_type] = by_type.get(c.obj_type, 0) + 1
        by_lang[c.language] = by_lang.get(c.language, 0) + 1

    print("\nPodľa typu:")
    for t, n in sorted(by_type.items()):
        print(f"  {t}: {n}")

    print("\nPodľa jazyka:")
    for l, n in sorted(by_lang.items()):
        print(f"  {l}: {n}")

    if '--show' in sys.argv:
        for c in chunks[:3]:
            print(f"\n{'='*60}")
            print(f"ID: {c.chunk_id}")
            print(f"Summary: {c.summary}")
            print(f"Content (first 300 chars):\n{c.content[:300]}")
