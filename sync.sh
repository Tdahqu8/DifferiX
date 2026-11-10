#!/usr/bin/env bash

# Настройки путей (измени, если у тебя они другие)
LIVE_DIR="$HOME/e/d"
SANDBOX_DIR="$HOME/sandboxes/diffex-rsi-01"

# Проверка, что обе папки существуют
if [ ! -d "$LIVE_DIR" ] || [ ! -d "$SANDBOX_DIR" ]; then
    echo "Error: Live or Sandbox directory not found."
    exit 1
fi

# Флаги для rsync, чтобы не переносить мусор
RSYNC_FLAGS=(
    -avz --no-perms --no-owner --no-group
    "--exclude=.git/"
    "--exclude=.venv/"
    "--exclude=__pycache__/"
    "--exclude=*.pyc"
    "--exclude=state.json"
    "--exclude=.vegex/"
    "--exclude=iteration_logs/"
    "--exclude=status/"
    "--exclude=memory.db*"
    "--exclude=.env"
    "--exclude=.env.rsi"
    "--exclude=models_config.json"
    "--exclude=openrouter_models_cache.json"
    "--exclude=projects_registry.json"
)

ACTION=$1

if [ "$ACTION" == "push" ]; then
    echo "⬆️  Pushing from LIVE to SANDBOX..."
    rsync "${RSYNC_FLAGS[@]}" "$LIVE_DIR/" "$SANDBOX_DIR/"
    echo "✅ Done. Sandbox is up to date."
elif [ "$ACTION" == "pull" ]; then
    echo "⬇️  Pulling from SANDBOX to LIVE..."
    # Внимание: без --delete, чтобы случайно не снести новые файлы в live
    rsync "${RSYNC_FLAGS[@]}" "$SANDBOX_DIR/" "$LIVE_DIR/"
    echo "✅ Done. Live is up to date."
else
    echo "Usage: ./sync.sh [push|pull]"
    echo "  push - copy code from LIVE to SANDBOX (before starting RSI)"
    echo "  pull - copy code from SANDBOX to LIVE (after finishing RSI)"
    exit 1
fi
