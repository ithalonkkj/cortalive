"""Pastas do app. Tudo fica em ~/CortaLive, ou em CORTALIVE_HOME se você definir."""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    p = Path(os.environ.get("CORTALIVE_HOME") or (Path.home() / "CortaLive"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def projects_dir() -> Path:
    d = home() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def uploads_dir() -> Path:
    d = home() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fonts_dir() -> Path:
    return Path(__file__).parent / "fonts"


def static_dir() -> Path:
    return Path(__file__).parent / "static"
