#!/bin/bash
# run.sh - Spustí AI Code Asistenta
ASSISTANT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$ASSISTANT_DIR/venv/bin/activate"
export OLLAMA_FLASH_ATTENTION=1
export CUDA_VISIBLE_DEVICES=0
export OLLAMA_MODELS=/workspace/data/ollama
export HF_TOKEN=

# Skontroluj Ollama
if ! curl -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    echo "Spúšťam Ollama server..."
    OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve > /tmp/ollama.log 2>&1 &
    sleep 5
    echo "Ollama bežia"
fi

REPO_PATH="${1:-/workspace/repo}"
echo "Spúšťam asistenta pre repo: $REPO_PATH"
python3 "$ASSISTANT_DIR/cli.py" "$REPO_PATH" \
    --ollama "http://localhost:11434" \
    --model "qwen2.5-coder:32b-instruct-q4_K_M" \
    --embed-model "nomic-embed-text" \
    --chroma "$ASSISTANT_DIR/chroma_db"
