"""
api_server.py
OpenAI-compatible API server wrapping CodeAssistantAgent.
Open WebUI sa pripojí k tomuto serveru ako "OpenAI" endpoint.

Spustenie:
  ./venv/bin/uvicorn api_server:app --host 0.0.0.0 --port 8000
"""

import hashlib
import json
import queue
import threading
import time
import uuid
from typing import Optional

import requests as http_requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent import CodeAssistantAgent

# ============================================================================
# Konfigurácia
# ============================================================================

REPO_PATH = "/workspace/repo"
CHROMA_PATH = "./chroma_db"
OLLAMA_URL = "http://localhost:11434"
LLM_MODEL = "qwen2.5-coder:32b-instruct-q4_K_M"
EMBED_MODEL = "nomic-embed-text"
MODEL_ID = "ai-code-assistant"

# ============================================================================
# FastAPI app
# ============================================================================

app = FastAPI(title="AI Code Assistant API", version="1.0.0")

# Globálny agent — inicializácia pri štarte
_agent: Optional[CodeAssistantAgent] = None
_agent_lock = threading.Lock()  # Len 1 request naraz (single GPU)


@app.on_event("startup")
def startup():
    global _agent
    print("[api] Inicializujem CodeAssistantAgent...", flush=True)
    _agent = CodeAssistantAgent(
        repo_path=REPO_PATH,
        chroma_path=CHROMA_PATH,
        ollama_url=OLLAMA_URL,
        llm_model=LLM_MODEL,
        embed_model=EMBED_MODEL,
    )
    # LLM warmup
    _agent._ensure_llm_loaded()
    print("[api] Agent ready.", flush=True)


# ============================================================================
# Helpers
# ============================================================================

def _thread_id_from_messages(messages: list) -> str:
    """Stabilný thread_id z prvej user správy v konverzácii."""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return hashlib.sha256(content.encode()).hexdigest()[:16]
    return "default"


def _make_chunk(content: str, model: str, finish_reason: Optional[str] = None) -> str:
    """Vytvorí SSE chunk v OpenAI streaming formáte."""
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": content} if content else {},
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"


# ============================================================================
# Endpointy
# ============================================================================

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "agent_loaded": _agent is not None}


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{
            "id": MODEL_ID,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "ai-code-assistant",
        }],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if not messages:
        return JSONResponse(status_code=400, content={"error": "messages required"})

    # Posledná user správa
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    if not user_message:
        return JSONResponse(status_code=400, content={"error": "no user message found"})

    # Open WebUI posiela interné "Task" requesty (titulky, tagy, follow-up)
    # Tieto nepotrebujú RAG/tools — odpovieme priamo cez Ollama
    if user_message.strip().startswith("### Task:"):
        return await _passthrough_to_ollama(messages, stream)

    # System prompt z Open WebUI — pridaj ako kontext pred user správu
    system_prefix = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_prefix = msg.get("content", "").strip()
            break
    if system_prefix:
        user_message = f"[Používateľské inštrukcie]: {system_prefix}\n\n{user_message}"

    thread_id = _thread_id_from_messages(messages)

    if stream:
        return StreamingResponse(
            _stream_response(user_message, thread_id),
            media_type="text/event-stream",
        )
    else:
        return _sync_response(user_message, thread_id)


async def _passthrough_to_ollama(messages: list, stream: bool):
    """
    Presmeruje interné Open WebUI requesty (Task: titulky, tagy, follow-up)
    priamo na Ollama bez RAG/tools. Nepoužíva agent, neukladá do pamäte.
    """
    try:
        r = http_requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"num_ctx": 4096, "num_predict": 256, "num_batch": 64,
                            "temperature": 0.7},
            },
            timeout=60,
        )
        content = r.json().get("message", {}).get("content", "")
    except Exception as e:
        content = f"Error: {e}"

    return JSONResponse(content={
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


def _sync_response(user_message: str, thread_id: str) -> JSONResponse:
    """Non-streaming odpoveď."""
    with _agent_lock:
        result = _agent.chat(user_message, thread_id=thread_id)

    return JSONResponse(content={
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


async def _stream_response(user_message: str, thread_id: str):
    """
    Streaming odpoveď cez SSE v OpenAI formáte.
    Používa chat() (non-streaming) aby sa vyhol tool call noise,
    potom výsledok streamuje po častiach.
    """
    result_queue: queue.Queue = queue.Queue()
    SENTINEL = object()
    CHUNK_SIZE = 20  # znakov na chunk — plynulý streaming efekt

    def _run():
        try:
            with _agent_lock:
                print(f"[api] chat() start: {user_message[:60]}", flush=True)
                result = _agent.chat(user_message, thread_id=thread_id)
                print(f"[api] chat() done: {len(result)} chars", flush=True)
            # Streamuj výsledok po častiach
            for i in range(0, len(result), CHUNK_SIZE):
                result_queue.put(result[i:i + CHUNK_SIZE])
        except Exception as e:
            print(f"[api] chat() error: {e}", flush=True)
            result_queue.put(f"\n[Error: {e}]")
        finally:
            result_queue.put(SENTINEL)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    while True:
        try:
            token = result_queue.get(timeout=120)
        except queue.Empty:
            break

        if token is SENTINEL:
            break

        yield _make_chunk(token, MODEL_ID)

    # Finálny chunk
    yield _make_chunk("", MODEL_ID, finish_reason="stop")
    yield "data: [DONE]\n\n"


# ============================================================================
# Standalone run
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
