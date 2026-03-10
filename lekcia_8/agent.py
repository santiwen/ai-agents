"""
agent.py
ReAct-style agent pre AI code asistenta.
NePouíva bind_tools() — priame volanie LLM s ReAct promptom.

Nástroje:
  - search_codebase      : vector search v ChromaDB
  - get_file_content     : git show HEAD:path
  - get_file_content_direct : find file on filesystem, return content
  - get_git_history      : git log --follow
  - get_object_deps      : dependency graph lookup
  - search_in_git        : git grep
  - get_recent_changes   : posledné git zmeny
  - propose_change       : generuje diff, čaká na schválenie

LLM: Qwen2.5-Coder-32B-Instruct cez Ollama (bez bind_tools)
"""

import json
import os
import re
import requests
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime

from indexer import CodeIndexer
from retriever import CodeRetriever
from git_tools import GitTools
from fkinstall_registry import FkinstallRegistry


# ---------------------------------------------------------------------------
# System prompts - adaptívne podľa typu úlohy
# ---------------------------------------------------------------------------

_TOOL_CALL_RULES = """
CRITICAL RULES for tool usage:
- To call a tool, output ONLY the tool call XML tag with JSON inside. Example:
  <tool_call>{"name":"search_codebase","arguments":{"query":"example"}}</tool_call>
- Output ONE tool call per response. STOP immediately after the closing tag. Do NOT write anything after it.
- NEVER guess, predict, or assume what a tool will return. WAIT for the actual result.
- NEVER invent example code, file contents, file paths, or table names. Only use real data from tool results.
- If you need information, call a tool. Do not speculate.
- If a tool returns no results, try a DIFFERENT tool. Do NOT repeat the same tool with similar arguments.
  Strategy: locate_object (if you know object name) → search_codebase (semantic) → search_in_git (exact text) → get_object_deps (dependencies) → get_file_content_direct (filesystem).
- When you have enough information from tools, write your final answer WITHOUT any tool call.
- Format tool results into a clear, readable answer. Do not dump raw grep output.
- IMPORTANT: When the user asks for a list/zoznam (e.g. "which procedures use table X"), return the COMPLETE list of ALL results — every file path from search_in_git output. Do NOT summarize, do NOT skip items, do NOT say "and others". List ALL matching files with their full paths.
- For search queries, prefer search_in_git with a simple pattern (e.g. just the table name) to get ALL references, not overly specific regex patterns."""

_DOMAIN_KNOWLEDGE = """Repository: 278 fkinstall packages. SQL procedures in PackageName/server/src/components/trm/share/sybase/procedures/*.pl, tables in tables/*.pl.
Use locate_object(name) to find which package owns a procedure/table. Use get_package_info(pkg) for package details."""

SYSTEM_PROMPT_ANALYZE = """You are an AI code assistant for Sybase SQL, Python 2.7, and Perl wrappers (.pl).

Available tools:
{tools_description}
{tool_call_rules}
{domain_knowledge}
Additional rules: Use Sybase syntax, not PostgreSQL. Focus on clear explanations with code references.
Use tools to find relevant code before answering. Show dependencies and relationships.
Respond in the same language as the question."""

SYSTEM_PROMPT_CHANGE = """You are an AI code assistant for Sybase SQL, Python 2.7, and Perl wrappers (.pl).

Available tools:
{tools_description}
{tool_call_rules}
{domain_knowledge}
Additional rules: Use Sybase syntax, not PostgreSQL. Changes only via propose_change. Wait for approval.
Before proposing any change:
1. Read the current file content
2. Check dependencies with get_object_deps to understand impact
3. Only then propose the change with full file content
Generate precise, minimal changes. Preserve existing code style.
Respond in the same language as the question."""

SYSTEM_PROMPT_SEARCH = """You are an AI code assistant for Sybase SQL, Python 2.7, and Perl wrappers (.pl).

Available tools:
{tools_description}
{tool_call_rules}
{domain_knowledge}
Additional rules: Use Sybase syntax, not PostgreSQL. Be concise and direct.
Use the most efficient tool for the task. Prefer search_in_git for exact matches,
search_codebase for semantic search, get_object_deps for relationships.
Respond in the same language as the question."""

TOOL_DESCRIPTIONS = {
    "search_codebase": "search_codebase(query, n_results=5, language=None, obj_type=None) - semantic search in code",
    "get_file_content": "get_file_content(filepath, ref='HEAD') - file content from git",
    "get_git_history": "get_git_history(filepath, max_commits=10) - git history",
    "get_object_deps": "get_object_deps(object_name, depth=2) - dependency graph",
    "search_in_git": "search_in_git(pattern, file_pattern=None) - git grep",
    "get_recent_changes": "get_recent_changes(days=7) - recent git changes",
    "propose_change": "propose_change(filepath, new_content, reason) - propose a single-file change, WAITS for approval",
    "propose_changeset": 'propose_changeset(changes, reason) - propose multi-file change. changes = JSON: [{"filepath":"...","new_content":"...","reason":"..."}]',
    "get_file_content_direct": "get_file_content_direct(filename, start_line=1, max_lines=100) - find file on filesystem and return content with pagination (no git needed)",
    "get_package_info": "get_package_info(package_name) - fkinstall package info: procedures, tables, requirements, description",
    "locate_object": "locate_object(object_name) - find which fkinstall package contains a procedure/table and return its path",
}

# Keyword-based task type detection
CHANGE_KEYWORDS = ['zmeň', 'zmen', 'oprav', 'pridaj', 'uprav', 'vymaž', 'vymaz',
                    'refaktor', 'optimalizuj', 'prepíš', 'prepis', 'migr',
                    'change', 'fix', 'add', 'modify', 'update', 'delete', 'remove',
                    'create', 'implement', 'refactor', 'optimize']
SEARCH_KEYWORDS = ['nájdi', 'najdi', 'kde sa', 'kde je', 'kto volá', 'kto vola',
                    'koľko', 'kolko', 'zoznam', 'list',
                    'find', 'where', 'who calls', 'how many', 'show me', 'list']


def detect_task_type(message: str) -> str:
    """Detekuje typ úlohy z user správy (keyword matching, nie LLM)."""
    lower = message.lower()
    for kw in CHANGE_KEYWORDS:
        if kw in lower:
            return 'change'
    for kw in SEARCH_KEYWORDS:
        if kw in lower:
            return 'search'
    return 'analyze'


# ---------------------------------------------------------------------------
# Conversation Memory
# ---------------------------------------------------------------------------

class ConversationMemory:
    """Ukladá históriu správ per thread_id v JSON súboroch."""

    def __init__(self, storage_dir: str = "./conversations"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, List[Dict]] = {}

    def _path(self, thread_id: str) -> Path:
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', thread_id)
        return self.storage_dir / f"{safe_id}.json"

    def load(self, thread_id: str) -> List[Dict[str, str]]:
        """Načíta históriu správ pre thread_id."""
        if thread_id in self._cache:
            return self._cache[thread_id]

        path = self._path(thread_id)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    messages = json.load(f)
                self._cache[thread_id] = messages
                return messages
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def save(self, thread_id: str, messages: List[Dict[str, str]]):
        """Uloží históriu správ."""
        self._cache[thread_id] = messages
        path = self._path(thread_id)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(messages, f, ensure_ascii=False, indent=1)
        except IOError as e:
            print(f"[memory] Chyba ukladania konverzácie: {e}", flush=True)

    def get_sliding_window(self, thread_id: str, system_prompt: str,
                           max_messages: int = 20) -> List[Dict[str, str]]:
        """
        Vráti sliding window: system prompt + prvá user správa + posledných N správ.
        Zachová kontext bez preplnenia context window.
        """
        history = self.load(thread_id)
        if not history:
            return [{"role": "system", "content": system_prompt}]

        # Vždy system prompt
        result = [{"role": "system", "content": system_prompt}]

        # Filtruj len user/assistant správy (nie system)
        conv_messages = [m for m in history if m['role'] in ('user', 'assistant')]

        if len(conv_messages) <= max_messages:
            result.extend(conv_messages)
        else:
            # Prvá user správa (kontext) + posledných N
            if conv_messages and conv_messages[0]['role'] == 'user':
                result.append(conv_messages[0])
            result.extend(conv_messages[-max_messages:])

        return result

    def append(self, thread_id: str, role: str, content: str):
        """Pridá správu do histórie."""
        history = self.load(thread_id)
        history.append({"role": role, "content": content})
        self.save(thread_id, history)

    def clear(self, thread_id: str):
        """Vymaže históriu pre thread_id."""
        self._cache.pop(thread_id, None)
        path = self._path(thread_id)
        if path.exists():
            path.unlink()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

class Tools:
    """Obal pre všetky nástroje agenta."""

    def __init__(self, retriever: CodeRetriever, git_tools: GitTools,
                 registry: FkinstallRegistry = None):
        self.retriever = retriever
        self.git_tools = git_tools
        self.registry = registry

    def search_codebase(self, query: str, n_results: int = 5,
                        language: str = None, obj_type: str = None) -> str:
        results = self.retriever.search(
            query=query,
            n_results=n_results,
            filter_language=language,
            filter_type=obj_type,
            expand_deps=True,
            dep_depth=2,
        )
        return self.retriever.format_results(results, show_content=True, max_content_chars=800)

    def get_file_content(self, filepath: str, ref: str = 'HEAD') -> str:
        content = self.git_tools.get_file_content(filepath, ref)
        if not content:
            return f"Súbor '{filepath}' nenájdený v ref={ref}"
        lines = content.splitlines()
        return f"=== {filepath} ({ref}) === {len(lines)} riadkov ===\n{content}"

    def get_git_history(self, filepath: str, max_commits: int = 10) -> str:
        commits = self.git_tools.get_git_history(filepath, max_commits)
        if not commits:
            return f"Žiadna história pre '{filepath}'"
        lines = [f"=== Git história: {filepath} ({len(commits)} commitov) ==="]
        for c in commits:
            lines.append(f"\n[{c.sha}] {c.date} - {c.author}")
            lines.append(f"  {c.message}")
            if c.files_changed:
                lines.append(f"  Súbory: {', '.join(c.files_changed[:3])}")
        return '\n'.join(lines)

    def get_object_deps(self, object_name: str, depth: int = 2) -> str:
        graph = self.retriever.dep_graph
        deps = graph.get_deps(object_name, depth=depth)
        dependents = graph.get_dependents(object_name, depth=1)
        impact = graph.get_impact(object_name)

        lines = [f"=== Dependency Graph: {object_name} ==="]
        if deps:
            lines.append(f"\nZávislosti ({len(deps)}) - čo {object_name} volá:")
            for d in deps[:15]:
                node = graph.nodes.get(d)
                type_str = node.obj_type if node else '?'
                lines.append(f"  → [{type_str}] {d}")
        else:
            lines.append(f"\nŽiadne závislosti.")

        if dependents:
            lines.append(f"\nDependenti ({len(dependents)}) - kto volá {object_name}:")
            for d in dependents[:10]:
                node = graph.nodes.get(d)
                type_str = node.obj_type if node else '?'
                lines.append(f"  ← [{type_str}] {d}")
        else:
            lines.append(f"\nNikto iný {object_name} nevolá.")

        lines.append(f"\nDopad zmeny: {impact['total_affected']} objektov ovplyvnených")
        return '\n'.join(lines)

    def search_in_git(self, pattern: str, file_pattern: str = None) -> str:
        results = self.git_tools.search_in_git(pattern, file_pattern)
        if not results:
            return f"Nič nenájdené pre '{pattern}'"

        # Zoznam unikátnych súborov (kompletný, netruncovaný)
        unique_files = list(dict.fromkeys(r['file'] for r in results))
        lines = [f"=== Výsledky git grep: '{pattern}' ==="]
        lines.append(f"Nájdené v {len(unique_files)} súboroch ({len(results)} riadkov):")
        lines.append("")
        lines.append("SÚBORY:")
        for f in unique_files:
            lines.append(f"  {f}")
        lines.append("")

        # Detail: prvých 30 výsledkov s kontextom
        lines.append("DETAIL (prvé výskyty):")
        for r in results[:30]:
            lines.append(f"  {r['file']}:{r['line']}  {r['content']}")
        if len(results) > 30:
            lines.append(f"  ... a {len(results)-30} ďalších riadkov")
        return '\n'.join(lines)

    def get_recent_changes(self, days: int = 7, file_pattern: str = None) -> str:
        import fnmatch
        changes = self.git_tools.get_recent_changes(days)
        if not changes:
            return f"Žiadne zmeny za posledných {days} dní."
        lines = [f"=== Zmeny za posledných {days} dní ({len(changes)} commitov) ==="]
        for c in changes[:10]:
            lines.append(f"\n[{c['sha']}] {c['date']}: {c['message']}")
            files = c['files']
            if file_pattern:
                files = [f for f in files if fnmatch.fnmatch(f, file_pattern)]
            for f in files[:5]:
                lines.append(f"  {f}")
            if len(files) > 5:
                lines.append(f"  ... a {len(files)-5} ďalších")
        return '\n'.join(lines)

    def get_file_content_direct(self, filename: str, start_line: int = 1, max_lines: int = 100) -> str:
        """Nájde súbor na filesystéme podľa mena a vráti jeho obsah s pagináciou."""
        repo = self.git_tools.repo_path
        matches = []
        for root, dirs, files in os.walk(repo):
            for f in files:
                if f == filename or f == os.path.basename(filename):
                    matches.append(os.path.join(root, f))
            if len(matches) > 20:
                break

        # Ak filename obsahuje cestu, skús aj priamy prístup
        if not matches:
            direct = os.path.join(repo, filename)
            if os.path.isfile(direct):
                matches.append(direct)

        if not matches:
            return f"Súbor '{filename}' nenájdený v {repo}"

        if len(matches) > 1:
            # Ak je viac výsledkov, preferuj presný match cesty
            exact = [m for m in matches if m.endswith(os.sep + filename) or m.endswith(filename)]
            if len(exact) == 1:
                matches = exact
            else:
                listing = "\n".join(os.path.relpath(m, repo) for m in matches[:10])
                return f"Nájdených {len(matches)} súborov s menom '{filename}':\n{listing}\nPoužite presnejšiu cestu."

        filepath = matches[0]
        rel_path = os.path.relpath(filepath, repo)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
                all_lines = fh.readlines()
        except Exception as e:
            return f"Chyba pri čítaní '{rel_path}': {e}"

        total = len(all_lines)
        start_line = max(1, start_line)
        end_line = min(start_line + max_lines - 1, total)
        selected = all_lines[start_line - 1:end_line]
        content = "".join(selected).rstrip('\n')

        header = f"=== {rel_path} (filesystem) === riadky {start_line}-{end_line} z {total} ==="
        if end_line < total:
            header += f"\n[Pokračovanie: start_line={end_line + 1}]"
        return f"{header}\n{content}"

    def get_package_info(self, package_name: str) -> str:
        """Informácie o fkinstall balíku."""
        if not self.registry:
            return "[CHYBA] Registry nie je dostupný"
        return self.registry.format_package_info(package_name)

    def locate_object(self, object_name: str) -> str:
        """Nájde balík a cestu k procedúre/tabuľke."""
        if not self.registry:
            return "[CHYBA] Registry nie je dostupný"

        pkg_name = self.registry.find_package_for_object(object_name)
        if not pkg_name:
            return f"Objekt '{object_name}' nenájdený v žiadnom balíku."

        info = self.registry.get_package_info(pkg_name)
        if not info:
            return f"Balík '{pkg_name}' nenájdený."

        # Určí typ a cestu
        server_dir = info.server_dir or 'server'
        if object_name in info.procedures:
            obj_type = "procedure"
            rel_path = f"{pkg_name}/{server_dir}/src/components/trm/share/sybase/procedures/{object_name}.pl"
        elif object_name in info.tables:
            obj_type = "table"
            rel_path = f"{pkg_name}/{server_dir}/src/components/trm/share/sybase/tables/{object_name}.pl"
        else:
            obj_type = "object"
            rel_path = f"{pkg_name}/ (presná cesta neznáma)"

        lines = [
            f"=== {obj_type.upper()}: {object_name} ===",
            f"Balík: {pkg_name}",
            f"Verzia: {info.version}",
            f"Autor: {info.author}",
            f"Cesta: {rel_path}",
        ]

        # Skontroluj či súbor existuje
        full_path = os.path.join(self.git_tools.repo_path, rel_path)
        if os.path.isfile(full_path):
            lines.append(f"Súbor existuje: áno")
        else:
            # Skús nájsť alternatívnu cestu
            alt_path = None
            for root, dirs, files in os.walk(os.path.join(self.git_tools.repo_path, pkg_name)):
                if f"{object_name}.pl" in files:
                    alt_path = os.path.relpath(os.path.join(root, f"{object_name}.pl"),
                                               self.git_tools.repo_path)
                    break
            if alt_path:
                lines.append(f"Skutočná cesta: {alt_path}")
            else:
                lines.append(f"Súbor existuje: nie (možno ešte neinštalovaný)")

        return "\n".join(lines)

    def propose_change(self, filepath: str, new_content: str, reason: str) -> str:
        # Self-verifikácia pred návrhom
        verification = self._verify_content(filepath, new_content)

        proposal = self.git_tools.propose_change(filepath, new_content, reason)
        lines = [
            "╔══════════════════════════════════════════╗",
            "║   NAVRHOVANÁ ZMENA - ČAKÁ NA SCHVÁLENIE  ║",
            "╚══════════════════════════════════════════╝",
            "",
            f"Súbor: {proposal['filepath']}",
            f"Dôvod: {proposal['reason']}",
            f"Zmeny: +{proposal['additions']} riadkov, -{proposal['deletions']} riadkov",
        ]

        # Pridaj verifikáciu
        if verification['errors']:
            lines.append("")
            lines.append("⚠ VERIFIKAČNÉ CHYBY:")
            for err in verification['errors']:
                lines.append(f"  ✗ {err}")

        if verification['warnings']:
            lines.append("")
            lines.append("⚡ VAROVANIA:")
            for warn in verification['warnings']:
                lines.append(f"  ! {warn}")

        lines.extend([
            "",
            "=== DIFF ===",
            proposal['patch'][:3000] if proposal['patch'] else "(žiadne zmeny)",
            "",
            "Napíš 'schválim' alebo 'áno' pre aplikovanie zmeny.",
            "Napíš 'zamietam' alebo 'nie' pre zrušenie.",
        ])
        return '\n'.join(lines)

    def propose_changeset(self, changes: str, reason: str) -> str:
        """Multi-file zmena. changes = JSON string: [{"filepath":"...", "new_content":"...", "reason":"..."}]"""
        try:
            change_list = json.loads(changes) if isinstance(changes, str) else changes
        except json.JSONDecodeError:
            return "[CHYBA] Neplatný JSON pre changes parameter"

        # Verifikuj každý súbor
        all_errors = []
        for change in change_list:
            v = self._verify_content(change['filepath'], change['new_content'])
            if v['errors']:
                all_errors.extend([f"{change['filepath']}: {e}" for e in v['errors']])

        changeset = self.git_tools.propose_changeset(change_list, reason)

        lines = [
            "╔══════════════════════════════════════════════╗",
            "║   MULTI-FILE ZMENA - ČAKÁ NA SCHVÁLENIE      ║",
            "╚══════════════════════════════════════════════╝",
            "",
            f"Dôvod: {changeset['reason']}",
            f"Súbory ({len(changeset['files'])}):",
        ]
        for fp in changeset['files']:
            lines.append(f"  • {fp}")
        lines.append(f"Zmeny: +{changeset['additions']} riadkov, -{changeset['deletions']} riadkov")

        if all_errors:
            lines.append("\n⚠ VERIFIKAČNÉ CHYBY:")
            for err in all_errors:
                lines.append(f"  ✗ {err}")

        lines.extend([
            "",
            "=== DIFF ===",
            changeset['patch'][:5000] if changeset['patch'] else "(žiadne zmeny)",
            "",
            "Napíš 'schválim' alebo 'áno' pre aplikovanie zmien.",
        ])
        return '\n'.join(lines)

    def _verify_content(self, filepath: str, content: str) -> Dict[str, List[str]]:
        """Self-verifikácia obsahu pred návrhom zmeny."""
        errors = []
        warnings = []

        if filepath.endswith('.py'):
            # Python syntax check
            try:
                import ast
                ast.parse(content)
            except SyntaxError as e:
                errors.append(f"Python syntax error riadok {e.lineno}: {e.msg}")

        elif filepath.endswith('.sql') or filepath.endswith('.pl'):
            # SQL syntax check cez sqlglot (ak dostupný)
            try:
                import sqlglot
                # Extrahuj SQL bloky (medzi GO delimiters)
                sql_blocks = re.split(r'\bGO\b', content, flags=re.IGNORECASE)
                for i, block in enumerate(sql_blocks):
                    block = block.strip()
                    if not block or block.startswith('#') or block.startswith('--'):
                        continue
                    # Preskočí Perl wrapping, parsuj len SQL
                    sql_match = re.search(r'(?:<<SQL|qq\{)(.*?)(?:SQL\b|\})', block, re.DOTALL)
                    sql_to_check = sql_match.group(1) if sql_match else block
                    if sql_to_check.strip():
                        try:
                            sqlglot.parse(sql_to_check, dialect='tsql')
                        except sqlglot.errors.ParseError as e:
                            warnings.append(f"SQL parse warning blok {i+1}: {str(e)[:100]}")
            except ImportError:
                pass  # sqlglot nie je nainštalovaný

        # Dependency check - overiť referované objekty
        if self.retriever and self.retriever.dep_graph:
            graph = self.retriever.dep_graph
            # Hľadaj EXEC/CALL referencie
            refs = re.findall(r'(?:EXEC|EXECUTE|CALL)\s+(\w+)', content, re.IGNORECASE)
            for ref in refs:
                if ref.lower() not in ('sp_', 'xp_') and ref not in graph.nodes:
                    warnings.append(f"Referencia na '{ref}' nenájdená v dependency grafe")

        return {'errors': errors, 'warnings': warnings}

    def call(self, name: str, arguments: dict) -> str:
        """Volá nástroj podľa mena s argumentmi."""
        fn = getattr(self, name, None)
        if fn is None:
            return f"[CHYBA] Neznámy nástroj: {name}"
        try:
            return fn(**arguments)
        except Exception as e:
            return f"[CHYBA] Nástroj {name} zlyhal: {e}"


# ---------------------------------------------------------------------------
# ReAct Agent
# ---------------------------------------------------------------------------

class CodeAssistantAgent:
    """
    ReAct-style agent pre AI code asistenta.
    Nepoužíva bind_tools() — priame LLM volanie s tool descriptions v systémovom prompte.
    Podporuje konverzačnú pamäť, adaptívne system prompty a plánovací krok.
    """

    def __init__(self,
                 repo_path: str,
                 chroma_path: str = "./chroma_db",
                 ollama_url: str = "http://localhost:11434",
                 llm_model: str = "qwen2.5-coder:32b-instruct-q4_K_M",
                 embed_model: str = "nomic-embed-text",
                 conversation_dir: str = "./conversations"):

        self.repo_path = repo_path

        # Indexer + Retriever
        self.indexer = CodeIndexer(
            chroma_path=chroma_path,
            ollama_url=ollama_url,
            embed_model=embed_model,
        )
        self.retriever = CodeRetriever(self.indexer)

        # Git nástroje
        self.git_tools = GitTools(repo_path)

        # Fkinstall registry
        self.registry = FkinstallRegistry(repo_path)
        registry_path = Path(chroma_path) / "fkinstall_registry.json"
        if registry_path.exists():
            self.registry.load(str(registry_path))
        else:
            self.registry.build()
            self.registry.save(str(registry_path))

        # Tools
        self.tools = Tools(self.retriever, self.git_tools, self.registry)

        # LLM config — priame requests volanie (stabilnejšie ako langchain_ollama
        # pre 32B model na RTX 4090 24GB)
        self.llm_model = llm_model
        self.ollama_url = ollama_url

        # Adaptívne system prompty
        tools_desc = "\n".join(TOOL_DESCRIPTIONS.values())
        fmt = {"tools_description": tools_desc, "tool_call_rules": _TOOL_CALL_RULES,
               "domain_knowledge": _DOMAIN_KNOWLEDGE}
        self.system_prompts = {
            'analyze': SYSTEM_PROMPT_ANALYZE.format(**fmt),
            'change': SYSTEM_PROMPT_CHANGE.format(**fmt),
            'search': SYSTEM_PROMPT_SEARCH.format(**fmt),
        }
        # Default pre warmup
        self.system_prompt = self.system_prompts['analyze']

        # Konverzačná pamäť
        self.memory = ConversationMemory(storage_dir=conversation_dir)

        # Pending proposal pre approval workflow
        self.pending_proposal: Optional[Dict] = None

        self._llm_warm = False

    def _ensure_llm_loaded(self):
        """Zaistí že LLM model je načítaný v GPU s plným kontextom."""
        if self._llm_warm:
            return
        import time
        print("[agent] Loading LLM into GPU (20GB, môže trvať 10-30s)...", flush=True)

        # Warm up s presne rovnakým system promptom ako reálne volanie
        # Zmena system promptu medzi volaniami crashuje Ollama runner
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": self.llm_model,
                        "messages": [
                            {"role": "system", "content": self.system_prompt},
                            {"role": "user", "content": "hi"},
                        ],
                        "stream": False,
                        "options": {
                            "num_ctx": 16384,
                            "num_predict": 1,
                            "num_batch": 64,
                            "temperature": 0.3,
                            "top_k": 40,
                            "top_p": 0.9,
                            "repeat_penalty": 1.15,
                            "repeat_last_n": 256,
                        },
                    },
                    timeout=180,
                )
                if r.status_code == 200:
                    break
                print(f"[agent] LLM warmup attempt {attempt+1} HTTP {r.status_code}", flush=True)
            except Exception as e:
                print(f"[agent] LLM warmup attempt {attempt+1}: {e}", flush=True)
            time.sleep(10)

        self._llm_warm = True
        print("[agent] LLM ready.", flush=True)

    def _call_llm(self, messages: List[Dict[str, str]],
                  temperature: float = 0.3) -> str:
        """Volá Ollama chat API priamo cez requests (stabilnejšie pre 32B model)."""
        import time
        self._ensure_llm_loaded()

        payload = {
            "model": self.llm_model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": 16384,
                "num_predict": 2048,
                "num_batch": 64,
                "temperature": temperature,
                "top_k": 40,
                "top_p": 0.9,
                "repeat_penalty": 1.15,
                "repeat_last_n": 256,
            },
        }

        for attempt in range(3):
            try:
                r = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                    timeout=300,
                )
                if r.status_code == 200:
                    data = r.json()
                    return data.get("message", {}).get("content", "")
                else:
                    print(f"[agent] LLM HTTP {r.status_code}: {r.text[:200]}", flush=True)
            except Exception as e:
                print(f"[agent] LLM call attempt {attempt+1} error: {e}", flush=True)

            # Model crashed, needs reload
            print(f"[agent] LLM call failed (attempt {attempt+1}), reloading...", flush=True)
            self._llm_warm = False
            time.sleep(10)
            self._ensure_llm_loaded()

        raise RuntimeError("LLM nedostupný po 3 pokusoch")

    def _call_llm_stream(self, messages: List[Dict[str, str]],
                          temperature: float = 0.3, callback=None) -> str:
        """Streaming LLM volanie - zobrazuje tokeny priebežne."""
        import time
        self._ensure_llm_loaded()

        payload = {
            "model": self.llm_model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_ctx": 16384,
                "num_predict": 2048,
                "num_batch": 64,
                "temperature": temperature,
                "top_k": 40,
                "top_p": 0.9,
                "repeat_penalty": 1.15,
                "repeat_last_n": 256,
            },
        }

        for attempt in range(3):
            try:
                r = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                    timeout=300,
                    stream=True,
                )
                if r.status_code == 200:
                    full_text = []
                    for line in r.iter_lines():
                        if line:
                            try:
                                chunk = json.loads(line)
                                token = chunk.get("message", {}).get("content", "")
                                if token:
                                    full_text.append(token)
                                    if callback:
                                        callback(token)
                                if chunk.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue
                    return ''.join(full_text)
                else:
                    print(f"[agent] LLM HTTP {r.status_code}: {r.text[:200]}", flush=True)
            except Exception as e:
                print(f"[agent] LLM stream attempt {attempt+1} error: {e}", flush=True)

            self._llm_warm = False
            time.sleep(10)
            self._ensure_llm_loaded()

        raise RuntimeError("LLM nedostupný po 3 pokusoch")

    def chat_stream(self, message: str, thread_id: str = "default",
                     token_callback=None) -> str:
        """
        Streaming verzia chat() - zobrazuje tokeny priebežne.
        token_callback(token: str) sa volá pre každý vygenerovaný token.
        Používa smart buffer: prvých ~100 znakov sa bufferuje aby sa detekoval
        tool call. Ak to nie je tool call, buffer sa flushne a streaming pokračuje.
        """
        task_type = detect_task_type(message)
        system_prompt = self.system_prompts[task_type]
        temperature = 0.3 if task_type == 'change' else 0.7 if task_type == 'analyze' else 0.3

        messages = self.memory.get_sliding_window(
            thread_id, system_prompt, max_messages=20
        )
        messages.append({"role": "user", "content": message})
        self.memory.append(thread_id, "user", message)

        text = ""
        recent_tools = []  # sledovanie tool loop
        for iteration in range(15):
            # Streaming s buffered callback pre detekciu tool calls
            buffer = []
            buffer_flushed = [False]
            is_tool_call = [False]
            BUFFER_THRESHOLD = 80  # znakov pred rozhodnutím

            def _has_tool_pattern(text):
                """Detekuje tool call pattern v texte - všetky Qwen varianty."""
                # Vyčisti špeciálne tokeny
                cleaned = re.sub(r'<\|[^|]+\|>', '', text).strip()
                # Hlavný pattern: JSON s kľúčom "name" a hodnotou = známy tool
                # Pokrýva: {"name", {name", <tool_call>{"name, atď.
                if re.search(r'[{<]"?name"?\s*[":]\s*"?\w', cleaned):
                    return True
                if '<tool_call>' in text or '<tool>' in text or '[c]' in text or '<toolcall>' in text:
                    return True
                # Qwen wrappuje tool call do ```xml\n{... alebo ```json\n{...
                if re.search(r'```(?:xml|json)\s*\n\s*[{<]', cleaned):
                    return True
                return False

            def buffered_callback(token):
                if is_tool_call[0]:
                    return  # Potlač tool call výstup
                buffer.append(token)
                buffered_text = ''.join(buffer)

                if not buffer_flushed[0]:
                    if _has_tool_pattern(buffered_text):
                        is_tool_call[0] = True
                        return
                    # Flush buffer ak je dosť dlhý a nie je tool call
                    if len(buffered_text) >= BUFFER_THRESHOLD:
                        buffer_flushed[0] = True
                        if token_callback:
                            token_callback(buffered_text)
                else:
                    # Ak sa tool call objaví neskôr v streame (text + tool_call)
                    if _has_tool_pattern(buffered_text):
                        is_tool_call[0] = True
                        return
                    if token_callback:
                        token_callback(token)

            text = self._call_llm_stream(
                messages, temperature=temperature,
                callback=buffered_callback if token_callback else None
            )

            # Ak buffer nebol flushed (krátka odpoveď), flush teraz
            if not buffer_flushed[0] and not is_tool_call[0] and token_callback and buffer:
                token_callback(''.join(buffer))
                buffer_flushed[0] = True

            tool_call = self._parse_tool_call(text)

            if tool_call:
                tool_name, tool_args = tool_call
                recent_tools.append(tool_name)

                # Detekcia tool loop
                if len(recent_tools) >= 3 and len(set(recent_tools[-3:])) == 1:
                    other_tools = [t for t in TOOL_DESCRIPTIONS if t != tool_name]
                    hint = (f"[SYSTÉM] Nástroj {tool_name} bol volaný 3x za sebou bez úspechu. "
                            f"Skús iný nástroj: {', '.join(other_tools[:3])}")
                    messages.append({"role": "user", "content": hint})
                    recent_tools.clear()
                    continue

                clean_text = self._strip_after_tool_call(text)
                messages.append({"role": "assistant", "content": clean_text})

                if token_callback:
                    token_callback(f"[{tool_name}] ")

                tool_result = self.tools.call(tool_name, tool_args)

                if tool_name in ('propose_change', 'propose_changeset'):
                    self.pending_proposal = self.git_tools.propose_change(
                        tool_args.get('filepath', ''),
                        tool_args.get('new_content', ''),
                        tool_args.get('reason', ''),
                    ) if tool_name == 'propose_change' else None

                result_text = self._smart_truncate(tool_result, max_chars=8000)
                messages.append({
                    "role": "user",
                    "content": f"[Výsledok nástroja {tool_name}]\n{result_text}"
                })
            else:
                self.memory.append(thread_id, "assistant", text)
                return text

        self.memory.append(thread_id, "assistant", text)
        return text

    def _parse_tool_call(self, text: str) -> Optional[Tuple[str, dict]]:
        """
        Extrahuje tool call z textu.
        Podporuje formáty:
          - <tool_call>{"name": "...", "arguments": {...}}</tool_call>
          - Bare JSON: {"name": "...", "arguments": {...}}
        Handluje Qwen artefakty: <|im_start|>, dvojité zátvorky {{...}}
        """
        # Vyčisti Qwen špeciálne tokeny a artefakty
        cleaned = re.sub(r'<\|[^|]+\|>', '', text).strip()
        # Qwen niekedy generuje <{"name":...} — odstráň úvodný <
        cleaned = re.sub(r'<(\{"name")', r'\1', cleaned)

        # Qwen [c][...] wrapper: [c][{"name":"..."}]</c> alebo [c]{"name":"..."}[/c]
        cleaned = re.sub(r'\[/?c\]', '', cleaned)
        cleaned = re.sub(r'</c>', '', cleaned)

        # Qwen streaming artefakt: {name":"... namiesto {"name":"...
        # (chýba " pred name lebo je súčasťou <|im_start|> tokenu)
        cleaned = re.sub(r'\{name":', '{"name":', cleaned)
        cleaned = re.sub(r'\{arguments":', '{"arguments":', cleaned)

        # Oprav dvojité zátvorky {{ }} → { } (Qwen niekedy kopíruje z format stringu)
        # Ale len ak obsahujú "name" pattern (nie v bežnom texte)
        if '{{' in cleaned and '"name"' in cleaned:
            cleaned = cleaned.replace('{{', '{').replace('}}', '}')

        # Try <tool_call> or <tool> or <toolcall> wrapper — JSON alebo XML body
        for tag in ['tool_call', 'tool', 'toolcall']:
            match = re.search(rf'<{tag}>\s*(.*?)\s*</{tag}>', cleaned, re.DOTALL)
            if not match:
                match = re.search(rf'<{tag}>\s*(.*)', cleaned, re.DOTALL)
            if match:
                body = match.group(1).strip()

                # Variant A: JSON body {"name": "...", "arguments": {...}}
                json_match = re.search(r'\{.*\}', body, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(0))
                        name = data.get('name', '')
                        if name and name in TOOL_DESCRIPTIONS:
                            return (name, data.get('arguments', {}))
                    except json.JSONDecodeError:
                        pass

                # Variant B: XML body <name>tool</name><arguments><key>val</key></arguments>
                name_match = re.search(r'<name>\s*([^<]+?)\s*</name>', body)
                if name_match:
                    name = name_match.group(1).strip().strip('"\'')
                    if name in TOOL_DESCRIPTIONS:
                        args_match = re.search(r'<arguments>\s*(.*?)\s*</arguments>', body, re.DOTALL)
                        arguments = {}
                        if args_match:
                            args_body = args_match.group(1)
                            # S closing tagmi: <key>val</key>
                            for arg in re.finditer(r'<(\w+)>\s*([^<]*?)\s*</\1>', args_body):
                                val = arg.group(2).strip().strip('"\'`')
                                arguments[arg.group(1)] = val
                            # Bez closing tagov (broken XML): <key>val\n<next_key>
                            if not arguments:
                                tags = re.findall(r'<(\w+)>\s*(.+?)(?=\s*<\w+>|\s*$)', args_body, re.DOTALL)
                                for tag_name, val in tags:
                                    arguments[tag_name] = val.strip().strip('"\'`')
                        return (name, arguments)

        # Try bare JSON with "name" and "arguments" keys
        match = re.search(r'\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}', cleaned, re.DOTALL)
        if match:
            name = match.group(1)
            if name in TOOL_DESCRIPTIONS:
                try:
                    arguments = json.loads(match.group(2))
                    return (name, arguments)
                except json.JSONDecodeError:
                    pass

        return None

    def _strip_after_tool_call(self, text: str) -> str:
        """
        Odstráni všetok text za </tool_call> alebo za bare JSON tool call.
        Zabraňuje tomu, aby sa hallucinated výsledky ukladali do histórie.
        """
        # Strip po </tool_call> alebo </c>
        tc_end = text.find('</tool_call>')
        if tc_end >= 0:
            return text[:tc_end + len('</tool_call>')]
        tc_end = text.find('</c>')
        if tc_end >= 0:
            return text[:tc_end + len('</c>')]
        tc_end = text.find('</toolcall>')
        if tc_end >= 0:
            # Nájdi koniec markdown bloku ak existuje
            end = text.find('```', tc_end)
            return text[:end + 3] if end >= 0 else text[:tc_end + len('</toolcall>')]

        # Strip po bare JSON tool call - nájdi koniec JSON bloku
        # Handluje aj {name": variant (Qwen streaming artefakt)
        match = re.search(r'(\{[^{}]*"?name"?\s*:\s*"[^"]+"[^{}]*"?arguments"?\s*:\s*\{[^{}]*\}[^{}]*\})', text, re.DOTALL)
        if match:
            return text[:match.end()]

        return text

    def _smart_truncate(self, text: str, max_chars: int = 8000) -> str:
        """Skráti text na hranici kódového bloku (GO, def, CREATE, ===)."""
        if len(text) <= max_chars:
            return text

        # Hľadaj vhodné miesto na orezanie pred max_chars
        cut_zone = text[max_chars - 500:max_chars]
        # Preferuj rezanie na GO, CREATE, def, === hraniciach
        boundary_patterns = ['\nGO\n', '\nCREATE ', '\ndef ', '\nclass ', '\n===']
        best_cut = max_chars

        for pattern in boundary_patterns:
            idx = cut_zone.rfind(pattern)
            if idx >= 0:
                best_cut = max_chars - 500 + idx
                break

        # Fallback: posledný newline
        if best_cut == max_chars:
            nl_idx = cut_zone.rfind('\n')
            if nl_idx >= 0:
                best_cut = max_chars - 500 + nl_idx

        truncated = text[:best_cut]
        remaining = len(text) - best_cut
        return f"{truncated}\n\n[... skrátené, ešte {remaining} znakov ...]"

    def chat(self, message: str, thread_id: str = "default") -> str:
        """
        Spracuje správu pomocou ReAct slučky s konverzačnou pamäťou.
        Adaptívny system prompt podľa typu úlohy.
        Max 15 iterácií (tool calls) pred finálnou odpoveďou.
        """
        # Detekcia typu úlohy → adaptívny prompt a teplota
        task_type = detect_task_type(message)
        system_prompt = self.system_prompts[task_type]
        temperature = 0.3 if task_type == 'change' else 0.7 if task_type == 'analyze' else 0.3

        # Načítaj konverzačnú pamäť (sliding window)
        messages = self.memory.get_sliding_window(
            thread_id, system_prompt, max_messages=20
        )

        # Pridaj aktuálnu správu
        messages.append({"role": "user", "content": message})

        # Ulož user správu do pamäte
        self.memory.append(thread_id, "user", message)

        text = ""
        recent_tools = []  # sledovanie tool loop
        for iteration in range(15):
            text = self._call_llm(messages, temperature=temperature)

            # Skontroluj či LLM volá nástroj
            tool_call = self._parse_tool_call(text)

            if tool_call:
                tool_name, tool_args = tool_call
                recent_tools.append(tool_name)

                # Detekcia tool loop - 3x rovnaký nástroj za sebou
                if len(recent_tools) >= 3 and len(set(recent_tools[-3:])) == 1:
                    other_tools = [t for t in TOOL_DESCRIPTIONS if t != tool_name]
                    hint = (f"[SYSTÉM] Nástroj {tool_name} bol volaný 3x za sebou bez úspechu. "
                            f"Skús iný nástroj: {', '.join(other_tools[:3])}")
                    messages.append({"role": "user", "content": hint})
                    recent_tools.clear()
                    continue

                # Pridaj len tool call do histórie (bez hallucinated textu za ním)
                clean_text = self._strip_after_tool_call(text)
                messages.append({"role": "assistant", "content": clean_text})

                # Volaj nástroj
                tool_result = self.tools.call(tool_name, tool_args)

                # Ulož pending proposal ak je to propose_change
                if tool_name == 'propose_change':
                    self.pending_proposal = self.git_tools.propose_change(
                        tool_args.get('filepath', ''),
                        tool_args.get('new_content', ''),
                        tool_args.get('reason', ''),
                    )

                # Smart truncation - rezať na hranici kódového bloku
                result_text = self._smart_truncate(tool_result, max_chars=8000)
                messages.append({
                    "role": "user",
                    "content": f"[Výsledok nástroja {tool_name}]\n{result_text}"
                })
            else:
                # Finálna odpoveď — žiadny tool call
                # Ulož odpoveď do pamäte
                self.memory.append(thread_id, "assistant", text)
                return text

        # Ak sme presiahli max iterácií - ulož poslednú odpoveď
        self.memory.append(thread_id, "assistant", text)
        return text

    def index_repo(self, force: bool = False) -> Dict:
        """Indexuje repozitár."""
        count = self.indexer.index_repository(self.repo_path, force_reindex=force)
        stats = self.indexer.get_stats()
        return {'indexed': count, 'stats': stats}


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys

    repo_path = sys.argv[1] if len(sys.argv) > 1 else '.'
    ollama_url = os.environ.get('OLLAMA_URL', 'http://localhost:11434')

    agent = CodeAssistantAgent(
        repo_path=repo_path,
        ollama_url=ollama_url,
        llm_model="qwen2.5-coder:32b-instruct-q4_K_M",
    )

    print("=== Code Assistant Agent ===")
    print(f"Repo: {repo_path}")
    print("Napíš 'index' pre indexovanie repozitára")
    print("Napíš 'exit' pre ukončenie\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.lower() in ('exit', 'quit', 'koniec'):
            break
        elif user_input.lower() == 'index':
            print("Indexujem repozitár...")
            result = agent.index_repo()
            print(f"Indexovaných: {result['indexed']} chunkov")
            continue
        elif not user_input:
            continue

        response = agent.chat(user_input)
        print(f"\nAssistant: {response}\n")
