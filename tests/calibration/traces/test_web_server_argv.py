"""The web driver's server command, tested without httpx installed."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PATH = Path(__file__).parent / "targets" / "web" / "web_bench.py"


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import web_bench against a stub httpx (env without it)."""
    monkeypatch.setitem(sys.modules, "httpx", ModuleType("httpx"))
    spec = importlib.util.spec_from_file_location("web_bench_stub", PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_server_command_is_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = _load(monkeypatch).server_argv([], 4321)
    assert argv[:4] == [sys.executable, "-m", "uvicorn", "app:app"]
    assert argv[argv.index("--port") + 1] == "4321"


def test_custom_server_command_gets_the_port_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = _load(monkeypatch).server_argv(["node", "server.js"], 4321)
    assert argv == ["node", "server.js", "4321"]
