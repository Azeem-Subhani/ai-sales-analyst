"""Shared pytest fixtures."""

import smtplib
from email.message import EmailMessage
from typing import ClassVar, Self

import pytest

from src.data import generate as gen

OWNER = "owner@example.com"  # dummy report owner used in email tests


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
