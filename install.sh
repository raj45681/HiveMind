#!/usr/bin/env bash
set -euo pipefail
hive_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin=python3
if [[ -x "$hive_root/.venv/bin/python" ]]; then
    python_bin="$hive_root/.venv/bin/python"
fi
exec "$python_bin" "$hive_root/bootstrap.py" "$@"
