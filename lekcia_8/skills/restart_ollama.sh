#!/bin/bash
# restart_ollama.sh - Clean restart of Ollama server
# Usage: ./skills/restart_ollama.sh

echo "=== Restarting Ollama ==="

# 1. Kill existing
echo "[1] Stopping Ollama..."
pkill -f "ollama serve" 2>/dev/null
sleep 3

if pgrep -a ollama > /dev/null 2>&1; then
    echo "  Force killing..."
    pkill -9 -f "ollama serve" 2>/dev/null
    sleep 2
fi

# 2. Verify GPU is free
echo "[2] Checking GPU..."
VRAM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null | tr -d ' ')
echo "  VRAM used: $VRAM"

# 3. Start fresh
echo "[3] Starting Ollama..."
export OLLAMA_MODELS=/workspace/data/ollama
export CUDA_VISIBLE_DEVICES=0

OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve > /tmp/ollama.log 2>&1 &
sleep 5

# 4. Verify
if curl -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    echo "  [OK] Ollama running (PID: $(pgrep -f 'ollama serve'))"
    echo ""
    echo "  Models:"
    curl -s "http://localhost:11434/api/tags" | python3 -c "
import sys,json
for m in json.load(sys.stdin).get('models',[]):
    print(f'    {m[\"name\"]}')" 2>/dev/null
else
    echo "  [FAIL] Ollama did not start. Check: tail -20 /tmp/ollama.log"
fi
echo ""
echo "=== Done ==="
