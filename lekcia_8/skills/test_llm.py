#!/usr/bin/env python3
"""
test_llm.py - Test LLM inference with full system prompt
Usage: python3 skills/test_llm.py [--count 10] [--prompt "custom prompt"]

Tests that the LLM works reliably with the actual system prompt
used by the agent. Verifies the num_batch fix for CUDA crash.
"""

import argparse
import requests
import sys
import time

sys.path.insert(0, '.')
from agent import SYSTEM_PROMPT, TOOL_DESCRIPTIONS

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5-coder:32b-instruct-q4_K_M"


def test_inference(prompt: str, count: int = 5):
    tools_desc = '\n'.join(TOOL_DESCRIPTIONS.values())
    sp = SYSTEM_PROMPT.format(tools_description=tools_desc)

    ok = 0
    fail = 0

    for i in range(count):
        try:
            r = requests.post(f'{OLLAMA_URL}/api/chat', json={
                'model': MODEL,
                'messages': [
                    {'role': 'system', 'content': sp},
                    {'role': 'user', 'content': prompt},
                ],
                'stream': False,
                'options': {
                    'num_ctx': 4096,
                    'num_predict': 50,
                    'num_batch': 64,
                    'temperature': 0.7,
                    'top_k': 40,
                    'top_p': 0.9,
                },
            }, timeout=180)

            if r.status_code == 200:
                content = r.json()['message']['content'][:80]
                print(f'  Test {i+1}/{count}: OK - {content}')
                ok += 1
            else:
                print(f'  Test {i+1}/{count}: FAIL HTTP {r.status_code}')
                fail += 1
                time.sleep(5)
        except Exception as e:
            print(f'  Test {i+1}/{count}: ERROR - {e}')
            fail += 1
            time.sleep(5)

    print(f'\n  Results: {ok}/{count} passed, {fail}/{count} failed')
    return fail == 0


def main():
    parser = argparse.ArgumentParser(description='Test LLM inference')
    parser.add_argument('--count', type=int, default=5, help='Number of tests')
    parser.add_argument('--prompt', default='Say hello in Slovak', help='Test prompt')
    args = parser.parse_args()

    print(f'=== LLM Inference Test ===')
    print(f'Model: {MODEL}')
    print(f'Prompt: {args.prompt}')
    print(f'Count: {args.count}')
    print()

    success = test_inference(args.prompt, args.count)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
