#!/bin/bash
# run_api.sh - Spustí API server pre Open WebUI
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
    echo "Ollama beží"
fi

echo "Spúšťam API server na :8000..."
uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 1
