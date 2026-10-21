"""Shared pytest fixtures."""

import shutil
import smtplib
import socket
from datetime import date
from email.message import EmailMessage
from typing import ClassVar, Self

import pandas as pd
import pytest

from src.analytics import forecasting
from src.data import generate as gen

OWNER = "owner@example.com"  # dummy report owner used in email tests
BLOCKED_HOST_SUFFIX = "openrouter.ai"  # tests must never spend OpenRouter credit
LLM_ENV_NAMES = (
    "OPENROUTER_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_FALLBACK_MODEL",
    "LLM_TIMEOUT_SECONDS",
)


class NetworkBlockedError(OSError):
    """Raised when a test tries to reach OpenRouter."""


@pytest.fixture(autouse=True)
def block_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every real connection to OpenRouter fail, and clear any LLM settings.

    Patching socket name resolution and connect works whatever HTTP library the
    openai SDK uses underneath. Tests that need settings set dummy values themselves.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection

    def guarded_host(host: object) -> None:
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name.rstrip(".").endswith(BLOCKED_HOST_SUFFIX):
            raise NetworkBlockedError(f"Tests may not call {name}; use a fake LLM client.")

    def getaddrinfo(host: object, *args: object, **kwargs: object) -> list:
        guarded_host(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def create_connection(address: tuple, *args: object, **kwargs: object) -> socket.socket:
        guarded_host(address[0])
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build the data once per test run into a temporary database."""
    tables, issues = gen.build_tables(gen.SEED)
    db_path = tmp_path_factory.mktemp("data") / "sales.duckdb"
    gen.write_database(tables, db_path)
    return {"tables": tables, "issues": issues, "db_path": db_path}


class FakeSMTP:
    """Stands in for smtplib.SMTP and records what would have been sent."""

    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent: list[tuple[EmailMessage, str, list[str]]] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, context: object = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")

    def send_message(self, msg: EmailMessage, from_addr: str, to_addrs: list[str]) -> None:
        self.calls.append("send")
        self.sent.append((msg, from_addr, to_addrs))


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    """Fake SMTP plus the env the emailer needs (dummy values, never real credentials)."""
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("REPORT_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-app-password")
    return FakeSMTP


def pytest_addoption(parser: pytest.Parser) -> None:
    """--runslow runs the slow tests as well as the default ones."""
    parser.addoption("--runslow", action="store_true", help="also run tests marked slow")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip slow tests unless --runslow is given or -m selects them."""
    if config.getoption("--runslow") or "slow" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="slow: run with --runslow or -m slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


DEMAND_DAYS = [date(2026, 9, 27), date(2026, 9, 30)]
REAL_OWN_FORECASTS = forecasting.own_forecasts


def fast_own_forecasts(inputs: object, series: pd.DataFrame, settings: dict) -> tuple:
    """Stand-in for the statsforecast fit in default tests: recent-mean forecasts only.

    Every product gets the fallback forecast and there is no backtest, so a demand run
    takes about a second. Tests marked slow use the real fit (real_demand_runs).
    """
    ids = sorted(series["product_id"].unique())
    forecasts = forecasting.recent_mean_forecast(series, ids, settings)
    forecasts["is_chosen"], forecasts["is_fallback"] = True, True
    forecasts["sku"] = forecasts["product_id"].map(series.groupby("product_id")["sku"].first())
    return forecasts, pd.DataFrame()


@pytest.fixture(scope="session", autouse=True)
def fast_forecasts() -> object:
    """Use the fast stand-in for every demand run except real_demand_runs."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(forecasting, "own_forecasts", fast_own_forecasts)
        yield


@pytest.fixture
def real_own_forecasts() -> object:
    """The real statsforecast fit, for tests that call it on a small product subset."""
    return REAL_OWN_FORECASTS


@pytest.fixture(scope="session")
def demand_runs(built: dict) -> dict:
    """Demand snapshots for 2026-09-27 and 2026-09-30 with the fast forecast stand-in.

    planning.duckdb is written next to the temporary sales database.
    """
    from src.demand import run

    return {day: run.run_demand(day, sales_db=built["db_path"]) for day in DEMAND_DAYS}


@pytest.fixture(scope="session")
def real_demand_runs(built: dict, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The same snapshots with the real statsforecast fit (slow tests only).

    They use their own copy of the sales database, so their planning.duckdb never
    mixes with the fast snapshots.
    """
    from src.demand import run

    folder = tmp_path_factory.mktemp("real_demand")
    db_path = folder / "sales.duckdb"
    shutil.copy(built["db_path"], db_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(forecasting, "own_forecasts", REAL_OWN_FORECASTS)
        runs = {day: run.run_demand(day, sales_db=db_path) for day in DEMAND_DAYS}
    return {"db_path": db_path, **runs}
