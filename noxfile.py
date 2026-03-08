"""Nox sessions for deterministic local and CI execution."""

from __future__ import annotations

import nox

nox.options.default_venv_backend = "uv"


@nox.session(python=["3.11"])
def lint(session: nox.Session) -> None:
    session.install("ruff==0.12.8")
    session.run("ruff", "check", ".")


@nox.session(python=["3.11"])
def type_check(session: nox.Session) -> None:
    session.install("mypy==1.17.1")
    session.install("-e", ".[dev]")
    session.run("mypy", ".")


@nox.session(python=["3.11"])
def tests(session: nox.Session) -> None:
    session.install("-e", ".[dev]")
    session.run("pytest", "-m", "unit or contract", "-vv")
