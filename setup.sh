#!/usr/bin/env bash
set -euo pipefail
hive_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$hive_root/.venv/bin/python" ]]; then
    python3 -m venv "$hive_root/.venv"
fi
hive_python="$hive_root/.venv/bin/python"
"$hive_python" -m pip install -r "$hive_root/requirements.txt"
if [[ ! -f "$hive_root/hive.local.json" ]]; then
    "$hive_python" "$hive_root/hive.py" init
fi
if [[ "${1:-}" != "--skip-register" ]]; then
    "$hive_python" "$hive_root/scripts/register_agents.py"
fi
echo "HiveMind ready. Use $hive_python $hive_root/hive.py doctor"
echo "Obsidian vault: $hive_root/vault"
