#!/bin/bash
# setup.sh - Inštalácia AI Code Asistenta na RunPod (RTX 4090, Ubuntu)
# Spusti: bash /workspace/projects/ai-assistant/setup.sh /workspace/repo

set -e

REPO_PATH="${1:-/workspace/repo}"
ASSISTANT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ASSISTANT_DIR/venv"
OLLAMA_URL="http://localhost:11434"

# GPU: Ollama automaticky využíva CUDA ak je dostupná
export OLLAMA_FLASH_ATTENTION=1       # Flash Attention pre RTX 4090
export CUDA_VISIBLE_DEVICES=0         # Používaj prvú GPU

# Ollama modely do /workspace (väčší disk, nie root /home)
export OLLAMA_MODELS=/workspace/data/ollama
mkdir -p "$OLLAMA_MODELS"

# Persistentné nastavenie — pridaj do ~/.bashrc ak tam ešte nie je
if ! grep -q "OLLAMA_MODELS" ~/.bashrc 2>/dev/null; then
    echo "" >> ~/.bashrc
    echo "# Ollama modely do /workspace (väčší disk)" >> ~/.bashrc
    echo "export OLLAMA_MODELS=/workspace/data/ollama" >> ~/.bashrc
    echo "export OLLAMA_FLASH_ATTENTION=1" >> ~/.bashrc
    echo "  [OK] OLLAMA_MODELS pridaný do ~/.bashrc"
else
    echo "  [OK] OLLAMA_MODELS už v ~/.bashrc"
fi

echo "============================================"
echo " AI Code Assistant - Setup"
echo "============================================"
echo " Repo         : $REPO_PATH"
echo " Assistant dir: $ASSISTANT_DIR"
echo " Venv         : $VENV_DIR"
echo " Ollama models: $OLLAMA_MODELS"
echo ""

# Zobraziť GPU info
if command -v nvidia-smi &> /dev/null; then
    echo " GPU:"
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | sed 's/^/   /'
    echo ""
fi

# Zobraziť voľné miesto
echo " Disk /workspace:"
df -h /workspace | tail -1 | awk '{print "   Celkom: "$2, "Voľné: "$4}'
echo ""

# ---------------------------------------------------------------------------
# 1. Systémové závislosti
# ---------------------------------------------------------------------------
echo "[1/6] Systémové balíky..."
apt-get update -qq && apt-get install -y -qq \
    git \
    curl \
    wget \
    build-essential \
    python3-pip \
    python3-venv \
    sqlite3 \
    2>/dev/null || true

# ---------------------------------------------------------------------------
# 2. Ollama
# ---------------------------------------------------------------------------
echo "[2/6] Kontrolujem Ollama..."
if ! command -v ollama &> /dev/null; then
    echo "  Inštalujem Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "  Ollama už nainštalovaná: $(ollama --version)"
fi

# Reštartuj Ollamu aby prebehla s novým OLLAMA_MODELS
# (ak beží bez neho, sťahovala by do /root)
echo "  Reštartujem Ollama server (modely: $OLLAMA_MODELS)..."
pkill -9 ollama 2>/dev/null; sleep 3
OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve > /tmp/ollama.log 2>&1 &
OLLAMA_PID=$!
echo "  Ollama PID: $OLLAMA_PID"
sleep 5
# Overenie
if curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
    echo "  [OK] Ollama beží"
else
    echo "  [WARN] Ollama sa nespustila, skontroluj: cat /tmp/ollama.log"
fi

# ---------------------------------------------------------------------------
# 3. Stiahnutie modelov
# ---------------------------------------------------------------------------
echo ""
echo "[3/6] Sťahujem embedding model (nomic-embed-text)..."
echo "  Veľkosť: ~274 MB"
ollama pull nomic-embed-text
echo "  [OK] nomic-embed-text stiahnutý"

echo ""
echo "[3/6] Sťahujem LLM model (qwen2.5-coder:32b-instruct-q4_K_M)..."
echo "  Veľkosť: ~20 GB — môže trvať 20-40 minút"
echo "  Ukladám do: $OLLAMA_MODELS"
echo "  Progres:"
ollama pull qwen2.5-coder:32b-instruct-q4_K_M

echo ""
echo "  Dostupné modely:"
ollama list

# ---------------------------------------------------------------------------
# 4. Python virtual environment
# ---------------------------------------------------------------------------
echo "[4/6] Python virtual environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Upgrade pip
pip install --upgrade pip -q

# Inštalácia závislostí s progress
echo "  Inštalujem Python balíky (requirements.txt)..."
pip install -r "$ASSISTANT_DIR/requirements.txt" \
    --progress-bar on \
    2>&1 | grep -E "^(Collecting|Installing|Successfully|ERROR|error)" || true

# tqdm pre progress bary (ak nie je v requirements.txt)
pip install tqdm -q 2>/dev/null || true

echo ""
echo "  Verifikácia knižníc:"
python3 -c "import chromadb; print(f'  [OK] ChromaDB {chromadb.__version__}')"
python3 -c "import langgraph; v=getattr(langgraph,'__version__',None) or __import__('importlib.metadata',fromlist=['version']).version('langgraph'); print(f'  [OK] LangGraph {v}')"
python3 -c "import langchain_ollama; print(f'  [OK] LangChain-Ollama')"
python3 -c "import git; print(f'  [OK] GitPython {git.__version__}')"
python3 -c "import sqlglot; print(f'  [OK] SQLGlot {sqlglot.__version__}')"
python3 -c "import tqdm; print(f'  [OK] tqdm {tqdm.__version__}')"

# ---------------------------------------------------------------------------
# 5. Vytvor launcher skripty
# ---------------------------------------------------------------------------
echo "[5/6] Vytváram launcher skripty..."

# Hlavný launcher
cat > "$ASSISTANT_DIR/run.sh" << 'RUNEOF'
#!/bin/bash
# run.sh - Spustí AI Code Asistenta
ASSISTANT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$ASSISTANT_DIR/venv/bin/activate"
export OLLAMA_FLASH_ATTENTION=1
export CUDA_VISIBLE_DEVICES=0
export OLLAMA_MODELS=/workspace/data/ollama

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
RUNEOF
chmod +x "$ASSISTANT_DIR/run.sh"

# Index launcher (s progress)
cat > "$ASSISTANT_DIR/index.sh" << 'IDXEOF'
#!/bin/bash
# index.sh - Indexuje repozitár do ChromaDB
# Použitie: ./index.sh /workspace/repo [--force]
ASSISTANT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$ASSISTANT_DIR/venv/bin/activate"
export OLLAMA_FLASH_ATTENTION=1
export CUDA_VISIBLE_DEVICES=0
export OLLAMA_MODELS=/workspace/data/ollama

if ! curl -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    echo "Spúšťam Ollama server..."
    OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve > /tmp/ollama.log 2>&1 &
    sleep 5
fi

REPO_PATH="${1:-/workspace/repo}"
FORCE="${2:-}"
echo "============================================"
echo " Indexovanie: $REPO_PATH"
echo " GPU: CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
echo "============================================"
echo ""
python3 "$ASSISTANT_DIR/indexer.py" "$REPO_PATH" $FORCE
IDXEOF
chmod +x "$ASSISTANT_DIR/index.sh"

# ---------------------------------------------------------------------------
# 6. Systemd service (optional)
# ---------------------------------------------------------------------------
echo "[6/6] Vytváram systemd service pre Ollama..."
cat > /etc/systemd/system/ollama.service << 'EOF' 2>/dev/null || true
[Unit]
Description=Ollama AI Model Server
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=3
Environment=OLLAMA_ORIGINS=*
Environment=OLLAMA_HOST=0.0.0.0:11434
Environment=OLLAMA_MODELS=/workspace/data/ollama

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload 2>/dev/null || true
systemctl enable ollama 2>/dev/null || true
systemctl start ollama 2>/dev/null || true

# ---------------------------------------------------------------------------
# Hotovo
# ---------------------------------------------------------------------------
echo ""
echo "============================================"
echo " Setup DOKONČENÝ!"
echo "============================================"
echo ""
echo "Použitie:"
echo "  # Indexovanie repozitára:"
echo "  $ASSISTANT_DIR/index.sh $REPO_PATH"
echo ""
echo "  # Spustenie asistenta:"
echo "  $ASSISTANT_DIR/run.sh $REPO_PATH"
echo ""
echo "  # Alebo priamo:"
echo "  source $VENV_DIR/bin/activate"
echo "  python3 $ASSISTANT_DIR/cli.py $REPO_PATH"
echo ""
echo "Dostupné Ollama modely (v $OLLAMA_MODELS):"
ollama list
