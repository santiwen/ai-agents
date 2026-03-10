"""
indexer.py
ChromaDB indexer - ukladá chunky do vektorovej DB s nomic-embed-text cez Ollama.
Perzistentný lokálny ChromaDB bez externých služieb.
GPU: embeddingy beží cez Ollama ktorý automaticky využíva CUDA GPU.
"""

import os
import json
import time
import hashlib
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any

import chromadb
from chromadb.config import Settings
import requests

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from chunker import Chunk, SemanticChunker
from dependency_graph import DependencyGraph


def _check_gpu():
    """Zobrazí info o GPU dostupnosti pre Ollama."""
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                            "--format=csv,noheader"], capture_output=True, timeout=5)
        if r.returncode == 0:
            gpu_info = r.stdout.decode().strip()
            print(f"[gpu] GPU: {gpu_info}")
            return True
    except Exception:
        pass
    print("[gpu] GPU info nedostupné (nvidia-smi nenájdené alebo CPU mode)")
    return False


# ---------------------------------------------------------------------------
# Ollama embedding client
# ---------------------------------------------------------------------------

class OllamaEmbedder:
    """Wrapper pre Ollama embedding API."""

    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip('/')
        self._check_model()

    def _check_model(self):
        """Skontroluje či model beží."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            models = [m['name'] for m in r.json().get('models', [])]
            if not any(self.model in m for m in models):
                print(f"[embedder] WARN: Model '{self.model}' nie je v Ollama.")
                print(f"  Spusti: ollama pull {self.model}")
        except Exception as e:
            print(f"[embedder] WARN: Ollama nedostupná: {e}")

    def embed(self, texts: List[str], batch_size: int = 32,
              progress_bar=None) -> List[List[float]]:
        """Vytvorí embeddingy pre zoznam textov."""
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = []

            for text in batch:
                try:
                    r = requests.post(
                        f"{self.base_url}/api/embeddings",
                        json={"model": self.model, "prompt": text},
                        timeout=60
                    )
                    r.raise_for_status()
                    embedding = r.json()['embedding']
                    batch_embeddings.append(embedding)
                except Exception as e:
                    print(f"[embedder] ERR embedding: {e}")
                    batch_embeddings.append([0.0] * 768)

                if progress_bar is not None:
                    progress_bar.update(1)

            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    def embed_one(self, text: str) -> List[float]:
        """Embedding pre jeden text."""
        return self.embed([text])[0]


# ---------------------------------------------------------------------------
# ChromaDB Indexer
# ---------------------------------------------------------------------------

class CodeIndexer:
    """
    Manages ChromaDB collections pre code chunks.

    Kolekcie:
      - code_objects  : hlavná kolekcia (procedures, tables, functions, classes)
      - modules       : Python moduly a Perl skripty ako celky
    """

    COLLECTION_NAME = "code_objects"
    DEP_GRAPH_FILE = "dependency_graph.json"

    def __init__(self,
                 chroma_path: str = "./chroma_db",
                 ollama_url: str = "http://localhost:11434",
                 embed_model: str = "nomic-embed-text"):

        self.chroma_path = Path(chroma_path)
        self.chroma_path.mkdir(parents=True, exist_ok=True)

        # ChromaDB - persistentný lokálny
        self.client = chromadb.PersistentClient(
            path=str(self.chroma_path),
            settings=Settings(anonymized_telemetry=False)
        )

        # Embedder
        self.embedder = OllamaEmbedder(model=embed_model, base_url=ollama_url)

        # Kolekcia
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )

        # Dependency graph
        self.dep_graph = DependencyGraph()
        self._load_dep_graph_if_exists()

        print(f"[indexer] ChromaDB: {self.chroma_path}")
        print(f"[indexer] Existujúcich chunkov: {self.collection.count()}")
        _check_gpu()

    def index_repository(self, repo_path: str,
                          incremental: bool = True,
                          force_reindex: bool = False) -> int:
        """
        Indexuje repozitár.
        incremental=True: preskočí nezmenené súbory
        force_reindex=True: vymaže a znova indexuje všetko
        """
        t_start = time.time()
        print(f"\n[indexer] Indexujem: {repo_path}")

        if force_reindex:
            print("[indexer] Force reindex - mažem existujúce dáta...")
            self.client.delete_collection(self.COLLECTION_NAME)
            self.collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )

        # Chunkovanie
        print("[indexer] Parsovanie súborov...", flush=True)
        chunker = SemanticChunker(repo_root=repo_path)
        chunks = chunker.chunk_repository()

        if not chunks:
            print("[indexer] Žiadne chunky nenájdené.")
            return 0

        print(f"[indexer] Celkom chunkov: {len(chunks)}", flush=True)

        # Dependency graph
        print("[indexer] Budujem dependency graph...", flush=True)
        t_dep = time.time()
        self.dep_graph.build_from_chunks(chunks)
        self._save_dep_graph()
        print(f"[indexer] Dependency graph hotový ({time.time()-t_dep:.1f}s)")

        # Filter: preskočiť existujúce (ak incremental)
        if incremental and not force_reindex:
            chunks = self._filter_new_chunks(chunks)
            if not chunks:
                print("[indexer] Všetky chunky sú aktuálne.")
                return 0

        # Indexovanie po dávkach
        indexed = self._index_chunks(chunks)
        elapsed = time.time() - t_start
        print(f"\n[indexer] Indexovaných: {indexed} chunkov za {elapsed:.0f}s")
        print(f"[indexer] Celkom v DB: {self.collection.count()}")
        return indexed

    def _index_chunks(self, chunks: List[Chunk], batch_size: int = 50) -> int:
        """Indexuje chunky do ChromaDB s progress barom."""
        total = 0
        n = len(chunks)

        if HAS_TQDM:
            pbar = tqdm(total=n, desc="Indexovanie", unit="chunk",
                        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
        else:
            pbar = None
            print(f"[indexer] Indexovanie {n} chunkov...", flush=True)

        t_batch_start = time.time()

        for i in range(0, n, batch_size):
            batch = chunks[i:i + batch_size]
            embed_texts = [c.embed_text for c in batch]

            if not HAS_TQDM:
                pct = int(100 * i / n)
                elapsed = time.time() - t_batch_start
                eta = (elapsed / i * (n - i)) if i > 0 else 0
                print(f"  [{pct:3d}%] batch {i+1}-{min(i+batch_size,n)}/{n}"
                      f"  elapsed={elapsed:.0f}s  eta={eta:.0f}s", flush=True)

            embeddings = self.embedder.embed(embed_texts, progress_bar=pbar)

            ids, documents, metadatas = [], [], []
            for chunk, embedding in zip(batch, embeddings):
                chunk_id = self._safe_id(chunk.chunk_id)
                ids.append(chunk_id)
                documents.append(chunk.content[:8000])
                metadatas.append({
                    'chunk_id': chunk.chunk_id,
                    'name': chunk.name,
                    'obj_type': chunk.obj_type,
                    'language': chunk.language,
                    'source_file': chunk.source_file,
                    'line_start': chunk.line_start,
                    'line_end': chunk.line_end,
                    'module': chunk.module or '',
                    'docstring': (chunk.docstring or '')[:500],
                    'dependencies': ','.join(chunk.dependencies[:20]),
                    'parameters': ','.join(chunk.parameters[:20]),
                    'summary': chunk.summary[:500],
                    'package_name': chunk.metadata.get('package_name', ''),
                    'content_hash': self._hash(chunk.content),
                })

            try:
                self.collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    documents=documents,
                    metadatas=metadatas,
                )
                total += len(batch)
            except Exception as e:
                print(f"[indexer] ERR upsert: {e}")

        if pbar:
            pbar.close()

        return total

    def _filter_new_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """Vráti len chunky, ktoré sa zmenili alebo sú nové."""
        if self.collection.count() == 0:
            return chunks

        new_chunks = []
        for chunk in chunks:
            chunk_id = self._safe_id(chunk.chunk_id)
            try:
                result = self.collection.get(ids=[chunk_id], include=['metadatas'])
                if result['ids']:
                    old_hash = result['metadatas'][0].get('content_hash', '')
                    new_hash = self._hash(chunk.content)
                    if old_hash == new_hash:
                        continue  # Nezmenený - preskočiť
            except Exception:
                pass
            new_chunks.append(chunk)

        print(f"[indexer] Nových/zmenených chunkov: {len(new_chunks)}/{len(chunks)}")
        return new_chunks

    def _save_dep_graph(self):
        """Uloží dependency graph."""
        path = self.chroma_path / self.DEP_GRAPH_FILE
        self.dep_graph.save(str(path))

    def _load_dep_graph_if_exists(self):
        """Načíta dependency graph ak existuje."""
        path = self.chroma_path / self.DEP_GRAPH_FILE
        if path.exists():
            try:
                self.dep_graph.load(str(path))
            except Exception as e:
                print(f"[indexer] WARN: Dep graph load failed: {e}")

    def _safe_id(self, chunk_id: str) -> str:
        """ChromaDB ID musí byť bezpečný string."""
        # Hash pre príliš dlhé alebo špeciálne IDs
        if len(chunk_id) > 512 or any(c in chunk_id for c in ['\n', '\r', '\t']):
            return hashlib.md5(chunk_id.encode()).hexdigest()
        return chunk_id.replace('\\', '/').replace(' ', '_')

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()[:16]

    def get_stats(self) -> Dict:
        """Štatistiky indexu."""
        count = self.collection.count()

        # Sample pre štatistiky
        sample = self.collection.get(limit=min(count, 1000), include=['metadatas'])

        by_type = {}
        by_lang = {}
        by_file = {}

        for meta in sample.get('metadatas', []):
            t = meta.get('obj_type', 'unknown')
            l = meta.get('language', 'unknown')
            f = str(Path(meta.get('source_file', '')).parent)

            by_type[t] = by_type.get(t, 0) + 1
            by_lang[l] = by_lang.get(l, 0) + 1
            by_file[f] = by_file.get(f, 0) + 1

        dep_stats = self.dep_graph.stats()

        return {
            'total_chunks': count,
            'by_type': by_type,
            'by_language': by_lang,
            'top_directories': sorted(by_file.items(), key=lambda x: -x[1])[:10],
            'dependency_graph': dep_stats,
        }

    def delete_file(self, source_file: str) -> int:
        """Vymaže všetky chunky z daného súboru."""
        results = self.collection.get(
            where={"source_file": source_file},
            include=['metadatas']
        )
        ids = results.get('ids', [])
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Použitie: python indexer.py <repo_path> [--force]")
        sys.exit(1)

    repo_path = sys.argv[1]
    force = '--force' in sys.argv

    indexer = CodeIndexer(
        chroma_path="./chroma_db",
        ollama_url="http://localhost:11434",
        embed_model="nomic-embed-text"
    )

    count = indexer.index_repository(repo_path, force_reindex=force)

    stats = indexer.get_stats()
    print("\n=== Index Stats ===")
    print(f"Celkom chunkov: {stats['total_chunks']}")
    print("\nPodľa typu:")
    for t, n in sorted(stats['by_type'].items()):
        print(f"  {t}: {n}")
    print("\nPodľa jazyka:")
    for l, n in sorted(stats['by_language'].items()):
        print(f"  {l}: {n}")
    print("\nTop adresáre:")
    for d, n in stats['top_directories'][:5]:
        print(f"  {d}: {n}")
