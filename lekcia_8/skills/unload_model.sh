#!/bin/bash
# unload_model.sh - Unload LLM from GPU to free VRAM
# Usage: ./skills/unload_model.sh

echo "=== Unloading model from GPU ==="

echo "Before:"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null

curl -s http://localhost:11434/api/generate \
    -d '{"model":"qwen2.5-coder:32b-instruct-q4_K_M","keep_alive":0}' \
    --max-time 10 > /dev/null 2>&1

sleep 3

echo "After:"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null

echo "=== Done ==="
