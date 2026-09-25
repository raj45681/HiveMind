"""Populate a fresh local vault from generic templates without replacing notes."""
from pathlib import Path


def seed_vault(root):
    root = Path(root).resolve()
    templates = root / "templates/vault"
    vault = root / "vault"
    created = []
    for source in sorted(templates.rglob("*.md")):
        destination = vault / source.relative_to(templates)
        if not source.resolve().is_relative_to(templates.resolve()) or not destination.resolve().is_relative_to(root):
            raise ValueError("Vault templates must remain inside HiveMind")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as output:
                output.write(source.read_bytes())
        except FileExistsError:
            continue
        created.append(destination.relative_to(vault).as_posix())
    return created
