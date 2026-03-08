from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

class Session:
    def install(self, *args: str) -> None: ...
    def run(self, *args: str) -> None: ...

class _Options:
    default_venv_backend: str

options: _Options

def session(
    *,
    python: Sequence[str] | None = ...,
) -> Callable[[_F], _F]: ...
