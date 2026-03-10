"""
retriever.py
Hybrid retriever: vector search + dependency graph expansion.
Pri retrieval automaticky dotiahne závislosti nájdených objektov.
"""

from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
import re

import chromadb
from chromadb.config import Settings
from pathlib import Path

from indexer import CodeIndexer, OllamaEmbedder
from dependency_graph import DependencyGraph
from chunker import Chunk


@dataclass
class SearchResult:
    """Výsledok vyhľadávania s kontextom."""
    chunk_id: str
    name: str
    obj_type: str
    language: str
    source_file: str
    line_start: int
    line_end: int
    content: str
    summary: str
    score: float              # cosine similarity (0-1)
    is_dependency: bool       # True ak bol pridaný ako závislosť
    dep_depth: int            # hĺbka závislosti (0=priamy, 1=závislosť atď.)
    dependencies: List[str]
    parameters: List[str]
    metadata: Dict[str, Any]

    def __repr__(self):
        kind = "DEP" if self.is_dependency else "MATCH"
        return f"<{kind}:{self.obj_type.upper()} {self.name} score={self.score:.3f}>"


class CodeRetriever:
    """
    Hybrid retriever kombinujúci:
    1. Vector similarity search (ChromaDB + nomic-embed-text)
    2. Dependency graph expansion (automaticky dotiahne závislosti)
    3. Keyword/metadata filtre
    """

    def __init__(self, indexer: CodeIndexer):
        self.indexer = indexer
        self.collection = indexer.collection
        self.embedder = indexer.embedder
        self.dep_graph = indexer.dep_graph

    def search(self,
               query: str,
               n_results: int = 5,
               expand_deps: bool = True,
               dep_depth: int = 2,
               filter_language: Optional[str] = None,
               filter_type: Optional[str] = None,
               filter_file_pattern: Optional[str] = None,
               filter_package: Optional[str] = None,
               min_score: float = 0.0) -> List[SearchResult]:
        """
        Hlavná search funkcia.

        Args:
            query: Prirodzený jazyk alebo meno objektu
            n_results: Počet primárnych výsledkov
            expand_deps: Či rozšíriť o závislosti
            dep_depth: Hĺbka rozšírenia závislostí
            filter_language: Filtrovať podľa jazyka ('sql', 'python', ...)
            filter_type: Filtrovať podľa typu ('procedure', 'table', ...)
            filter_file_pattern: Regex pattern pre cestu súboru
            min_score: Minimálna similarity score
        """
        # Embedding pre query
        query_embedding = self.embedder.embed_one(query)

        # Filtre pre ChromaDB
        where_filter = self._build_where_filter(
            filter_language, filter_type, filter_file_pattern, filter_package
        )

        # Vector search
        search_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": min(n_results * 3, self.collection.count() or 1),
            "include": ["documents", "metadatas", "distances"],
        }
        if where_filter:
            search_kwargs["where"] = where_filter

        raw_results = self.collection.query(**search_kwargs)

        # Konvertuj výsledky
        primary_results = self._process_raw_results(raw_results, min_score)

        # Obmedz na n_results
        primary_results = primary_results[:n_results]

        if not expand_deps or not primary_results:
            return primary_results

        # Rozšíri o závislosti
        all_results = self._expand_with_dependencies(
            primary_results, dep_depth
        )

        return all_results

    def search_by_name(self, name: str,
                        exact: bool = False,
                        expand_deps: bool = True,
                        dep_depth: int = 2) -> List[SearchResult]:
        """
        Vyhľadáva objekt podľa mena (presne alebo fuzzy).
        Ideálne pre: "Nájdi procedúru sp_calc_accruals"
        """
        # Skúsi presné meno cez dependency graph
        if exact:
            chunks = self.dep_graph.get_with_deps(name, depth=dep_depth if expand_deps else 0)
            results = []
            for i, chunk in enumerate(chunks):
                results.append(self._chunk_to_result(
                    chunk, score=1.0 if i == 0 else 0.8,
                    is_dependency=(i > 0), dep_depth=min(i, dep_depth)
                ))
            return results

        # Fuzzy: vector search + keyword boost
        boosted_query = f"function procedure {name} {name}"
        results = self.search(boosted_query, n_results=10, expand_deps=expand_deps,
                               dep_depth=dep_depth)

        # Rerank: objekty s menom v identifikátore na vrch
        name_lower = name.lower()
        results.sort(key=lambda r: (
            -int(name_lower in r.name.lower()),
            -r.score
        ))

        return results[:5]

    def get_file_chunks(self, source_file: str) -> List[SearchResult]:
        """Vráti všetky chunky z konkrétneho súboru."""
        results = self.collection.get(
            where={"source_file": source_file},
            include=["documents", "metadatas"]
        )

        chunks = []
        for i, (doc_id, doc, meta) in enumerate(zip(
            results.get('ids', []),
            results.get('documents', []),
            results.get('metadatas', [])
        )):
            chunks.append(self._meta_to_result(meta, doc, score=1.0))

        return sorted(chunks, key=lambda r: r.line_start)

    def get_context_for_change(self, obj_name: str,
                                change_description: str) -> List[SearchResult]:
        """
        Zbiera kontext pre navrhovanú zmenu:
        - Samotný objekt
        - Jeho závislosti (čo volá)
        - Objekty, ktoré ho volajú (impact analysis)
        - Sémanticky podobné objekty
        """
        results = []
        seen_ids = set()

        def add_result(r: SearchResult):
            if r.chunk_id not in seen_ids:
                seen_ids.add(r.chunk_id)
                results.append(r)

        # 1. Primárny objekt + závislosti
        for r in self.search_by_name(obj_name, expand_deps=True, dep_depth=2):
            add_result(r)

        # 2. Dependenti (kto volá tento objekt)
        dependents = self.dep_graph.get_dependents(obj_name, depth=1)
        for dep_name in dependents[:3]:
            for r in self.search_by_name(dep_name, expand_deps=False):
                r.is_dependency = True
                r.dep_depth = -1  # -1 = dependent (caller)
                add_result(r)

        # 3. Sémanticky podobné na základe popisu zmeny
        query = f"{obj_name} {change_description}"
        for r in self.search(query, n_results=3, expand_deps=False):
            add_result(r)

        return results

    def _expand_with_dependencies(self, primary: List[SearchResult],
                                   depth: int) -> List[SearchResult]:
        """Rozšíri výsledky o závislosti."""
        all_results = list(primary)
        seen_ids = {r.chunk_id for r in primary}

        for result in primary:
            dep_names = self.dep_graph.get_deps(result.name, depth=depth)
            for dep_name in dep_names:
                dep_chunks = self.dep_graph.get_with_deps(dep_name, depth=0)
                for chunk in dep_chunks:
                    if chunk.chunk_id not in seen_ids:
                        seen_ids.add(chunk.chunk_id)
                        all_results.append(self._chunk_to_result(
                            chunk, score=result.score * 0.7,
                            is_dependency=True, dep_depth=1
                        ))

        return all_results

    def _process_raw_results(self, raw_results: Dict,
                              min_score: float) -> List[SearchResult]:
        """Konvertuje raw ChromaDB výsledky na SearchResult objekty."""
        results = []

        ids_list = raw_results.get('ids', [[]])[0]
        docs_list = raw_results.get('documents', [[]])[0]
        metas_list = raw_results.get('metadatas', [[]])[0]
        dists_list = raw_results.get('distances', [[]])[0]

        for chunk_id, doc, meta, dist in zip(ids_list, docs_list, metas_list, dists_list):
            score = max(0.0, 1.0 - dist)  # cosine distance → similarity
            if score < min_score:
                continue
            results.append(self._meta_to_result(meta, doc, score))

        return results

    def _meta_to_result(self, meta: Dict, content: str, score: float,
                         is_dependency: bool = False, dep_depth: int = 0) -> SearchResult:
        """Konvertuje ChromaDB metadata na SearchResult."""
        deps_str = meta.get('dependencies', '')
        params_str = meta.get('parameters', '')
        return SearchResult(
            chunk_id=meta.get('chunk_id', meta.get('name', '')),
            name=meta.get('name', ''),
            obj_type=meta.get('obj_type', 'unknown'),
            language=meta.get('language', 'unknown'),
            source_file=meta.get('source_file', ''),
            line_start=meta.get('line_start', 0),
            line_end=meta.get('line_end', 0),
            content=content,
            summary=meta.get('summary', ''),
            score=score,
            is_dependency=is_dependency,
            dep_depth=dep_depth,
            dependencies=[d for d in deps_str.split(',') if d],
            parameters=[p for p in params_str.split(',') if p],
            metadata=meta,
        )

    def _chunk_to_result(self, chunk: Chunk, score: float,
                          is_dependency: bool = False,
                          dep_depth: int = 0) -> SearchResult:
        """Konvertuje Chunk na SearchResult."""
        return SearchResult(
            chunk_id=chunk.chunk_id,
            name=chunk.name,
            obj_type=chunk.obj_type,
            language=chunk.language,
            source_file=chunk.source_file,
            line_start=chunk.line_start,
            line_end=chunk.line_end,
            content=chunk.content,
            summary=chunk.summary,
            score=score,
            is_dependency=is_dependency,
            dep_depth=dep_depth,
            dependencies=chunk.dependencies,
            parameters=chunk.parameters,
            metadata=chunk.metadata,
        )

    def _build_where_filter(self, language: Optional[str],
                             obj_type: Optional[str],
                             file_pattern: Optional[str],
                             package: Optional[str] = None) -> Optional[Dict]:
        """Buduje ChromaDB where filter."""
        conditions = []

        if language:
            conditions.append({"language": {"$eq": language}})
        if obj_type:
            conditions.append({"obj_type": {"$eq": obj_type}})
        if package:
            conditions.append({"package_name": {"$eq": package}})
        # file_pattern nie je priamo podporovaný v ChromaDB where
        # - riešime post-filter

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def format_results(self, results: List[SearchResult],
                        show_content: bool = True,
                        max_content_chars: int = 500) -> str:
        """Formátuje výsledky pre zobrazenie."""
        if not results:
            return "Žiadne výsledky."

        lines = []
        primary = [r for r in results if not r.is_dependency]
        deps = [r for r in results if r.is_dependency]

        lines.append(f"=== Výsledky ({len(primary)} hlavných + {len(deps)} závislostí) ===\n")

        for i, r in enumerate(primary, 1):
            lines.append(f"{i}. [{r.language.upper()} {r.obj_type.upper()}] {r.name}")
            pkg = r.metadata.get('package_name', '')
            pkg_str = f" [{pkg}]" if pkg else ""
            lines.append(f"   Súbor: {r.source_file}:{r.line_start}-{r.line_end}{pkg_str}")
            lines.append(f"   Score: {r.score:.3f}")
            if r.parameters:
                lines.append(f"   Parametre: {', '.join(r.parameters[:5])}")
            if r.dependencies:
                lines.append(f"   Volá: {', '.join(r.dependencies[:5])}")
            if show_content and r.content:
                preview = r.content[:max_content_chars].replace('\n', '\n   ')
                lines.append(f"   Kód:\n   {preview}")
                if len(r.content) > max_content_chars:
                    lines.append(f"   ... [{len(r.content)-max_content_chars} znakov skrytých]")
            lines.append("")

        if deps:
            lines.append(f"--- Závislosti ({len(deps)}) ---")
            for r in deps[:5]:
                lines.append(f"  → [{r.obj_type.upper()}] {r.name} ({r.source_file})")

        return '\n'.join(lines)
