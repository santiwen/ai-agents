"""
dependency_graph.py
Buduje a traversuje dependency graph SQL a Python objektov.
Používa sa pri retrieval: ak nájdeš procedúru X, automaticky dotiahneš závislosti.
"""

import json
import pickle
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from collections import defaultdict, deque
from dataclasses import dataclass, field

from chunker import Chunk


@dataclass
class DependencyNode:
    name: str
    obj_type: str
    language: str
    source_file: str
    chunk_id: str
    dependents: Set[str] = field(default_factory=set)    # kto volá mňa
    dependencies: Set[str] = field(default_factory=set)  # koho ja volám


class DependencyGraph:
    """
    Directed dependency graph pre SQL a Python objekty.

    Hrany: A → B znamená "A závisí od B" (A volá/referencuje B).

    Funkcie:
      - build_from_chunks(chunks)   - postaví graf z chunkov
      - get_deps(name, depth)       - tranzitívne závislosti
      - get_dependents(name)        - kto závisí od daného objektu
      - get_with_deps(name)         - chunk + všetky jeho závislosti
      - find_cycles()               - nájde cyklické závislosti
      - save(path) / load(path)     - persistencia
    """

    def __init__(self):
        self.nodes: Dict[str, DependencyNode] = {}       # name → node
        self.name_to_chunk_id: Dict[str, str] = {}       # name → chunk_id
        self.chunk_id_to_chunk: Dict[str, Chunk] = {}    # chunk_id → Chunk
        # Normalizované meno → list možných mien (riešenie ambiguity)
        self._name_aliases: Dict[str, List[str]] = defaultdict(list)

    def build_from_chunks(self, chunks: List[Chunk]) -> None:
        """Postaví dependency graph zo zoznamu chunkov."""
        print(f"[dep_graph] Budujem graf z {len(chunks)} chunkov...")

        # Fáza 1: Zaregistruj všetky objekty
        for chunk in chunks:
            self._register_chunk(chunk)

        # Fáza 2: Prepoj závislosti
        for chunk in chunks:
            node = self.nodes.get(chunk.name)
            if node is None:
                node = self.nodes.get(self._normalize_name(chunk.name))
            if node is None:
                continue

            for dep_name in chunk.dependencies:
                resolved = self._resolve_name(dep_name)
                if resolved:
                    node.dependencies.add(resolved)
                    # Spätná hrana: dep je dependent-on mňa
                    if resolved in self.nodes:
                        self.nodes[resolved].dependents.add(chunk.name)

        total_edges = sum(len(n.dependencies) for n in self.nodes.values())
        print(f"[dep_graph] Graf: {len(self.nodes)} uzlov, {total_edges} hrán")

    def _register_chunk(self, chunk: Chunk) -> None:
        """Zaregistruje chunk ako uzol v grafe."""
        name = chunk.name
        norm_name = self._normalize_name(name)

        node = DependencyNode(
            name=name,
            obj_type=chunk.obj_type,
            language=chunk.language,
            source_file=chunk.source_file,
            chunk_id=chunk.chunk_id,
        )

        self.nodes[name] = node
        self.nodes[norm_name] = node  # alias pre normalizované meno
        self.name_to_chunk_id[name] = chunk.chunk_id
        self.name_to_chunk_id[norm_name] = chunk.chunk_id
        self.chunk_id_to_chunk[chunk.chunk_id] = chunk

        # Skrátené meno bez schémy (napr. "dbo.sp_foo" → "sp_foo")
        if '.' in name:
            short = name.split('.')[-1]
            self._name_aliases[self._normalize_name(short)].append(name)

    def get_deps(self, name: str, depth: int = 2,
                  include_types: List[str] = None) -> List[str]:
        """
        Vráti tranzitívne závislosti objektu do hĺbky depth.
        include_types: filtrovať len určité typy (napr. ['procedure', 'table'])
        """
        start = self._resolve_name(name)
        if not start:
            return []

        visited = set()
        queue = deque([(start, 0)])

        while queue:
            current, current_depth = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            if current_depth >= depth:
                continue

            node = self.nodes.get(current)
            if node:
                for dep in node.dependencies:
                    if dep not in visited:
                        queue.append((dep, current_depth + 1))

        result = list(visited - {start})

        if include_types:
            result = [
                r for r in result
                if self.nodes.get(r) and self.nodes[r].obj_type in include_types
            ]

        return result

    def get_dependents(self, name: str, depth: int = 1) -> List[str]:
        """Vráti objekty, ktoré závisia od daného objektu (kto ma volá)."""
        start = self._resolve_name(name)
        if not start:
            return []

        visited = set()
        queue = deque([(start, 0)])

        while queue:
            current, current_depth = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            if current_depth >= depth:
                continue

            node = self.nodes.get(current)
            if node:
                for dep in node.dependents:
                    if dep not in visited:
                        queue.append((dep, current_depth + 1))

        return list(visited - {start})

    def get_with_deps(self, name: str, depth: int = 2) -> List[Chunk]:
        """
        Vráti chunk pre daný objekt + všetky jeho závislosti.
        Hlavná funkcia pre RAG retrieval s kontextom.
        """
        chunks = []

        # Primárny objekt
        primary = self._get_chunk_by_name(name)
        if primary:
            chunks.append(primary)

        # Závislosti
        dep_names = self.get_deps(name, depth=depth)
        for dep_name in dep_names:
            chunk = self._get_chunk_by_name(dep_name)
            if chunk and chunk not in chunks:
                chunks.append(chunk)

        return chunks

    def get_impact(self, name: str) -> Dict:
        """
        Analýza dopadu zmeny - kto bude ovplyvnený ak sa zmení objekt.
        Vráti: priami a nepriami dependenti + počet.
        """
        direct = self.get_dependents(name, depth=1)
        indirect = self.get_dependents(name, depth=3)
        indirect = [d for d in indirect if d not in direct]

        return {
            'object': name,
            'direct_impact': direct,
            'indirect_impact': indirect,
            'total_affected': len(direct) + len(indirect),
        }

    def find_cycles(self) -> List[List[str]]:
        """Nájde cyklické závislosti v grafe (DFS)."""
        cycles = []
        visited = set()
        path = []
        path_set = set()

        def dfs(node_name: str):
            if node_name in path_set:
                cycle_start = path.index(node_name)
                cycles.append(path[cycle_start:] + [node_name])
                return
            if node_name in visited:
                return

            visited.add(node_name)
            path.append(node_name)
            path_set.add(node_name)

            node = self.nodes.get(node_name)
            if node:
                for dep in node.dependencies:
                    dfs(dep)

            path.pop()
            path_set.discard(node_name)

        for name in list(self.nodes.keys()):
            if name not in visited:
                dfs(name)

        return cycles

    def get_orphans(self) -> List[str]:
        """Objekty bez závislostí a bez dependentov (izolované)."""
        orphans = []
        for name, node in self.nodes.items():
            if not node.dependencies and not node.dependents:
                orphans.append(name)
        return orphans

    def get_hub_objects(self, min_dependents: int = 5) -> List[Tuple[str, int]]:
        """Objekty s najviac dependentmi (kritické/hub objekty)."""
        hubs = [
            (name, len(node.dependents))
            for name, node in self.nodes.items()
            if len(node.dependents) >= min_dependents
        ]
        return sorted(hubs, key=lambda x: -x[1])

    def stats(self) -> Dict:
        """Štatistiky grafu."""
        by_type = defaultdict(int)
        for node in self.nodes.values():
            by_type[node.obj_type] += 1

        return {
            'total_nodes': len(self.nodes),
            'total_edges': sum(len(n.dependencies) for n in self.nodes.values()),
            'by_type': dict(by_type),
            'cycles': len(self.find_cycles()),
            'hub_objects': self.get_hub_objects(min_dependents=3)[:10],
        }

    def save(self, path: str) -> None:
        """Uloží graf na disk."""
        data = {
            'nodes': {
                name: {
                    'obj_type': node.obj_type,
                    'language': node.language,
                    'source_file': node.source_file,
                    'chunk_id': node.chunk_id,
                    'dependencies': list(node.dependencies),
                    'dependents': list(node.dependents),
                }
                for name, node in self.nodes.items()
            },
            'name_to_chunk_id': self.name_to_chunk_id,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[dep_graph] Graf uložený: {path}")

    def load(self, path: str, chunks: List[Chunk] = None) -> None:
        """Načíta graf z disku."""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.name_to_chunk_id = data['name_to_chunk_id']

        for name, node_data in data['nodes'].items():
            node = DependencyNode(
                name=name,
                obj_type=node_data['obj_type'],
                language=node_data['language'],
                source_file=node_data['source_file'],
                chunk_id=node_data['chunk_id'],
                dependencies=set(node_data['dependencies']),
                dependents=set(node_data['dependents']),
            )
            self.nodes[name] = node

        if chunks:
            for chunk in chunks:
                self.chunk_id_to_chunk[chunk.chunk_id] = chunk

        print(f"[dep_graph] Graf načítaný: {len(self.nodes)} uzlov")

    def _resolve_name(self, name: str) -> Optional[str]:
        """Normalizuje a resolv-uje meno na kanonický tvar."""
        if name in self.nodes:
            return name
        norm = self._normalize_name(name)
        if norm in self.nodes:
            return norm
        # Skrátené meno → plné
        aliases = self._name_aliases.get(norm, [])
        if aliases:
            return aliases[0]
        return None

    def _normalize_name(self, name: str) -> str:
        """Normalizuje meno objektu (lowercase, strip schema prefix)."""
        return name.strip().lower()

    def _get_chunk_by_name(self, name: str) -> Optional[Chunk]:
        """Vráti Chunk pre dané meno."""
        resolved = self._resolve_name(name)
        if not resolved:
            return None
        chunk_id = self.name_to_chunk_id.get(resolved)
        if not chunk_id:
            return None
        return self.chunk_id_to_chunk.get(chunk_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    from chunker import SemanticChunker

    repo = sys.argv[1] if len(sys.argv) > 1 else '.'
    chunker = SemanticChunker(repo_root=repo)
    chunks = chunker.chunk_repository()

    graph = DependencyGraph()
    graph.build_from_chunks(chunks)

    stats = graph.stats()
    print("\n=== Dependency Graph Stats ===")
    print(f"Uzly: {stats['total_nodes']}")
    print(f"Hrany: {stats['total_edges']}")
    print(f"Cyklické závislosti: {stats['cycles']}")
    print("\nTypy objektov:")
    for t, n in sorted(stats['by_type'].items()):
        print(f"  {t}: {n}")

    print("\nHub objekty (najviac dependentov):")
    for name, cnt in stats['hub_objects'][:10]:
        print(f"  {name}: {cnt} dependentov")

    if len(sys.argv) > 2:
        query_name = sys.argv[2]
        deps = graph.get_deps(query_name, depth=2)
        print(f"\nZávislosti pre '{query_name}':")
        for d in deps:
            print(f"  → {d}")

        impact = graph.get_impact(query_name)
        print(f"\nDopad zmeny '{query_name}':")
        print(f"  Priami: {impact['direct_impact']}")
        print(f"  Nepriami: {impact['indirect_impact'][:5]}")
