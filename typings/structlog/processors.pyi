from collections.abc import MutableMapping
from typing import Any

class TimeStamper:
    def __init__(self, *, fmt: str, utc: bool) -> None: ...
    def __call__(
        self,
        logger: Any,
        method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> MutableMapping[str, Any]: ...

def add_log_level(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]: ...

class JSONRenderer:
    def __call__(
        self,
        logger: Any,
        method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> str: ...
