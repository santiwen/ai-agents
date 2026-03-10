"""
cli.py
Interaktívne CLI rozhranie pre AI Code Asistenta.
Human-in-the-loop: všetky zmeny vyžadujú potvrdenie.

Príkazy:
  /index [--force]        - indexuj repozitár
  /stats                  - štatistiky indexu
  /search <query>         - priamy vector search
  /file <path>            - zobraziť súbor
  /deps <name>            - dependency graph pre objekt
  /history <path>         - git história
  /grep <pattern>         - git grep
  /filter lang=sql        - nastav filtre
  /approve                - schváli poslednú navrhnutú zmenu
  /reject                 - zamietni poslednú zmenu
  /help                   - zobraz pomoc
  /exit                   - ukončiť
"""

import os
import sys
import json
import readline
import textwrap
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime

# Farby pre terminál
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    DIM = '\033[2m'

    @staticmethod
    def disable():
        for attr in dir(Colors):
            if not attr.startswith('_') and attr != 'disable':
                setattr(Colors, attr, '')


def c(color: str, text: str) -> str:
    """Ofarbi text."""
    return f"{color}{text}{Colors.RESET}"


class CLIHistory:
    """Historia prikazov pre readline."""

    HISTORY_FILE = os.path.expanduser("~/.ai_assistant_history")

    def __init__(self):
        try:
            readline.read_history_file(self.HISTORY_FILE)
        except FileNotFoundError:
            pass
        readline.set_history_length(500)

    def save(self):
        try:
            readline.write_history_file(self.HISTORY_FILE)
        except Exception:
            pass


class CodeAssistantCLI:
    """
    Interaktívne CLI s human-in-the-loop safety.
    """

    BANNER = """
╔══════════════════════════════════════════════════════════╗
║         AI CODE ASSISTANT - FKTRM CSD Edition           ║
║   Qwen2.5-Coder-32B + ChromaDB + Dependency Graph       ║
╚══════════════════════════════════════════════════════════╝
"""

    def __init__(self,
                 repo_path: str,
                 chroma_path: str = "./chroma_db",
                 ollama_url: str = "http://localhost:11434",
                 llm_model: str = "qwen2.5-coder:32b-instruct-q4_K_M",
                 embed_model: str = "nomic-embed-text",
                 no_color: bool = False):

        if no_color or not sys.stdout.isatty():
            Colors.disable()

        self.repo_path = repo_path
        self.ollama_url = ollama_url
        self.llm_model = llm_model
        self.embed_model = embed_model
        self.chroma_path = chroma_path

        self.agent = None
        self.pending_proposal: Optional[Dict] = None
        self.conversation_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.filters: Dict = {}
        self.chat_history: List[Dict] = []

        self.cli_history = CLIHistory()

    def _lazy_init_agent(self):
        """Inicializuje agenta pri prvom použití."""
        if self.agent is None:
            print(c(Colors.DIM, "Inicializujem agenta..."))
            try:
                from agent import CodeAssistantAgent
                self.agent = CodeAssistantAgent(
                    repo_path=self.repo_path,
                    chroma_path=self.chroma_path,
                    ollama_url=self.ollama_url,
                    llm_model=self.llm_model,
                    embed_model=self.embed_model,
                )
                print(c(Colors.GREEN, "Agent inicializovaný."))
            except Exception as e:
                print(c(Colors.RED, f"Chyba inicializácie: {e}"))
                raise

    def run(self):
        """Hlavná slučka CLI."""
        print(c(Colors.CYAN, self.BANNER))
        print(c(Colors.DIM, f"Repozitár: {self.repo_path}"))
        print(c(Colors.DIM, f"Ollama: {self.ollama_url} | Model: {self.llm_model}"))
        print(c(Colors.DIM, f"Napíš /help pre zoznam príkazov\n"))

        # Auto-init s lazy loading
        try:
            self._lazy_init_agent()
        except Exception:
            print(c(Colors.YELLOW, "Agent sa nepodarilo inicializovať. Skontroluj Ollama."))

        while True:
            try:
                prompt = self._build_prompt()
                user_input = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n" + c(Colors.DIM, "Dovidenia!"))
                self.cli_history.save()
                break

            if not user_input:
                continue

            # Pridaj do histórie
            readline.add_history(user_input)

            # Spracuj vstup
            try:
                should_continue = self._handle_input(user_input)
                if not should_continue:
                    break
            except KeyboardInterrupt:
                print(c(Colors.YELLOW, "\nPrerušené."))
            except Exception as e:
                print(c(Colors.RED, f"Chyba: {e}"))
                import traceback
                traceback.print_exc()

        self.cli_history.save()

    def _build_prompt(self) -> str:
        """Zostaví prompt string."""
        parts = []

        if self.pending_proposal:
            parts.append(c(Colors.YELLOW, "[ČAKÁ NA SCHVÁLENIE] "))

        filter_str = ""
        if self.filters:
            filter_str = c(Colors.DIM, f"[{' '.join(f'{k}={v}' for k,v in self.filters.items())}] ")

        parts.append(filter_str)
        parts.append(c(Colors.GREEN + Colors.BOLD, "You"))
        parts.append(c(Colors.DIM, " > "))

        return ''.join(parts)

    def _handle_input(self, user_input: str) -> bool:
        """Spracuje vstup. Vráti False pre ukončenie."""
        # Slash príkazy
        if user_input.startswith('/'):
            return self._handle_command(user_input)

        # Shortcuty pre schválenie/zamietnutie
        if self.pending_proposal:
            lower = user_input.lower().strip()
            if lower in ('áno', 'ano', 'yes', 'y', 'schválim', 'schvalim', 'ok', 'aplikuj'):
                return self._approve_proposal()
            elif lower in ('nie', 'no', 'n', 'zamietam', 'zruš', 'zrus', 'cancel'):
                return self._reject_proposal()

        # Chat s agentom
        self._chat(user_input)
        return True

    def _handle_command(self, cmd_str: str) -> bool:
        """Spracuje /príkaz."""
        parts = cmd_str.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        commands = {
            '/index': self._cmd_index,
            '/stats': self._cmd_stats,
            '/search': self._cmd_search,
            '/file': self._cmd_file,
            '/deps': self._cmd_deps,
            '/history': self._cmd_history,
            '/grep': self._cmd_grep,
            '/filter': self._cmd_filter,
            '/approve': lambda _: self._approve_proposal(),
            '/reject': lambda _: self._reject_proposal(),
            '/newchat': self._cmd_newchat,
            '/help': self._cmd_help,
            '/exit': lambda _: False,
            '/quit': lambda _: False,
        }

        handler = commands.get(cmd)
        if handler:
            result = handler(args)
            return result if result is not None else True
        else:
            print(c(Colors.RED, f"Neznámy príkaz: {cmd}"))
            print(c(Colors.DIM, "Napíš /help pre zoznam príkazov"))
            return True

    def _cmd_index(self, args: str) -> bool:
        """Indexuje repozitár."""
        force = '--force' in args or '-f' in args
        self._lazy_init_agent()

        print(c(Colors.CYAN, f"Indexujem repozitár: {self.repo_path}"))
        if force:
            print(c(Colors.YELLOW, "Force reindex - mažem existujúce dáta..."))

        try:
            result = self.agent.index_repo(force=force)
            print(c(Colors.GREEN, f"Hotovo! Indexovaných: {result['indexed']} nových chunkov"))
            stats = result['stats']
            print(f"Celkom v DB: {stats['total_chunks']}")
        except Exception as e:
            print(c(Colors.RED, f"Chyba indexovania: {e}"))
        return True

    def _cmd_stats(self, args: str) -> bool:
        """Zobrazí štatistiky."""
        self._lazy_init_agent()
        stats = self.agent.indexer.get_stats()

        print(c(Colors.CYAN, "\n=== Index Stats ==="))
        print(f"Celkom chunkov: {c(Colors.BOLD, str(stats['total_chunks']))}")

        print("\nPodľa typu:")
        for t, n in sorted(stats.get('by_type', {}).items(), key=lambda x: -x[1]):
            bar = '█' * min(n // 10, 30)
            print(f"  {t:<20} {n:>5}  {c(Colors.BLUE, bar)}")

        print("\nPodľa jazyka:")
        for l, n in sorted(stats.get('by_language', {}).items(), key=lambda x: -x[1]):
            print(f"  {l:<20} {n:>5}")

        print("\nTop adresáre:")
        for d, n in stats.get('top_directories', [])[:8]:
            print(f"  {d:<40} {n:>4}")

        dep = stats.get('dependency_graph', {})
        if dep:
            print(f"\nDependency Graph:")
            print(f"  Uzly: {dep.get('total_nodes', 0)}")
            print(f"  Hrany: {dep.get('total_edges', 0)}")
            hubs = dep.get('hub_objects', [])
            if hubs:
                print(f"  Hub objekty (top 5):")
                for name, cnt in hubs[:5]:
                    print(f"    {name}: {cnt} dependentov")

        return True

    def _cmd_search(self, query: str) -> bool:
        """Priamy vector search."""
        if not query:
            print(c(Colors.YELLOW, "Použitie: /search <query>"))
            return True

        self._lazy_init_agent()

        lang = self.filters.get('lang')
        obj_type = self.filters.get('type')

        print(c(Colors.DIM, f"Hľadám: {query}..."))
        results = self.agent.retriever.search(
            query=query, n_results=5,
            filter_language=lang, filter_type=obj_type,
            expand_deps=True,
        )

        output = self.agent.retriever.format_results(results, show_content=True)
        print(output)
        return True

    def _cmd_file(self, filepath: str) -> bool:
        """Zobrazí obsah súboru."""
        if not filepath:
            print(c(Colors.YELLOW, "Použitie: /file <cesta/k/suboru>"))
            return True

        self._lazy_init_agent()
        content = self.agent.git_tools.get_file_content(filepath.strip())

        if not content:
            print(c(Colors.RED, f"Súbor nenájdený: {filepath}"))
            return True

        lines = content.splitlines()
        print(c(Colors.CYAN, f"\n=== {filepath} ({len(lines)} riadkov) ===\n"))

        for i, line in enumerate(lines, 1):
            print(f"{c(Colors.DIM, str(i).rjust(4))}  {line}")

        return True

    def _cmd_deps(self, name: str) -> bool:
        """Zobrazí dependency graph."""
        if not name:
            print(c(Colors.YELLOW, "Použitie: /deps <meno_objektu>"))
            return True

        self._lazy_init_agent()
        graph = self.agent.retriever.dep_graph

        deps = graph.get_deps(name.strip(), depth=3)
        dependents = graph.get_dependents(name.strip(), depth=2)
        impact = graph.get_impact(name.strip())

        print(c(Colors.CYAN, f"\n=== Dependency Graph: {name} ==="))

        if deps:
            print(c(Colors.YELLOW, f"\nZávislosti ({len(deps)}) - {name} VOLÁ:"))
            for d in deps:
                node = graph.nodes.get(d)
                t = node.obj_type if node else '?'
                print(f"  → {c(Colors.BLUE, f'[{t}]')} {d}")
        else:
            print(c(Colors.DIM, "Žiadne závislosti."))

        if dependents:
            print(c(Colors.YELLOW, f"\nDependenti ({len(dependents)}) - KTO VOLÁ {name}:"))
            for d in dependents:
                node = graph.nodes.get(d)
                t = node.obj_type if node else '?'
                print(f"  ← {c(Colors.MAGENTA, f'[{t}]')} {d}")
        else:
            print(c(Colors.DIM, "Nikto iný tento objekt nevolá."))

        print(c(Colors.RED, f"\nDopad zmeny: {impact['total_affected']} objektov"))
        return True

    def _cmd_history(self, filepath: str) -> bool:
        """Git história súboru."""
        if not filepath:
            print(c(Colors.YELLOW, "Použitie: /history <cesta/k/suboru>"))
            return True

        self._lazy_init_agent()
        commits = self.agent.git_tools.get_git_history(filepath.strip(), max_commits=15)

        if not commits:
            print(c(Colors.YELLOW, f"Žiadna história pre: {filepath}"))
            return True

        print(c(Colors.CYAN, f"\n=== Git história: {filepath} ===\n"))
        for c_ in commits:
            sha_colored = c(Colors.YELLOW, c_.sha)
            date_colored = c(Colors.DIM, c_.date)
            print(f"{sha_colored} {date_colored} {c_.author}")
            print(f"  {c_.message[:80]}")

        return True

    def _cmd_grep(self, args: str) -> bool:
        """Git grep search."""
        if not args:
            print(c(Colors.YELLOW, "Použitie: /grep <vzor> [--files=*.py]"))
            return True

        self._lazy_init_agent()

        # Parse args
        parts = args.split()
        pattern = parts[0]
        file_pattern = None
        for p in parts[1:]:
            if p.startswith('--files='):
                file_pattern = p[8:]

        results = self.agent.git_tools.search_in_git(pattern, file_pattern)

        if not results:
            print(c(Colors.YELLOW, f"Nič nenájdené pre: {pattern}"))
            return True

        print(c(Colors.CYAN, f"\n=== Git grep: '{pattern}' ({len(results)} výsledkov) ===\n"))
        for r in results[:30]:
            file_colored = c(Colors.BLUE, r['file'])
            line_colored = c(Colors.YELLOW, str(r['line']))
            print(f"{file_colored}:{line_colored}  {r['content']}")

        return True

    def _cmd_filter(self, args: str) -> bool:
        """Nastaví filtre pre search."""
        if not args or args == 'reset':
            self.filters = {}
            print(c(Colors.GREEN, "Filtre vymazané."))
            return True

        for part in args.split():
            if '=' in part:
                key, val = part.split('=', 1)
                self.filters[key.strip()] = val.strip()

        print(c(Colors.GREEN, f"Filtre: {self.filters}"))
        return True

    def _cmd_newchat(self, args: str) -> bool:
        """Začne novú konverzáciu (vymaže pamäť pre aktuálny thread)."""
        old_id = self.conversation_id
        self.conversation_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.pending_proposal = None
        if self.agent:
            self.agent.pending_proposal = None
        print(c(Colors.GREEN, f"Nová konverzácia: {self.conversation_id}"))
        return True

    def _cmd_help(self, args: str) -> bool:
        """Zobrazí pomoc."""
        help_text = f"""
{c(Colors.CYAN + Colors.BOLD, '=== AI Code Assistant - Príkazy ===')}

{c(Colors.YELLOW, 'Indexovanie:')}
  /index [--force]        Indexuje repozitár (--force = reindex všetko)
  /stats                  Štatistiky indexu a dependency grafu

{c(Colors.YELLOW, 'Vyhľadávanie:')}
  /search <query>         Sémantické vyhľadávanie v kóde
  /file <path>            Zobrazí kompletný súbor z git
  /deps <name>            Dependency graph pre objekt
  /history <path>         Git história súboru
  /grep <pattern>         Fulltext search (git grep)

{c(Colors.YELLOW, 'Filtre (pre /search):')}
  /filter lang=sql        Filtrovať len SQL objekty
  /filter lang=python     Filtrovať len Python
  /filter type=procedure  Filtrovať len stored procedures
  /filter type=table      Filtrovať len tabuľky
  /filter reset           Vymazať filtre

{c(Colors.YELLOW, 'Správa zmien:')}
  /approve                Schváli navrhnutú zmenu
  /reject                 Zamietni navrhnutú zmenu

{c(Colors.YELLOW, 'Skratky pre schválenie:')}
  áno / yes / ok          → Schváliť zmenu
  nie / no / zamietam     → Zamietnuť zmenu

{c(Colors.YELLOW, 'Iné:')}
  /newchat                Začni novú konverzáciu (vymaže kontext)
  /help                   Táto pomoc
  /exit                   Ukončiť

{c(Colors.DIM, 'Tip: Chatuj priamo - opýtaj sa na čokoľvek o kóde!')}
{c(Colors.DIM, 'Príklady:')}
  {c(Colors.DIM, '  "Vysvetli procedúru sp_calc_accruals"')}
  {c(Colors.DIM, '  "Optimalizuj query v sp_get_positions"')}
  {c(Colors.DIM, '  "Aké tabuľky používa modul trm_trades.py?"')}
  {c(Colors.DIM, '  "Navrhni index pre tabuľku FKT_DEAL"')}
"""
        print(help_text)
        return True

    def _chat(self, message: str):
        """Pošle správu agentovi so streaming výstupom."""
        self._lazy_init_agent()

        print(f"\n{c(Colors.BLUE + Colors.BOLD, 'Assistant:')}\n", end='', flush=True)

        streaming_started = [False]

        def on_token(token: str):
            if not streaming_started[0]:
                streaming_started[0] = True
            print(token, end='', flush=True)

        try:
            response = self.agent.chat_stream(
                message,
                thread_id=self.conversation_id,
                token_callback=on_token,
            )
        except Exception as e:
            print(c(Colors.RED, f"\nChyba: {e}"))
            import traceback
            traceback.print_exc()
            return

        # Ak streaming nebol použitý (fallback), zobraz odpoveď tradične
        if not streaming_started[0] and response:
            for para in response.split('\n'):
                if len(para) > 120:
                    print(textwrap.fill(para, width=120))
                else:
                    print(para)

        print('\n')

        # Skontroluj či agent má pending proposal
        if self.agent and self.agent.pending_proposal:
            self.pending_proposal = self.agent.pending_proposal
            print(c(Colors.YELLOW, "="*60))
            print(c(Colors.GREEN, "Napíš 'áno' pre schválenie alebo 'nie' pre zamietnutie"))
        elif 'NAVRHOVANÁ ZMENA' in response:
            self.pending_proposal = {'raw': response, 'message': message}
            print(c(Colors.YELLOW, "="*60))
            print(c(Colors.GREEN, "Napíš 'áno' pre schválenie alebo 'nie' pre zamietnutie"))

        # Ulož do histórie
        self.chat_history.append({
            'role': 'user', 'content': message,
            'timestamp': datetime.now().isoformat()
        })
        self.chat_history.append({
            'role': 'assistant', 'content': response,
            'timestamp': datetime.now().isoformat()
        })

    def _approve_proposal(self) -> bool:
        """Schváli poslednú navrhnutú zmenu - priamy apply bez LLM."""
        if not self.pending_proposal and (not self.agent or not self.agent.pending_proposal):
            print(c(Colors.YELLOW, "Žiadna čakajúca zmena."))
            return True

        # Použi proposal z agenta (nie z CLI detekcie)
        proposal = self.agent.pending_proposal if self.agent and self.agent.pending_proposal else None
        if not proposal:
            print(c(Colors.YELLOW, "Žiadna čakajúca zmena v agentovi."))
            self.pending_proposal = None
            return True

        print(c(Colors.GREEN, "Schvaľujem zmenu..."))

        # Priamy apply cez git_tools - neposielať cez LLM
        result = self.agent.git_tools.apply_change(proposal, approved=True)

        if result.get('status') == 'applied':
            print(c(Colors.GREEN, f"Zmena aplikovaná: {result['filepath']}"))
            if result.get('backup'):
                print(c(Colors.DIM, f"Backup: {result['backup']}"))
            if result.get('staged'):
                print(c(Colors.DIM, "Súbor pridaný do git stage"))
        else:
            print(c(Colors.RED, f"Chyba pri aplikovaní zmeny: {result}"))

        self.pending_proposal = None
        self.agent.pending_proposal = None
        return True

    def _reject_proposal(self) -> bool:
        """Zamietne poslednú navrhnutú zmenu."""
        if not self.pending_proposal and (not self.agent or not self.agent.pending_proposal):
            print(c(Colors.YELLOW, "Žiadna čakajúca zmena."))
            return True

        print(c(Colors.RED, "Zmena zamietnutá."))
        self.pending_proposal = None
        if self.agent:
            self.agent.pending_proposal = None
        return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='AI Code Assistant pre FKTRM CSD projekt'
    )
    parser.add_argument('repo_path', nargs='?', default='.',
                        help='Cesta k git repozitáru (default: .)')
    parser.add_argument('--chroma', default='./chroma_db',
                        help='Cesta k ChromaDB (default: ./chroma_db)')
    parser.add_argument('--ollama', default='http://localhost:11434',
                        help='Ollama URL (default: http://localhost:11434)')
    parser.add_argument('--model', default='qwen2.5-coder:32b-instruct-q4_K_M',
                        help='LLM model (default: qwen2.5-coder:32b-instruct-q4_K_M)')
    parser.add_argument('--embed-model', default='nomic-embed-text',
                        help='Embedding model (default: nomic-embed-text)')
    parser.add_argument('--no-color', action='store_true',
                        help='Vypni farby')
    parser.add_argument('--index', action='store_true',
                        help='Indexuj repozitár a skonči')
    parser.add_argument('--force-index', action='store_true',
                        help='Force reindex a skonči')

    args = parser.parse_args()

    cli = CodeAssistantCLI(
        repo_path=args.repo_path,
        chroma_path=args.chroma,
        ollama_url=args.ollama,
        llm_model=args.model,
        embed_model=args.embed_model,
        no_color=args.no_color,
    )

    if args.index or args.force_index:
        cli._lazy_init_agent()
        result = cli.agent.index_repo(force=args.force_index)
        print(f"Indexovaných: {result['indexed']} chunkov")
        stats = result['stats']
        print(f"Celkom v DB: {stats['total_chunks']}")
        return

    cli.run()


if __name__ == '__main__':
    main()
