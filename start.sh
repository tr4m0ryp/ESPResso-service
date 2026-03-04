#!/usr/bin/env bash
#
# ESPResso Service -- Quick Setup & Run
#
# Installs all dependencies, configures the environment, runs migrations,
# and starts the service. Designed for one-command deployment on fresh servers.
#
# Usage:
#   ./start.sh              # Full setup + start server
#   ./start.sh --setup-only # Install deps and configure, but don't start
#   ./start.sh --run-only   # Skip setup, just start the server
#   ./start.sh --migrate    # Run database migrations only
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
LOG_PREFIX="[espresso]"

# --------------------------------------------------------------------------- #
#  Argument parsing
# --------------------------------------------------------------------------- #
MODE="full"
for arg in "$@"; do
    case "$arg" in
        --setup-only) MODE="setup" ;;
        --run-only)   MODE="run" ;;
        --migrate)    MODE="migrate" ;;
        --help|-h)
            echo "Usage: $0 [--setup-only | --run-only | --migrate | --help]"
            echo "  (no args)     Full setup + start server"
            echo "  --setup-only  Install deps and configure, skip server start"
            echo "  --run-only    Skip setup, start server immediately"
            echo "  --migrate     Run database migrations only"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Run $0 --help for usage."
            exit 1
            ;;
    esac
done

# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
info()  { echo "$LOG_PREFIX $*"; }
warn()  { echo "$LOG_PREFIX WARNING: $*" >&2; }
fail()  { echo "$LOG_PREFIX ERROR: $*" >&2; exit 1; }

command_exists() { command -v "$1" &>/dev/null; }

# --------------------------------------------------------------------------- #
#  Detect OS and package manager
# --------------------------------------------------------------------------- #
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_FAMILY="${ID_LIKE:-$OS_ID}"
    elif command_exists sw_vers; then
        OS_ID="macos"
        OS_FAMILY="macos"
    else
        OS_ID="unknown"
        OS_FAMILY="unknown"
    fi
}

# --------------------------------------------------------------------------- #
#  Install system dependencies
# --------------------------------------------------------------------------- #
install_system_deps() {
    info "Detecting operating system..."
    detect_os
    info "OS: $OS_ID (family: $OS_FAMILY)"

    # Determine which packages to install and how
    case "$OS_ID" in
        ubuntu|debian|pop|linuxmint)
            install_debian_deps
            ;;
        fedora)
            install_fedora_deps
            ;;
        centos|rhel|rocky|alma)
            install_rhel_deps
            ;;
        arch|manjaro)
            install_arch_deps
            ;;
        macos)
            install_macos_deps
            ;;
        *)
            # Try to detect by family
            case "$OS_FAMILY" in
                *debian*|*ubuntu*) install_debian_deps ;;
                *fedora*|*rhel*)   install_rhel_deps ;;
                *arch*)            install_arch_deps ;;
                *)
                    warn "Unrecognized OS '$OS_ID'. Skipping system package install."
                    warn "Ensure Python 3.11+, pip, and libgomp are installed manually."
                    ;;
            esac
            ;;
    esac
}

install_debian_deps() {
    info "Installing system dependencies (apt)..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 \
        python3-pip \
        python3-venv \
        libgomp1 \
        git \
        curl \
        2>&1 | tail -1
    info "System dependencies installed (Debian/Ubuntu)"
}

install_fedora_deps() {
    info "Installing system dependencies (dnf)..."
    sudo dnf install -y -q \
        python3 \
        python3-pip \
        libgomp \
        git \
        curl \
        2>&1 | tail -1
    info "System dependencies installed (Fedora)"
}

install_rhel_deps() {
    info "Installing system dependencies (dnf/yum)..."
    if command_exists dnf; then
        sudo dnf install -y -q \
            python3 \
            python3-pip \
            libgomp \
            git \
            curl \
            2>&1 | tail -1
    else
        sudo yum install -y -q \
            python3 \
            python3-pip \
            libgomp \
            git \
            curl \
            2>&1 | tail -1
    fi
    info "System dependencies installed (RHEL/CentOS)"
}

install_arch_deps() {
    info "Installing system dependencies (pacman)..."
    sudo pacman -S --noconfirm --needed \
        python \
        python-pip \
        gcc \
        git \
        curl \
        2>&1 | tail -1
    info "System dependencies installed (Arch)"
}

install_macos_deps() {
    if ! command_exists brew; then
        warn "Homebrew not found. Install from https://brew.sh"
        warn "Then re-run this script."
        exit 1
    fi
    info "Installing system dependencies (brew)..."
    brew install python@3.12 libomp git curl 2>&1 | tail -1
    info "System dependencies installed (macOS)"
}

# --------------------------------------------------------------------------- #
#  Verify Python version
# --------------------------------------------------------------------------- #
check_python() {
    local PY=""
    if command_exists python3; then
        PY="python3"
    elif command_exists python; then
        PY="python"
    else
        fail "Python not found after dependency installation. Check your PATH."
    fi

    local PY_VERSION
    PY_VERSION=$($PY -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    local PY_MAJOR PY_MINOR
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
        fail "Python 3.11+ required, found $PY_VERSION"
    fi

    info "Python $PY_VERSION ($PY)"
    PYTHON="$PY"
}

# --------------------------------------------------------------------------- #
#  Create/activate virtual environment
# --------------------------------------------------------------------------- #
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        info "Creating virtual environment at $VENV_DIR ..."
        $PYTHON -m venv "$VENV_DIR"
        info "Virtual environment created"
    else
        info "Virtual environment already exists at $VENV_DIR"
    fi

    # Activate
    source "$VENV_DIR/bin/activate"
    info "Virtual environment activated"
}

# --------------------------------------------------------------------------- #
#  Install Python dependencies
# --------------------------------------------------------------------------- #
install_python_deps() {
    info "Upgrading pip..."
    pip install --upgrade pip -q

    info "Installing espresso-service and dependencies..."
    pip install -e ".[db]" -q

    info "Python dependencies installed"
}

# --------------------------------------------------------------------------- #
#  Environment configuration
# --------------------------------------------------------------------------- #
setup_env() {
    if [ -f .env ]; then
        info ".env file found"
    elif [ -f .env.example ]; then
        info ".env file not found -- copying from .env.example"
        cp .env.example .env
        warn "IMPORTANT: Edit .env and fill in your actual values before running."
        warn "  Required: API_KEY, NIM_API_KEY (or NIM_API_KEYS)"
        warn "  Optional: SUPABASE_URL, SUPABASE_SERVICE_KEY, DATABASE_URL"

        if [ "$MODE" != "setup" ]; then
            warn "Continuing with defaults. The service may fail if keys are not set."
        fi
    else
        fail "Neither .env nor .env.example found. Cannot configure environment."
    fi
}

# --------------------------------------------------------------------------- #
#  Create artifact directories
# --------------------------------------------------------------------------- #
setup_directories() {
    mkdir -p artifacts
    mkdir -p espresso_models
    info "Artifact directories ready"
}

# --------------------------------------------------------------------------- #
#  Run database migrations
# --------------------------------------------------------------------------- #
run_migrations() {
    # Check if DATABASE_URL is configured
    local DB_URL=""
    if [ -f .env ]; then
        DB_URL=$(grep -E "^DATABASE_URL=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)
    fi

    if [ -z "$DB_URL" ]; then
        info "DATABASE_URL not set -- skipping migrations"
        info "Set DATABASE_URL in .env to enable automatic migrations"
        return 0
    fi

    info "Installing migration dependency (psycopg2-binary)..."
    pip install psycopg2-binary -q

    info "Running database migrations..."
    $PYTHON -m migrations.migrate
    info "Migrations complete"
}

# --------------------------------------------------------------------------- #
#  Validate configuration
# --------------------------------------------------------------------------- #
validate_config() {
    info "Validating configuration..."

    # Quick check that critical env vars are not placeholder values
    local has_errors=0

    if [ -f .env ]; then
        local api_key
        api_key=$(grep -E "^API_KEY=" .env | head -1 | cut -d= -f2- || true)
        if [ "$api_key" = "your-shared-secret-here" ] || [ -z "$api_key" ]; then
            warn "API_KEY is not configured (still placeholder or empty)"
            has_errors=1
        fi

        local nim_key
        nim_key=$(grep -E "^NIM_API_KEY=" .env | head -1 | cut -d= -f2- || true)
        local nim_keys
        nim_keys=$(grep -E "^NIM_API_KEYS=" .env | head -1 | cut -d= -f2- || true)
        if { [ "$nim_key" = "your-nvidia-nim-api-key" ] || [ -z "$nim_key" ]; } && [ -z "$nim_keys" ]; then
            warn "NIM API key not configured (set NIM_API_KEY or NIM_API_KEYS)"
            has_errors=1
        fi
    fi

    # Check model artifacts
    local models_found=0
    for m in artifacts/model_a.pkl artifacts/model_b.pkl artifacts/model_c.pkl; do
        if [ -f "$m" ]; then
            models_found=$((models_found + 1))
        fi
    done

    if [ "$models_found" -eq 0 ]; then
        warn "No model artifacts found in artifacts/"
        warn "Place model_a.pkl, model_b.pkl, model_c.pkl in artifacts/ for predictions"
    else
        info "Found $models_found/3 model artifacts"
    fi

    if [ "$has_errors" -eq 1 ]; then
        warn "Configuration has placeholder values -- the service may not work correctly"
        warn "Edit .env with your actual credentials"
    else
        info "Configuration looks good"
    fi
}

# --------------------------------------------------------------------------- #
#  Start the server
# --------------------------------------------------------------------------- #
start_server() {
    # Source .env for HOST/PORT defaults
    local host="0.0.0.0"
    local port="8000"
    local log_level="info"

    if [ -f .env ]; then
        host=$(grep -E "^HOST=" .env | head -1 | cut -d= -f2- || echo "0.0.0.0")
        port=$(grep -E "^PORT=" .env | head -1 | cut -d= -f2- || echo "8000")
        log_level=$(grep -E "^LOG_LEVEL=" .env | head -1 | cut -d= -f2- || echo "info")
        [ -z "$host" ] && host="0.0.0.0"
        [ -z "$port" ] && port="8000"
        [ -z "$log_level" ] && log_level="info"
    fi

    info "Starting on $host:$port"

    exec uvicorn app.main:app \
        --host "$host" \
        --port "$port" \
        --log-level "$log_level"
}

# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
case "$MODE" in
    full)
        install_system_deps
        check_python
        setup_venv
        install_python_deps
        setup_env
        setup_directories
        run_migrations
        validate_config
        start_server
        ;;
    setup)
        install_system_deps
        check_python
        setup_venv
        install_python_deps
        setup_env
        setup_directories
        run_migrations
        validate_config
        info "Setup complete. Run '$0 --run-only' to start the server."
        ;;
    run)
        check_python
        if [ -d "$VENV_DIR" ]; then
            source "$VENV_DIR/bin/activate"
        else
            warn "No virtual environment found. Run '$0' (full setup) first."
            warn "Attempting to start with system Python..."
        fi
        setup_env
        validate_config
        start_server
        ;;
    migrate)
        check_python
        if [ -d "$VENV_DIR" ]; then
            source "$VENV_DIR/bin/activate"
        fi
        setup_env
        run_migrations
        ;;
esac
