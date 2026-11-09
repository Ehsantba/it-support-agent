"""Liest eine .env-Datei (KEY=VALUE) in die Umgebung, ohne vorhandene Variablen zu überschreiben."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
