# AI Code Assistant - Claude Instructions

## Project

AI assistant for legacy Sybase SQL / Python 2.7 / Perl (.pl) codebase analysis.
Stack: Qwen2.5-Coder-32B (Ollama) + ChromaDB + dependency graph on RTX 4090.
Conversational agent with streaming output, memory, and human-in-the-loop approval.

## Key Files

- `agent.py` (~1060 lines) - ReAct agent: LLM calls, tool parsing, streaming, conversation memory, adaptive prompts
- `cli.py` (~641 lines) - Interactive CLI with slash commands, streaming output, approval workflow
- `retriever.py` - Hybrid search: vector + dependency expansion
- `indexer.py` - ChromaDB indexer (nomic-embed-text embeddings)
- `chunker.py` - Semantic chunker (1 object = 1 chunk)
- `dependency_graph.py` - Directed dep graph (2577 nodes)
- `pl_file_parser.py` - SQL/Perl file parser
- `git_tools.py` (~454 lines) - Git operations (read-only + approve workflow + multi-file ChangeSet)
- `fkinstall_registry.py` - Fkinstall package registry (246 packages, 603 objects → JSON cache)
- `run.sh` - Launcher (activates own venv at `./venv/`)
- `DOCUMENTATION.md` - Full architecture docs, known issues, environment details

## Critical: Ollama CUDA Bug

Ollama 0.17.7 crashes with `Assertion 'found' failed` in CUDA sampling on RTX 4090 with Q4_K_M quantization when batch size is large. **Fix: `num_batch: 64` in API options.** Both warmup (`_ensure_llm_loaded`) and `_call_llm`/`_call_llm_stream` in `agent.py` must include this. See DOCUMENTATION.md for full analysis.

## Critical: Qwen Streaming Token Artifact

Qwen2.5-Coder-32B in Ollama streaming mode produces malformed tool call JSON due to `<|im_start|>` token boundary consuming the `"` before JSON keys. Result: `{name":"tool_name",...}` instead of `{"name":"tool_name",...}`. Also generates `<tool>`, `<{"name"`, markdown-wrapped tool calls. All variants handled in `_parse_tool_call()`.

## Architecture

- **ReAct loop**: max 15 iterations, early exit on 2x consecutive no-tool-call
- **Adaptive prompts**: 3 system prompt variants (analyze/change/search) selected by keyword detection
- **Conversation memory**: JSON per thread_id, sliding window, persisted in `conversations/`
- **Streaming**: `chat_stream()` with 80-char buffer, tool call pattern suppression
- **Anti-hallucination**: `_TOOL_CALL_RULES` in prompts, `_strip_after_tool_call()` in message history
- **Tool loop detection**: 3x same tool → inject hint to try different tool
- **Self-verification**: ast.parse() for Python, sqlglot for SQL, dependency graph check
- **Multi-file changes**: `propose_changeset()` with atomic rollback in git_tools.py
- **Fkinstall registry**: `locate_object()` + `get_package_info()` tools, domain knowledge in system prompts

## LLM Config

- `num_ctx: 16384` (unified warmup + inference)
- `num_predict: 2048`
- `num_batch: 64` (CUDA crash fix)
- `temperature: 0.3` for code/search, `0.7` for analysis
- `top_k: 40`, `top_p: 0.9`

## Environment

- Venv: `./venv/` (run.sh activates it, no manual activation needed)
- Models: `/workspace/data/ollama`
- ChromaDB: `./chroma_db/`
- Repo: `/workspace/repo` (not a git repo, filesystem-only access)
- Ollama: `http://localhost:11434`, version 0.17.7
- GPU: RTX 4090, CUDA 12.8
- Python: 3.11

## Conventions

- Language: Slovak comments/UI, English code identifiers
- LLM options must always include `num_batch: 64` to prevent CUDA crash
- All code changes require human approval via `propose_change` / `propose_changeset` tool
- Agent uses direct HTTP requests to Ollama (not langchain_ollama bindings)
- System prompt is pure text with `<tool_call>` XML format, no bind_tools()
- Run with `./venv/bin/python` not system python (chromadb and other deps in venv)
