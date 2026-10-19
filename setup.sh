#!/usr/bin/env bash
set -euo pipefail
hive_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$hive_root/.venv/bin/python" ]]; then
    exec "$hive_root/.venv/bin/python" "$hive_root/bootstrap.py" "$@"
fi
exec python3 "$hive_root/bootstrap.py" "$@"
