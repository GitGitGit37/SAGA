"""Loads the YAML domain config (thresholds, fault codes, categories, scoring)."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.settings import get_settings


def _load(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    path = (config_dir or get_settings().config_dir) / name
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def thresholds() -> dict[str, Any]:
    return _load("thresholds.yaml")


@lru_cache
def fault_codes() -> dict[str, Any]:
    return _load("fault_codes.yaml")["codes"]


@lru_cache
def categories() -> dict[str, Any]:
    return _load("categories.yaml")["categories"]


@lru_cache
def scoring() -> dict[str, Any]:
    cfg = _load("scoring.yaml")
    total = sum(cfg["confidence_weights"].values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"confidence_weights must sum to 1.0, got {total}")
    return cfg
