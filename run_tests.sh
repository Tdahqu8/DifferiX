#!/usr/bin/env bash
# RSI sandbox test runner.
# Usage:
#   ./run_tests.sh          # unit tests only (fast, default)
#   ./run_tests.sh all      # unit + integration
#   ./run_tests.sh llm      # unit + integration + llm (expensive)
#
# Extra arguments are passed through to pytest. Example:
#   ./run_tests.sh fast -k test_storage

set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Run:"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -e '.[dev]'"
    exit 1
fi

source .venv/bin/activate

MODE="${1:-fast}"

# Drop the mode arg so it doesn't leak into pytest.
shift 2>/dev/null || true

case "$MODE" in
    fast|unit)
        echo "[RSI] Running unit tests..."
        pytest tests/ -m "unit" "$@"
        ;;
    all|integration)
        echo "[RSI] Running unit + integration tests..."
        pytest tests/ -m "unit or integration" "$@"
        ;;
    llm)
        echo "[RSI] Running all tests including LLM calls (expensive)..."
        pytest tests/ -m "unit or integration or llm" "$@"
        ;;
    *)
        echo "Usage: $0 [fast|all|llm] [pytest args...]"
        exit 1
        ;;
esac