#!/bin/bash
# run_openwebui.sh - Spustí Ollama + API server + Open WebUI
# Zastaví všetko pri Ctrl+C
ASSISTANT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$ASSISTANT_DIR/venv/bin/activate"
export OLLAMA_FLASH_ATTENTION=1
export CUDA_VISIBLE_DEVICES=0
export OLLAMA_MODELS=/workspace/data/ollama
export HF_TOKEN=

cleanup() {
    echo ""
    echo "Zastavujem servisy..."
    [ -n "$OPENWEBUI_PID" ] && kill $OPENWEBUI_PID 2>/dev/null
    [ -n "$API_PID" ] && kill $API_PID 2>/dev/null
    wait 2>/dev/null
    echo "Hotovo."
    exit 0
}
trap cleanup INT TERM

# --- 1. Ollama ---
if ! curl -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    echo "Spúšťam Ollama server..."
    OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve > /tmp/ollama.log 2>&1 &
    sleep 5
    if curl -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
        echo "[OK] Ollama beží na :11434"
    else
        echo "[WARN] Ollama sa nespustila, skontroluj: cat /tmp/ollama.log"
    fi
else
    echo "[OK] Ollama už beží na :11434"
fi

# --- 2. API server (pozadie) ---
echo "Spúšťam API server na :8000..."
cd "$ASSISTANT_DIR"
uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 1 &
API_PID=$!

# Počkaj kým API server naštartuje
for i in $(seq 1 30); do
    if curl -s "http://localhost:8000/health" > /dev/null 2>&1; then
        echo "[OK] API server beží na :8000"
        break
    fi
    sleep 2
done

# --- 3. Open WebUI (popredie) ---
echo "Spúšťam Open WebUI na :3000..."
export OPENAI_API_BASE_URLS="http://localhost:8000/v1"
export OPENAI_API_KEYS="sk-dummy"
export DATA_DIR="/workspace/data/openwebui"
mkdir -p "$DATA_DIR"

open-webui serve --port 3000 &
OPENWEBUI_PID=$!

echo ""
echo "============================================"
echo " Všetko beží:"
echo "   Ollama:     http://localhost:11434"
echo "   API server: http://localhost:8000"
echo "   Open WebUI: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):3000"
echo ""
echo " Ctrl+C pre zastavenie"
echo "============================================"
echo ""

# Čakaj na ukončenie
wait
