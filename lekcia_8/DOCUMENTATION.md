# AI Code Assistant - FKTRM CSD Edition

## Overview

AI Code Assistant for analyzing and optimizing legacy Sybase SQL, Python 2.7, and Perl wrapper (.pl) codebases. Uses Qwen2.5-Coder-32B as the LLM via Ollama, ChromaDB for vector search, and a custom dependency graph for impact analysis.

**Target repo:** `/workspace/repo` (FKTRM CSD financial codebase)
**Runtime:** RunPod, RTX 4090 (24GB VRAM), Ubuntu, Python 3.11

---

## Architecture

```
User (CLI) <-> cli.py <-> agent.py (ReAct loop) <-> Ollama API
                              |                        |
                         Tools layer           qwen2.5-coder:32b-instruct-q4_K_M
                              |                  nomic-embed-text
                   +----------+----------+
                   |          |          |
              retriever.py  git_tools.py  dependency_graph.py
                   |
              indexer.py + chunker.py + pl_file_parser.py
                   |
              ChromaDB (local, persistent)
```

### Components

| File | Purpose |
|------|---------|
| `run.sh` | Main launcher. Activates venv, starts Ollama if needed, runs CLI |
| `setup.sh` | One-time setup: installs Ollama, models, Python deps, creates venv |
| `index.sh` | Indexes repository into ChromaDB |
| `cli.py` | Interactive CLI with slash commands, streaming output, human-in-the-loop approval |
| `agent.py` | ReAct agent: adaptive system prompts, iterative LLM calls, tool call parsing, conversation memory, streaming |
| `retriever.py` | Hybrid retriever: vector similarity + dependency graph expansion |
| `indexer.py` | ChromaDB indexer with incremental updates, content hashing |
| `chunker.py` | Semantic chunker: 1 SQL object/Python function/class = 1 chunk |
| `pl_file_parser.py` | Parses .pl/.sql/.ddl files, extracts SQL objects (procedures, tables, views) |
| `dependency_graph.py` | Directed graph: tracks what calls what, impact analysis, cycle detection |
| `git_tools.py` | Git operations: history, grep, file content, propose_change, multi-file ChangeSet with atomic rollback |

### Data Flow

1. **Indexing** (`index.sh` or `/index` command):
   - `chunker.py` parses repo files into semantic chunks (1 object = 1 chunk)
   - `pl_file_parser.py` handles SQL/Perl files specifically
   - `dependency_graph.py` builds directed graph from chunk dependencies
   - `indexer.py` embeds chunks via `nomic-embed-text` and stores in ChromaDB
   - Incremental: skips unchanged files (content hash comparison)

2. **Query** (user chat):
   - `agent.py` detects task type (analyze/change/search) and selects adaptive system prompt
   - Runs ReAct loop: LLM generates reasoning + tool calls (max 15 iterations)
   - Tool calls parsed from `<tool_call>{"name":"...","arguments":{...}}</tool_call>` format
   - `retriever.py` does vector search + dependency expansion
   - Results returned to LLM for next iteration or final answer
   - Streaming output: tokens displayed in real-time, tool call JSON suppressed
   - Anti-hallucination: strict rules prevent guessing tool results
   - Tool loop detection: prevents model from calling same tool repeatedly

3. **Change proposal**:
   - Single file: `propose_change(filepath, new_content, reason)`
   - Multi-file: `propose_changeset(changes, reason)` with atomic rollback
   - Self-verification: ast.parse() for Python, sqlglot for SQL, dependency graph check
   - Generates diff, waits for user `/approve` or `/reject`
   - Human-in-the-loop: no changes applied without explicit approval

4. **Conversation memory**:
   - Each conversation stored as JSON in `conversations/` directory per thread_id
   - Sliding window: keeps recent messages + system prompt
   - `/newchat` starts fresh conversation

---

## Models

| Model | Purpose | Size | VRAM |
|-------|---------|------|------|
| `qwen2.5-coder:32b-instruct-q4_K_M` | LLM (chat, reasoning, code) | 18.5 GB | ~21 GB with 16K KV cache |
| `nomic-embed-text` | Embeddings (768-dim) | 274 MB | ~300 MB |

Both run via Ollama on CUDA GPU. Models stored at `/workspace/data/ollama`.

### LLM Configuration

```python
"options": {
    "num_ctx": 16384,       # Context window (unified warmup + inference)
    "num_predict": 2048,    # Max output tokens
    "num_batch": 64,        # CRITICAL: prevents CUDA sampling crash
    "temperature": 0.3,     # 0.3 for code/search, 0.7 for analysis
    "top_k": 40,
    "top_p": 0.9,
}
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `/index [--force]` | Index repository (--force = reindex all) |
| `/stats` | Show index statistics and dependency graph info |
| `/search <query>` | Semantic vector search in code |
| `/file <path>` | Display file content from git |
| `/deps <name>` | Show dependency graph for an object |
| `/history <path>` | Git history of a file |
| `/grep <pattern>` | Fulltext search (git grep) |
| `/filter lang=sql` | Set filters for search (lang, type) |
| `/filter reset` | Clear filters |
| `/approve` | Approve proposed change |
| `/reject` | Reject proposed change |
| `/newchat` | Start new conversation (clear memory) |
| `/help` | Show help |
| `/exit` | Quit |

**Approval shortcuts:** `ano/yes/ok` = approve, `nie/no` = reject

---

## Agent Tools

The ReAct agent has these tools (called via `<tool_call>` XML in LLM output):

| Tool | Signature | Description |
|------|-----------|-------------|
| `search_codebase` | `(query, n_results=5, language=None, obj_type=None)` | Semantic search in ChromaDB |
| `get_file_content` | `(filepath, ref='HEAD')` | File content from git |
| `get_file_content_direct` | `(filepath)` | File content from filesystem (fallback) |
| `get_git_history` | `(filepath, max_commits=10)` | Git log for a file |
| `get_object_deps` | `(object_name, depth=2)` | Dependency graph lookup |
| `search_in_git` | `(pattern, file_pattern=None)` | Git grep |
| `get_recent_changes` | `(days=7)` | Recent git changes |
| `propose_change` | `(filepath, new_content, reason)` | Propose single-file change, waits for approval |
| `propose_changeset` | `(changes, reason)` | Propose multi-file change with atomic rollback |

---

## Known Issues and Fixes

### CRITICAL: Ollama 0.17.7 CUDA Sampling Crash

**Symptom:** `llama-sampling.cpp:660: Assertion 'found' failed` or `panic: failed to sample token`. Ollama runner crashes with HTTP 500 during inference.

**Root cause:** CUDA batch processing in Ollama 0.17.7 corrupts logits when `num_batch` is large (default 512) with `qwen2.5-coder:32b Q4_K_M` on RTX 4090. The corruption causes the token sampler to fail because no valid tokens remain after filtering.

**Evidence:**
- Works on CPU (`num_gpu: 0`) - confirmed
- Fails on GPU with default batch size - confirmed
- Works on GPU with `num_batch: 64` - confirmed 10/10 stable
- Simple prompts work, longer system prompts trigger the bug more often (probabilistic based on batch alignment)

**Fix applied in `agent.py`:**
```python
"options": {
    "num_ctx": 16384,
    "num_predict": 2048,
    "num_batch": 64,        # CRITICAL: prevents CUDA sampling crash
    "temperature": 0.3,
    "top_k": 40,
    "top_p": 0.9,
}
```

**What does NOT fix it:**
- `OLLAMA_FLASH_ATTENTION=0` or `=1` - no effect
- `OLLAMA_NEW_ENGINE=true` - different crash message, same root cause
- `OLLAMA_KV_CACHE_TYPE=f16` - no effect
- `GGML_CUDA_FORCE_CUBLAS=1` - not passed to runner subprocess
- Changing `temperature` alone - helps intermittently but not reliably
- Preloading model with simple prompt first - no effect

**What DOES fix it:**
- `num_batch: 64` (or 32) in API options - **the reliable fix**
- Running on CPU (`num_gpu: 0`) - works but too slow for 32B model
- Upgrading Ollama (not tested, version 0.17.7 is current)

**Performance impact:** `num_batch: 64` slightly slows prompt processing (initial tokenization) but does NOT affect token generation speed.

### CRITICAL: Qwen2.5-Coder Streaming Tool Call Artifact

**Symptom:** Tool calls fail to parse in streaming mode. JSON starts with `{name":"` instead of `{"name":"`.

**Root cause:** Qwen2.5-Coder-32B in Ollama streaming mode produces token sequence where `<|im_start|>` special token boundary consumes the `"` character before JSON key names. The joined output becomes `<|im_start|>{name":"search_codebase",...}` with a missing opening quote.

**Additional variants observed:**
- `<tool>{"name":...}` instead of `<tool_call>{"name":...}</tool_call>`
- `<{"name":...}` (angle bracket prefix)
- `` ```xml\n<{"name":...`` (markdown-wrapped)
- Double braces `{{"name":...}}` (model mimics Python format strings)

**Fix applied in `_parse_tool_call()` in `agent.py`:**
```python
cleaned = re.sub(r'<\|[^|]+\|>', '', text)      # Remove special tokens
cleaned = re.sub(r'<(\{"name")', r'\1', cleaned) # Remove angle bracket prefix
cleaned = re.sub(r'\{name":', '{"name":', cleaned)  # Fix missing quote
cleaned = re.sub(r'\{arguments":', '{"arguments":', cleaned)
```

**Non-streaming mode** (`_call_llm`) returns proper `<tool_call>{"name":...}</tool_call>` — no issue there.

### Anti-Hallucination: Model Guessing Tool Results

**Symptom:** Model generates fake code content, file paths, or table names without calling tools first.

**Fix:** Strict `_TOOL_CALL_RULES` injected into all system prompts:
- One tool call per response, stop immediately after closing tag
- Never guess/predict/assume tool results
- If tool returns nothing, try different tool (fallback strategy defined)
- `_strip_after_tool_call()` removes any text after `</tool_call>` from message history

### Tool Loop: Model Repeats Same Tool

**Symptom:** Model calls `search_codebase` 10+ times with similar queries without progress.

**Fix:** `recent_tools` list tracks last 3 tool names. If all 3 are the same, inject system hint suggesting alternative tools. Applied in both `chat()` and `chat_stream()`.

### Git Repository Warning

`/workspace/repo` is not a git repository. Git-based tools (history, grep, blame) will not work. The codebase is accessed via filesystem only. To fix: `cd /workspace/repo && git init && git add -A && git commit -m "initial"`.

### Ollama Startup in run.sh

`run.sh` checks if Ollama is running and only starts it if not. If Ollama crashes and restarts as a zombie/bad state, `run.sh` may connect to the broken instance. Kill manually with `pkill -f "ollama serve"` before restarting.

Ollama logs:
- `run.sh` writes to `/tmp/ollama.log` (stdout+stderr via nohup redirection)
- Check stderr separately: `ls -la /proc/$(pgrep -f "ollama serve")/fd/2`

### Known Streaming Tradeoff

Text before mid-response tool calls may leak to user display. When the model writes reasoning text followed by a tool call in the same response, the streaming buffer (80 chars) may flush the reasoning before the tool pattern is detected. This is inherent to real-time streaming; the leaked text is real reasoning, not hallucinated data.

---

## Environment

| Setting | Value |
|---------|-------|
| Platform | RunPod, Ubuntu, Linux 6.8 |
| GPU | NVIDIA RTX 4090, 24564 MiB VRAM |
| CUDA | 12.8, Driver 570.195 |
| Python | 3.11 (venv at `./venv/`) |
| Ollama | 0.17.7 |
| ChromaDB | Persistent local at `./chroma_db/` |
| Models | `/workspace/data/ollama` |

### Key Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `CUDA_VISIBLE_DEVICES` | `0` | Use first GPU |
| `OLLAMA_MODELS` | `/workspace/data/ollama` | Model storage path |
| `OLLAMA_FLASH_ATTENTION` | (commented out) | Was causing segfaults, left disabled |

---

## File Structure

```
/workspace/projects/ai-assistant/
  agent.py              # ReAct agent (LLM + tools + memory + streaming)
  cli.py                # Interactive CLI with streaming output
  chunker.py            # Semantic code chunker
  indexer.py            # ChromaDB indexer
  retriever.py          # Hybrid search (vector + deps)
  dependency_graph.py   # Directed dependency graph
  git_tools.py          # Git operations + multi-file ChangeSet
  pl_file_parser.py     # SQL/Perl parser
  run.sh                # Main launcher
  index.sh              # Index launcher
  setup.sh              # One-time setup
  requirements.txt      # Python dependencies
  DOCUMENTATION.md      # This file
  CLAUDE.md             # Claude Code instructions
  .gitignore            # Ignores conversations/, __pycache__/, *.pyc, *.bak
  venv/                 # Python virtual environment
  chroma_db/            # ChromaDB persistent storage
    dependency_graph.json # Serialized dependency graph (2577 nodes)
  conversations/        # Conversation memory (JSON per thread_id)
```

---

## Dependency Graph

The dependency graph tracks 2577 nodes (SQL objects, Python functions, Perl wrappers). Key features:

- **Forward deps:** `get_deps(name, depth)` - what does object X call?
- **Reverse deps:** `get_dependents(name, depth)` - who calls object X?
- **Impact analysis:** `get_impact(name)` - how many objects affected by changing X?
- **Cycle detection:** `find_cycles()` - circular dependency chains
- **Hub objects:** `get_hub_objects()` - most-referenced objects (high change risk)
- **Serialized** to `dependency_graph.json` in ChromaDB directory

---

## Indexing Details

- **Semantic chunking:** 1 SQL procedure/table/view = 1 chunk, 1 Python function/class = 1 chunk
- **Embeddings:** nomic-embed-text (768 dimensions) via Ollama API
- **Storage:** ChromaDB with cosine similarity, persistent local
- **Incremental:** Content hash comparison skips unchanged files
- **Current state:** 29 chunks indexed (partial index)
- **Supported extensions:** `.py`, `.pl`, `.sql`, `.ddl`, `.sp`

---

## Implementation Phases

### Phase 0: Quick Wins (DONE)
- num_ctx: 4096/8192 → 16384 (unified warmup + inference)
- Max iterations: 8 → 15, num_predict: 1024 → 2048
- Temperature: adaptive (0.3 code, 0.7 analysis)
- Smart truncation: 8000 chars at code block boundaries

### Phase 1: Memory and Planning (DONE)
- ConversationMemory with JSON persistence per thread_id
- Adaptive system prompts (3 variants by task type)
- Fixed approval workflow (direct apply, not LLM roundtrip)

### Phase 2: Multi-file and Self-verification (DONE)
- Multi-file ChangeSet with atomic rollback
- Self-verification (ast.parse, sqlglot, dependency check)
- Streaming output with tool call suppression
- Anti-hallucination rules and tool loop detection

### Phase 3: Advanced (NOT STARTED)
- Parallel tool calls (ThreadPoolExecutor)
- Embedding cache (LRU)
- Cross-language dependency tracking (Python → SP via dbcmd())
- Undo capability, change history log (JSON audit trail)
- Hybrid model: Qwen-32B for tool execution, cloud model for complex reasoning
