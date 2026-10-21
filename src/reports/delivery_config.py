"""Recipient settings from config/reports.yaml, with ${VAR} filled in from the environment.

reports.yaml never holds real addresses: it writes `${REPORT_OWNER_EMAIL}` and the
value comes from .env. The top-level `delivery` block holds the allowlist (the only
addresses any email may go to) and who gets failure notices.
"""

import os
import re
from dataclasses import dataclass

DELIVERY_KEY = "delivery"
ENV_REFERENCE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


class ConfigError(ValueError):
    """A setting is missing or malformed; retrying will not help."""


@dataclass(frozen=True)
class Delivery:
    """Who may receive email and who is told about failures (all lower-cased)."""

    allowed_recipients: frozenset[str]
    failure_recipients: list[str]


def expand(value: str) -> str:
    """Return value, or the environment variable it names when written as ${NAME}.

    Only a whole-value reference is expanded; anything else is returned as written.
    """
    match = ENV_REFERENCE.match(value.strip())
    if not match:
        return value.strip()
    name = match.group(1)
    resolved = os.environ.get(name, "").strip()
    if not resolved:
        raise ConfigError(f"{name} is not set. Add it to .env (see .env.example).")
    return resolved


def expand_addresses(values: list[str]) -> list[str]:
    """Expand and lower-case a list of addresses."""
    return [expand(v).lower() for v in values]


def delivery_settings(config: dict) -> Delivery:
    """Read the `delivery` block of the full reports.yaml config."""
    block = config.get(DELIVERY_KEY) or {}
    allowed = expand_addresses(block.get("allowed_recipients") or [])
    failure = expand_addresses(block.get("failure_recipients") or [])
    if not allowed:
        raise ConfigError("delivery.allowed_recipients in config/reports.yaml is empty.")
    return Delivery(frozenset(allowed), failure)


def report_recipients(block: dict) -> list[str]:
    """A report block's recipients, expanded."""
    return expand_addresses(block.get("recipients") or [])
