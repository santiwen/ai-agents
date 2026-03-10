#!/bin/bash
# index.sh - Indexuje repozitár do ChromaDB
# Použitie: ./index.sh /workspace/repo [--force]
ASSISTANT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$ASSISTANT_DIR/venv/bin/activate"
export OLLAMA_FLASH_ATTENTION=1
export CUDA_VISIBLE_DEVICES=0
export OLLAMA_MODELS=/workspace/data/ollama

if ! curl -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    echo "Spúšťam Ollama server..."
    OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve > /tmp/ollama.log 2>&1 &
    sleep 5
fi

REPO_PATH="${1:-/workspace/repo}"
FORCE="${2:-}"
echo "============================================"
echo " Indexovanie: $REPO_PATH"
echo " GPU: CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
echo "============================================"
echo ""
python3 "$ASSISTANT_DIR/indexer.py" "$REPO_PATH" $FORCE
