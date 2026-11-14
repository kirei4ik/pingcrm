"""Unit tests for SOCKS5 proxy support in the Telegram transport."""
from __future__ import annotations

import os

import pytest

# Ensure settings are available before importing app modules
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("ENCRYPTION_KEY", "HiuobeEdnSk93dMtnycRm8Kob9D3-7-vCw3_L0YG9Ek=")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://localhost:5432/pingcrm_test")

from python_socks import ProxyType

from app.core.config import settings
from app.integrations.telegram_transport import _build_proxy, _make_client


def test_build_proxy_disabled_when_empty():
    assert _build_proxy("", "", "") is None
    assert _build_proxy("   ", "", "") is None


def test_build_proxy_without_credentials():
    assert _build_proxy("1.2.3.4:1080", "", "") == (
        ProxyType.SOCKS5,
        "1.2.3.4",
        1080,
        True,
        None,
        None,
    )


def test_build_proxy_with_credentials():
    assert _build_proxy("proxy.example:1080", "user", "pass") == (
        ProxyType.SOCKS5,
        "proxy.example",
        1080,
        True,
        "user",
        "pass",
    )


@pytest.mark.parametrize(
    "value",
    ["1.2.3.4", "1.2.3.4:", "1.2.3.4:notaport", "1.2.3.4:0", "1.2.3.4:70000"],
)
def test_build_proxy_rejects_invalid_value(value):
    with pytest.raises(ValueError):
        _build_proxy(value, "", "")


def test_make_client_without_proxy(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 12345)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(settings, "TELEGRAM_SOCKS5_PROXY", "")

    client = _make_client()

    assert client._proxy is None


def test_make_client_with_proxy(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", 12345)
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "hash")
    monkeypatch.setattr(settings, "TELEGRAM_SOCKS5_PROXY", "1.2.3.4:1080")
    monkeypatch.setattr(settings, "TELEGRAM_SOCKS5_USERNAME", "")
    monkeypatch.setattr(settings, "TELEGRAM_SOCKS5_PASSWORD", "")

    client = _make_client()

    assert client._proxy == (
        ProxyType.SOCKS5,
        "1.2.3.4",
        1080,
        True,
        None,
        None,
    )
