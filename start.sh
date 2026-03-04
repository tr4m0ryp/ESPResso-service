#!/usr/bin/env bash
#
# ESPResso Local Test Runner
#
# Checks prerequisites, verifies database connection, and runs the
# interactive local test pipeline (no API server needed).
#
set -euo pipefail

cd "$(dirname "$0")"

echo ""
echo "================================================================"
echo "  ESPResso Local Test Runner -- Prerequisites Check"
echo "================================================================"
echo ""

# Check Python
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    echo "ERROR: Python 3 not found. Install Python 3.11+."
    exit 1
fi
PYTHON=$(command -v python3 || command -v python)
echo "[ok] Python: $($PYTHON --version)"

# Check .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found."
    echo "  cp .env.example .env"
    echo "  Then fill in DATABASE_URL and other values."
    exit 1
fi
echo "[ok] .env file found"

# Check DATABASE_URL is set
DB_URL=$(grep -E "^DATABASE_URL=" .env | head -1 | cut -d= -f2-)
if [ -z "$DB_URL" ] || [ "$DB_URL" = "postgresql://postgres:postgres@localhost:54322/postgres" ]; then
    if [ -z "$DB_URL" ]; then
        echo "ERROR: DATABASE_URL not set in .env"
        exit 1
    fi
    echo "[--] DATABASE_URL: local Supabase (localhost:54322)"
else
    # Mask password for display
    DISPLAY_URL=$(echo "$DB_URL" | sed -E 's/(:)[^@:]+(@)/\1****\2/')
    echo "[ok] DATABASE_URL: $DISPLAY_URL"
fi

# Check psycopg2
if ! $PYTHON -c "import psycopg2" 2>/dev/null; then
    echo ""
    echo "Installing psycopg2-binary..."
    $PYTHON -m pip install psycopg2-binary -q
    echo "[ok] psycopg2 installed"
else
    echo "[ok] psycopg2 available"
fi

# Check model artifacts
echo ""
MODELS_FOUND=0
for m in artifacts/model_a.pkl artifacts/model_b.pkl artifacts/model_c.pkl; do
    if [ -f "$m" ]; then
        SIZE=$(du -h "$m" | cut -f1)
        echo "[ok] $m ($SIZE)"
        MODELS_FOUND=$((MODELS_FOUND + 1))
    else
        echo "[--] $m not found (predictions will be skipped)"
    fi
done

if [ $MODELS_FOUND -eq 0 ]; then
    echo ""
    echo "NOTE: No model artifacts found. The test will verify database"
    echo "      connectivity and data assembly, but cannot run predictions."
    echo "      Place .pkl files in artifacts/ to enable predictions."
fi

echo ""
echo "================================================================"
echo "  Starting interactive test..."
echo "================================================================"
echo ""

$PYTHON scripts/local_test.py
