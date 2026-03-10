#!/bin/bash
# setup.sh - Inštalácia AI Code Asistenta na RunPod (RTX 4090, Ubuntu)
#
# Použitie:
#   bash setup.sh              # Interaktívne menu
#   bash setup.sh 1            # Len základné balíky
#   bash setup.sh 2            # Len Claude Code
#   bash setup.sh 3            # Len AI asistent (Ollama + Python + modely)
#   bash setup.sh 4            # Len Open WebUI + API Server
#   bash setup.sh 1 2 3        # Základné balíky + Claude Code + AI asistent
#   bash setup.sh all          # Všetko po sebe (1 2 3 4)

set -e

REPO_PATH="${REPO_PATH:-/workspace/repo}"
ASSISTANT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ASSISTANT_DIR/venv"
OLLAMA_URL="http://localhost:11434"

# GPU a Ollama nastavenia
export OLLAMA_FLASH_ATTENTION=1
export CUDA_VISIBLE_DEVICES=0
export OLLAMA_MODELS=/workspace/data/ollama

# ============================================================================
# Farby
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "  ${RED}[ERROR]${NC} $1"; }
info() { echo -e "  ${CYAN}$1${NC}"; }
header() {
    echo ""
    echo -e "${BOLD}${BLUE}============================================${NC}"
    echo -e "${BOLD}${BLUE} $1${NC}"
    echo -e "${BOLD}${BLUE}============================================${NC}"
}
step() { echo -e "\n${BOLD}${CYAN}[$1]${NC} $2"; }

# ============================================================================
# Funkcie pre jednotlivé kroky
# ============================================================================

show_header() {
    header "AI Code Assistant - Setup"
    echo -e " Repo         : ${BOLD}$REPO_PATH${NC}"
    echo -e " Assistant dir: ${BOLD}$ASSISTANT_DIR${NC}"
    echo ""

    if command -v nvidia-smi &> /dev/null; then
        echo -e " ${GREEN}GPU:${NC}"
        nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | sed 's/^/   /'
        echo ""
    fi

    echo -e " ${CYAN}Disk /workspace:${NC}"
    df -h /workspace 2>/dev/null | tail -1 | awk '{print "   Celkom: "$2, "Voľné: "$4}'
    echo ""
}

# ---------------------------------------------------------------------------
# Bod 1: Základné systémové balíky
# ---------------------------------------------------------------------------
step_base() {
    header "[1] Základné systémové balíky"
    info "Inštalujem: apt update, less, vim, screen, rsync ..."
    apt-get update -qq
    apt-get install -y -qq \
        less \
        vim \
        screen \
        curl \
        wget \
        git \
        rsync \
        build-essential \
        2>/dev/null || true
    ok "Základné balíky nainštalované"
}

# ---------------------------------------------------------------------------
# Bod 2: Claude Code
# ---------------------------------------------------------------------------
step_claude_code() {
    header "[2] Claude Code"
    if command -v claude &> /dev/null; then
        ok "Claude Code už nainštalovaný: $(claude --version 2>/dev/null || echo 'verzia neznáma')"
        info "Preskakujem inštaláciu."
    else
        info "Inštalujem Claude Code..."
        curl -fsSL https://claude.ai/install.sh | bash
        ok "Claude Code nainštalovaný"
    fi
}

# ---------------------------------------------------------------------------
# Bod 3: AI Asistent (Ollama, modely, Python venv, launcher skripty)
# ---------------------------------------------------------------------------
step_ai_assistant() {
    header "[3] AI Asistent (Ollama + Python + modely)"

    # --- Ollama MODELS dir ---
    mkdir -p "$OLLAMA_MODELS"
    if ! grep -q "OLLAMA_MODELS" ~/.bashrc 2>/dev/null; then
        echo "" >> ~/.bashrc
        echo "# Ollama modely do /workspace (väčší disk)" >> ~/.bashrc
        echo "export OLLAMA_MODELS=/workspace/data/ollama" >> ~/.bashrc
        echo "export OLLAMA_FLASH_ATTENTION=1" >> ~/.bashrc
        ok "OLLAMA_MODELS pridaný do ~/.bashrc"
    else
        ok "OLLAMA_MODELS už v ~/.bashrc"
    fi

    # --- Systémové závislosti pre Python ---
    step "3.1" "Systémové balíky pre Python..."
    apt-get update -qq && apt-get install -y -qq \
        python3-pip \
        python3-venv \
        sqlite3 \
        zstd \
        pciutils \
        lshw \
        2>/dev/null || true

    # --- Ollama ---
    step "3.2" "Kontrolujem Ollama..."
    if ! command -v ollama &> /dev/null; then
        info "Inštalujem Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
    else
        ok "Ollama už nainštalovaná: $(ollama --version)"
    fi

    info "Reštartujem Ollama server (modely: $OLLAMA_MODELS)..."
    pkill -9 ollama 2>/dev/null || true; sleep 3
    OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve > /tmp/ollama.log 2>&1 &
    OLLAMA_PID=$!
    info "Ollama PID: $OLLAMA_PID"
    sleep 5
    if curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        ok "Ollama beží"
    else
        warn "Ollama sa nespustila, skontroluj: cat /tmp/ollama.log"
    fi

    # --- Modely ---
    step "3.3" "Sťahujem embedding model (nomic-embed-text)..."
    info "Veľkosť: ~274 MB"
    ollama pull nomic-embed-text
    ok "nomic-embed-text stiahnutý"

    step "3.3" "Sťahujem LLM model (qwen2.5-coder:32b-instruct-q4_K_M)..."
    info "Veľkosť: ~20 GB — môže trvať 20-40 minút"
    info "Ukladám do: $OLLAMA_MODELS"
    ollama pull qwen2.5-coder:32b-instruct-q4_K_M

    echo ""
    info "Dostupné modely:"
    ollama list

    # --- Python venv ---
    step "3.4" "Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"

    pip install --upgrade pip -q

    info "Inštalujem Python balíky (requirements.txt)..."
    pip install -r "$ASSISTANT_DIR/requirements.txt" \
        --progress-bar on \
        2>&1 | grep -E "^(Collecting|Installing|Successfully|ERROR|error)" || true

    pip install tqdm -q 2>/dev/null || true

    echo ""
    echo -e "  ${BOLD}Verifikácia knižníc:${NC}"
    python3 -c "import chromadb; print(f'  \033[0;32m[OK]\033[0m ChromaDB {chromadb.__version__}')"
    python3 -c "import langgraph; v=getattr(langgraph,'__version__',None) or __import__('importlib.metadata',fromlist=['version']).version('langgraph'); print(f'  \033[0;32m[OK]\033[0m LangGraph {v}')"
    python3 -c "import langchain_ollama; print(f'  \033[0;32m[OK]\033[0m LangChain-Ollama')"
    python3 -c "import git; print(f'  \033[0;32m[OK]\033[0m GitPython {git.__version__}')"
    python3 -c "import sqlglot; print(f'  \033[0;32m[OK]\033[0m SQLGlot {sqlglot.__version__}')"
    python3 -c "import tqdm; print(f'  \033[0;32m[OK]\033[0m tqdm {tqdm.__version__}')"

    # --- Launcher skripty ---
    step "3.5" "Vytváram launcher skripty..."

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

    # --- Systemd service ---
    step "3.6" "Vytváram systemd service pre Ollama..."
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

    ok "AI Asistent setup dokončený"
}

# ---------------------------------------------------------------------------
# Bod 4: Open WebUI + API Server
# ---------------------------------------------------------------------------
step_open_webui() {
    header "[4] Open WebUI + API Server"

    # --- Kontrola venv ---
    if [ ! -d "$VENV_DIR" ]; then
        err "Python venv neexistuje ($VENV_DIR). Najprv spusti krok 3."
        return 1
    fi
    source "$VENV_DIR/bin/activate"

    # --- 4.1 Inštalácia balíkov ---
    step "4.1" "Inštalujem fastapi, uvicorn, open-webui..."
    pip install fastapi "uvicorn[standard]" open-webui \
        --progress-bar on \
        2>&1 | grep -E "^(Collecting|Installing|Successfully|ERROR|error)" || true

    echo -e "  ${BOLD}Verifikácia:${NC}"
    python3 -c "import fastapi; print(f'  \033[0;32m[OK]\033[0m FastAPI {fastapi.__version__}')"
    python3 -c "import uvicorn; print(f'  \033[0;32m[OK]\033[0m Uvicorn')"
    python3 -c "
try:
    from importlib.metadata import version
    v = version('open-webui')
    print(f'  \033[0;32m[OK]\033[0m Open WebUI {v}')
except Exception:
    print('  \033[0;32m[OK]\033[0m Open WebUI (verzia neznáma)')
"

    # --- 4.2 Launcher skript ---
    step "4.2" "Vytváram run_api.sh launcher..."
    cat > "$ASSISTANT_DIR/run_api.sh" << 'APIEOF'
#!/bin/bash
# run_api.sh - Spustí API server pre Open WebUI
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
    echo "Ollama beží"
fi

echo "Spúšťam API server na :8000..."
uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 1
APIEOF
    chmod +x "$ASSISTANT_DIR/run_api.sh"
    ok "run_api.sh vytvorený"

    # --- 4.3 Systemd service pre API server ---
    step "4.3" "Vytváram systemd service pre API server..."
    cat > /etc/systemd/system/ai-assistant-api.service << EOF 2>/dev/null || true
[Unit]
Description=AI Code Assistant API Server
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=root
WorkingDirectory=$ASSISTANT_DIR
ExecStart=$VENV_DIR/bin/uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5
Environment=OLLAMA_MODELS=/workspace/data/ollama
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=CUDA_VISIBLE_DEVICES=0

[Install]
WantedBy=multi-user.target
EOF
    ok "ai-assistant-api.service vytvorený"

    # --- 4.4 Systemd service pre Open WebUI ---
    step "4.4" "Vytváram systemd service pre Open WebUI..."
    mkdir -p /workspace/data/openwebui
    cat > /etc/systemd/system/open-webui.service << EOF 2>/dev/null || true
[Unit]
Description=Open WebUI
After=network.target ai-assistant-api.service
Wants=ai-assistant-api.service

[Service]
Type=simple
User=root
ExecStart=$VENV_DIR/bin/open-webui serve --port 3000
Restart=always
RestartSec=5
Environment=OPENAI_API_BASE_URLS=http://localhost:8000/v1
Environment=OPENAI_API_KEYS=sk-dummy
Environment=DATA_DIR=/workspace/data/openwebui

[Install]
WantedBy=multi-user.target
EOF
    ok "open-webui.service vytvorený"

    # --- 4.5 Spustenie servisov ---
    step "4.5" "Spúšťam servisy..."
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable ai-assistant-api 2>/dev/null || true
    systemctl enable open-webui 2>/dev/null || true
    systemctl start ai-assistant-api 2>/dev/null || true
    sleep 3
    systemctl start open-webui 2>/dev/null || true

    if systemctl is-active --quiet ai-assistant-api 2>/dev/null; then
        ok "API server beží na :8000"
    else
        warn "API server sa nespustil. Skontroluj: journalctl -u ai-assistant-api"
        info "Manuálny štart: $ASSISTANT_DIR/run_api.sh"
    fi

    if systemctl is-active --quiet open-webui 2>/dev/null; then
        ok "Open WebUI beží na :3000"
    else
        warn "Open WebUI sa nespustilo. Skontroluj: journalctl -u open-webui"
        info "Manuálny štart: $VENV_DIR/bin/open-webui serve --port 3000"
    fi

    # --- 4.6 Inštrukcie ---
    step "4.6" "Konfigurácia"
    echo ""
    echo -e "  ${BOLD}Open WebUI:${NC} http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):3000"
    echo -e "  ${BOLD}API Server:${NC} http://localhost:8000"
    echo ""
    echo -e "  ${YELLOW}Prvá konfigurácia Open WebUI:${NC}"
    echo -e "  1. Otvor URL vyššie v prehliadači"
    echo -e "  2. Vytvor admin účet"
    echo -e "  3. Model ${BOLD}ai-code-assistant${NC} by mal byť dostupný automaticky"
    echo -e "     (cez env OPENAI_API_BASE_URLS v systemd service)"
    echo ""

    ok "Open WebUI setup dokončený"
}

# ============================================================================
# Interaktívne menu
# ============================================================================

show_menu() {
    header "Vyber kroky na inštaláciu"
    echo ""
    echo -e "  ${BOLD}1)${NC} Základné balíky (apt update, less, vim, screen)"
    echo -e "  ${BOLD}2)${NC} Claude Code (claude.ai/install.sh)"
    echo -e "  ${BOLD}3)${NC} AI Asistent (Ollama, modely, Python venv, skripty)"
    echo -e "  ${BOLD}4)${NC} Open WebUI + API Server (web rozhranie)"
    echo ""
    echo -e "  ${GREEN}${BOLD}a)${NC} Všetko (1 + 2 + 3 + 4)"
    echo -e "  ${RED}${BOLD}q)${NC} Ukončiť"
    echo ""
    echo -ne " ${YELLOW}Zadaj voľbu (napr. 1 2 3, alebo a):${NC} "
}

run_steps() {
    for step in "$@"; do
        case "$step" in
            1) step_base ;;
            2) step_claude_code ;;
            3) step_ai_assistant ;;
            4) step_open_webui ;;
            *)
                warn "Neznámy krok: $step (preskakujem)"
                ;;
        esac
    done
}

# ============================================================================
# Hlavná logika
# ============================================================================

show_header

# Ak boli zadané argumenty z command line
if [ $# -gt 0 ]; then
    # Spracuj argumenty
    args=()
    for arg in "$@"; do
        case "$arg" in
            all|a|A)
                args=(1 2 3 4)
                break
                ;;
            1|2|3|4)
                args+=("$arg")
                ;;
            *)
                warn "Neznámy argument: $arg"
                echo ""
                echo -e "${BOLD}Použitie:${NC} bash setup.sh [1] [2] [3] [4] [all]"
                echo -e "  ${BOLD}1${NC}   - Základné balíky (apt update, less, vim, screen)"
                echo -e "  ${BOLD}2${NC}   - Claude Code"
                echo -e "  ${BOLD}3${NC}   - AI Asistent (Ollama + Python + modely)"
                echo -e "  ${BOLD}4${NC}   - Open WebUI + API Server"
                echo -e "  ${BOLD}all${NC} - Všetko"
                exit 1
                ;;
        esac
    done

    if [ ${#args[@]} -eq 0 ]; then
        echo "Žiadne platné kroky."
        exit 1
    fi

    # Deduplikuj a zoraď
    sorted=($(echo "${args[@]}" | tr ' ' '\n' | sort -u))
    echo -e " ${BOLD}Spúšťam kroky: ${CYAN}${sorted[*]}${NC}"
    run_steps "${sorted[@]}"
else
    # Interaktívne menu
    show_menu
    read -r choice

    case "$choice" in
        q|Q)
            echo "Ukončené."
            exit 0
            ;;
        a|A)
            run_steps 1 2 3 4
            ;;
        *)
            # Rozdeľ vstup na jednotlivé čísla
            steps=($choice)
            if [ ${#steps[@]} -eq 0 ]; then
                echo "Žiadna voľba. Ukončené."
                exit 0
            fi
            # Deduplikuj a zoraď
            sorted=($(echo "${steps[@]}" | tr ' ' '\n' | sort -u))
            run_steps "${sorted[@]}"
            ;;
    esac
fi

echo ""
echo -e "${BOLD}${GREEN}============================================${NC}"
echo -e "${BOLD}${GREEN} Setup DOKONČENÝ!${NC}"
echo -e "${BOLD}${GREEN}============================================${NC}"
echo ""
echo -e "${BOLD}Ďalšie kroky:${NC}"
echo ""
echo -e "  ${CYAN}# Indexovanie repozitára:${NC}"
echo -e "  ${BOLD}$ASSISTANT_DIR/index.sh $REPO_PATH${NC}"
echo ""
echo -e "  ${CYAN}# Spustenie asistenta:${NC}"
echo -e "  ${BOLD}$ASSISTANT_DIR/run.sh $REPO_PATH${NC}"
echo ""
echo -e "  ${CYAN}# Alebo priamo:${NC}"
echo -e "  ${BOLD}source $VENV_DIR/bin/activate${NC}"
echo -e "  ${BOLD}python3 $ASSISTANT_DIR/cli.py $REPO_PATH${NC}"
echo ""
echo -e "  ${CYAN}# API server (pre Open WebUI):${NC}"
echo -e "  ${BOLD}$ASSISTANT_DIR/run_api.sh${NC}"
echo ""
echo -e "  ${CYAN}# Open WebUI:${NC}"
echo -e "  ${BOLD}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):3000${NC}"
echo ""
if command -v ollama &> /dev/null; then
    echo -e "${CYAN}Dostupné Ollama modely (v $OLLAMA_MODELS):${NC}"
    ollama list 2>/dev/null || true
fi
echo ""
