"""Demand-planning thresholds from config/demand.yaml, and a hash that identifies them."""

import hashlib
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMAND_CONFIG_PATH = PROJECT_ROOT / "config" / "demand.yaml"
HASH_CHARS = 12
REQUIRED_SECTIONS = (
    "demand",
    "customer_alerts",
    "category_alerts",
    "stock_alerts",
    "forecast_accuracy",
    "forecasts",
    "prebuild",
    "reports",
)


def load_config(path: Path = DEMAND_CONFIG_PATH) -> dict:
    """The thresholds as a dict; a missing section fails loudly rather than defaulting."""
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [name for name in REQUIRED_SECTIONS if name not in config]
    if missing:
        raise ValueError(f"{path} is missing sections: {missing}.")
    return config


def config_hash(config: dict) -> str:
    """A short, stable hash of the settings (key order does not matter)."""
    text = yaml.safe_dump(config, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_CHARS]
