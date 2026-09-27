"""Inspect HiveMind's pinned bridge dependencies without importing them."""
import importlib.metadata
from pathlib import Path
import re


def pinned_requirements(path):
    pins = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
        if not match:
            raise ValueError(f"Bridge requirement must be pinned with ==: {line}")
        pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def local_dependency_status(path):
    pins = pinned_requirements(path)
    installed = {}
    for name in pins:
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed[name] = None
    mismatched = {name: {"required": version, "installed": installed[name]}
                  for name, version in pins.items() if installed[name] != version}
    return {"ok": not mismatched, "pinned": pins, "installed": installed, "mismatched": mismatched}
