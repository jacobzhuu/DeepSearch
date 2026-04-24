#!/usr/bin/env sh
set -eu

PYTHON_BIN="${PYTHON:-python3}"
ACTION="${1:-upgrade}"
TARGET="${2:-head}"

case "$ACTION" in
  upgrade|downgrade|current|history)
    ;;
  *)
    echo "unsupported alembic action: $ACTION" >&2
    echo "usage: ./scripts/migrate.sh [upgrade|downgrade|current|history] [target]" >&2
    exit 2
    ;;
esac

if [ "$ACTION" = "upgrade" ] || [ "$ACTION" = "downgrade" ]; then
  exec "$PYTHON_BIN" -m alembic -c alembic.ini "$ACTION" "$TARGET"
fi

exec "$PYTHON_BIN" -m alembic -c alembic.ini "$ACTION"
