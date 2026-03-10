#!/bin/bash
# diagnose_ollama.sh - Diagnose Ollama health and GPU state
# Usage: ./skills/diagnose_ollama.sh

echo "=== Ollama Diagnostics ==="
echo ""

# 1. Process status
echo "[1] Ollama process:"
if pgrep -a ollama > /dev/null 2>&1; then
    pgrep -a ollama
    PID=$(pgrep -f "ollama serve" | head -1)
    echo "  Stderr -> $(ls -la /proc/$PID/fd/2 2>/dev/null | awk '{print $NF}')"
else
    echo "  NOT RUNNING"
fi
echo ""

# 2. API health
echo "[2] API health:"
if curl -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    echo "  API: OK"
    curl -s "http://localhost:11434/api/tags" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for m in d.get('models',[]):
    print(f'  Model: {m[\"name\"]} ({m.get(\"size\",0)//1024//1024} MB)')
" 2>/dev/null
else
    echo "  API: UNREACHABLE"
fi
echo ""

# 3. GPU status
echo "[3] GPU status:"
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null
else
    echo "  nvidia-smi not available"
fi
echo ""

# 4. Recent errors in logs
echo "[4] Recent errors (last 10):"
for logfile in /tmp/ollama.log /tmp/ollama_final.log; do
    if [ -f "$logfile" ]; then
        ERRS=$(grep -c "Assertion\|panic\|error\|FAIL" "$logfile" 2>/dev/null || echo 0)
        echo "  $logfile: $ERRS errors total"
        grep -i "Assertion\|panic" "$logfile" 2>/dev/null | tail -3 | sed 's/^/    /'
    fi
done
echo ""

# 5. Quick inference test
echo "[5] Quick inference test (simple prompt):"
RESULT=$(curl -s http://localhost:11434/api/chat \
    -d '{"model":"qwen2.5-coder:32b-instruct-q4_K_M","messages":[{"role":"user","content":"hi"}],"stream":false,"options":{"num_ctx":2048,"num_predict":5,"num_batch":64}}' \
    --max-time 120 2>&1)
STATUS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK:', d.get('message',{}).get('content','')[:50])" 2>/dev/null)
if [ -n "$STATUS" ]; then
    echo "  $STATUS"
else
    echo "  FAILED: $RESULT" | head -c 200
fi
echo ""

echo "=== Done ==="
