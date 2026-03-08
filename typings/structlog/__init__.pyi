from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from . import processors as _processors
from . import stdlib as _stdlib

processors = _processors
stdlib = _stdlib

class BoundLogger:
    def info(self, event: str, /, **kwargs: object) -> None: ...
    def error(self, event: str, /, **kwargs: object) -> None: ...

def configure(
    *,
    processors: list[Callable[[Any, str, MutableMapping[str, Any]], Mapping[str, Any] | str]],
    logger_factory: object,
    cache_logger_on_first_use: bool,
) -> None: ...

def get_logger(*, service: str) -> BoundLogger: ...
