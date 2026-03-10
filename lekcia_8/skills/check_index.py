#!/usr/bin/env python3
"""
check_index.py - Check ChromaDB index health and statistics
Usage: python3 skills/check_index.py [chroma_path]
"""

import sys
sys.path.insert(0, '.')

from pathlib import Path


def main():
    chroma_path = sys.argv[1] if len(sys.argv) > 1 else './chroma_db'

    print(f'=== ChromaDB Index Check ===')
    print(f'Path: {chroma_path}')
    print()

    # Check if path exists
    p = Path(chroma_path)
    if not p.exists():
        print('ERROR: ChromaDB path does not exist. Run /index first.')
        sys.exit(1)

    # Check dependency graph
    dep_file = p / 'dependency_graph.json'
    if dep_file.exists():
        import json
        with open(dep_file) as f:
            data = json.load(f)
        nodes = data.get('nodes', {})
        print(f'Dependency graph: {len(nodes)} nodes')

        # Count by type
        by_type = {}
        for name, node in nodes.items():
            t = node.get('obj_type', 'unknown')
            by_type[t] = by_type.get(t, 0) + 1
        for t, n in sorted(by_type.items(), key=lambda x: -x[1])[:10]:
            print(f'  {t}: {n}')
        print()
    else:
        print('No dependency graph found.')

    # Check ChromaDB
    try:
        import chromadb
        from chromadb.config import Settings
        client = chromadb.PersistentClient(
            path=str(chroma_path),
            settings=Settings(anonymized_telemetry=False)
        )
        col = client.get_or_create_collection('code_objects')
        count = col.count()
        print(f'ChromaDB chunks: {count}')

        if count > 0:
            sample = col.get(limit=min(count, 500), include=['metadatas'])
            by_lang = {}
            by_type = {}
            for meta in sample.get('metadatas', []):
                lang = meta.get('language', '?')
                typ = meta.get('obj_type', '?')
                by_lang[lang] = by_lang.get(lang, 0) + 1
                by_type[typ] = by_type.get(typ, 0) + 1

            print('\nBy language:')
            for l, n in sorted(by_lang.items(), key=lambda x: -x[1]):
                print(f'  {l}: {n}')

            print('\nBy type:')
            for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
                print(f'  {t}: {n}')
        else:
            print('Index is empty. Run: ./index.sh /workspace/repo')

    except ImportError:
        print('ERROR: chromadb not installed. Activate venv first.')
    except Exception as e:
        print(f'ERROR: {e}')

    print('\n=== Done ===')


if __name__ == '__main__':
    main()
