"""
fkinstall_registry.py
Lightweight in-memory registry 278 fkinstall balíkov.
Parsuje fkinstall.ini (root + server/) a poskytuje lookup objektov k balíkom.
"""

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class PackageInfo:
    name: str                       # CS-CSD-TRM-AutoTransfer
    version: str = ""               # z fkinstall.ini [global] version
    author: str = ""                # z [global] author
    description: str = ""           # z [description] sekcie
    requirements: List[str] = field(default_factory=list)   # z [requirement]
    procedures: List[str] = field(default_factory=list)     # z [build_procedures*]
    tables: List[str] = field(default_factory=list)         # z [build_tables*]
    server_dir: str = ""            # z [global] server-dir
    common_dir: str = ""            # z [global] common-dir


class FkinstallRegistry:
    """Registry všetkých fkinstall balíkov v repozitári."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.packages: Dict[str, PackageInfo] = {}
        self.object_to_package: Dict[str, str] = {}

    def build(self):
        """Skenuje repo a parsuje fkinstall.ini z každého top-level adresára."""
        self.packages.clear()
        self.object_to_package.clear()

        for entry in sorted(self.repo_path.iterdir()):
            if not entry.is_dir():
                continue
            root_ini = entry / "fkinstall.ini"
            if not root_ini.exists():
                continue

            pkg_name = entry.name
            try:
                info = self._parse_package(entry, pkg_name)
                self.packages[pkg_name] = info

                # Registruj objekty
                for proc in info.procedures:
                    self.object_to_package[proc] = pkg_name
                for tbl in info.tables:
                    self.object_to_package[tbl] = pkg_name
            except Exception as e:
                print(f"[registry] WARN: {pkg_name}: {e}")

        print(f"[registry] {len(self.packages)} balíkov, "
              f"{len(self.object_to_package)} objektov")

    def _parse_package(self, pkg_dir: Path, pkg_name: str) -> PackageInfo:
        """Parsuje root + server fkinstall.ini pre jeden balík."""
        info = PackageInfo(name=pkg_name)

        # Root fkinstall.ini — [global], [description], [requirement]
        root_ini = pkg_dir / "fkinstall.ini"
        root_sections = self._parse_ini(root_ini)

        global_sec = root_sections.get('global', {})
        info.version = global_sec.get('version', '')
        info.author = global_sec.get('author', '')
        info.server_dir = global_sec.get('server-dir', '')
        info.common_dir = global_sec.get('common-dir', '')
        info.description = root_sections.get('description', {}).get('_text', '')
        info.requirements = root_sections.get('requirement', {}).get('_lines', [])

        # Server fkinstall.ini — [build_procedures*], [build_tables*]
        server_dir = info.server_dir or 'server'
        server_ini = pkg_dir / server_dir / "fkinstall.ini"
        if server_ini.exists():
            srv_sections = self._parse_ini(server_ini)

            for sec_name, sec_data in srv_sections.items():
                if sec_name.startswith('build_procedures'):
                    for line in sec_data.get('_lines', []):
                        if line and line not in info.procedures:
                            info.procedures.append(line)
                elif sec_name.startswith('build_tables'):
                    for line in sec_data.get('_lines', []):
                        if line and line not in info.tables:
                            info.tables.append(line)

            # Requirement z server ini tiež
            if 'requirement' in srv_sections:
                for req in srv_sections['requirement'].get('_lines', []):
                    if req and req not in info.requirements:
                        info.requirements.append(req)

        return info

    def _parse_ini(self, path: Path) -> Dict[str, Dict]:
        """
        Parsuje fkinstall.ini — hybridný formát:
        - [global]: key=value
        - [requirement], [build_procedures*], [build_tables*]: bare lines
        - [description]: celý text
        """
        sections: Dict[str, Dict] = {}
        current_section = None

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')

                # Nová sekcia
                sec_match = re.match(r'^\[([^\]]+)\]', line)
                if sec_match:
                    current_section = sec_match.group(1).strip().lower()
                    if current_section not in sections:
                        sections[current_section] = {'_lines': [], '_text': ''}
                    continue

                if current_section is None:
                    continue

                sec = sections[current_section]

                # Key=value pre [global]
                if current_section == 'global' and '=' in line:
                    key, _, val = line.partition('=')
                    sec[key.strip().lower()] = val.strip()
                    continue

                # [description] — akumuluj text
                if current_section == 'description':
                    sec['_text'] += line + '\n'
                    continue

                # Bare lines — requirement, build_procedures, build_tables
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and not stripped.startswith(';'):
                    sec['_lines'].append(stripped)

        # Trim description
        if 'description' in sections:
            sections['description']['_text'] = sections['description']['_text'].strip()

        return sections

    def save(self, path: str):
        """Uloží registry do JSON."""
        data = {
            'packages': {name: asdict(info) for name, info in self.packages.items()},
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"[registry] Uložené do {path}")

    def load(self, path: str):
        """Načíta registry z JSON."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.packages.clear()
        self.object_to_package.clear()

        for name, info_dict in data.get('packages', {}).items():
            info = PackageInfo(**info_dict)
            self.packages[name] = info
            for proc in info.procedures:
                self.object_to_package[proc] = name
            for tbl in info.tables:
                self.object_to_package[tbl] = name

        print(f"[registry] Načítané: {len(self.packages)} balíkov, "
              f"{len(self.object_to_package)} objektov")

    def find_package_for_object(self, name: str) -> Optional[str]:
        """Nájde balík podľa mena procedúry/tabuľky."""
        # Presný match
        if name in self.object_to_package:
            return self.object_to_package[name]
        # Case-insensitive
        name_lower = name.lower()
        for obj, pkg in self.object_to_package.items():
            if obj.lower() == name_lower:
                return pkg
        return None

    def find_packages(self, partial_name: str) -> List[str]:
        """Fuzzy match na meno balíka."""
        partial_lower = partial_name.lower()
        return [name for name in self.packages
                if partial_lower in name.lower()]

    def get_package_info(self, name: str) -> Optional[PackageInfo]:
        """Vráti info o balíku (presný alebo case-insensitive match)."""
        if name in self.packages:
            return self.packages[name]
        name_lower = name.lower()
        for pkg_name, info in self.packages.items():
            if pkg_name.lower() == name_lower:
                return info
        return None

    def format_package_info(self, name: str) -> str:
        """Formátovaný výpis balíka pre LLM/CLI."""
        info = self.get_package_info(name)
        if not info:
            matches = self.find_packages(name)
            if matches:
                return (f"Balík '{name}' nenájdený. Podobné balíky:\n"
                        + "\n".join(f"  - {m}" for m in matches[:10]))
            return f"Balík '{name}' nenájdený."

        lines = [
            f"=== Balík: {info.name} ===",
            f"Verzia: {info.version}",
            f"Autor: {info.author}",
        ]
        if info.description:
            lines.append(f"Popis: {info.description[:300]}")
        if info.requirements:
            lines.append(f"Požiadavky: {', '.join(info.requirements)}")
        if info.procedures:
            lines.append(f"Procedúry ({len(info.procedures)}): {', '.join(info.procedures)}")
        if info.tables:
            lines.append(f"Tabuľky ({len(info.tables)}): {', '.join(info.tables)}")
        return "\n".join(lines)
