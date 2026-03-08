class _LabeledMetric:
    def inc(self, amount: float = ...) -> None: ...
    def observe(self, amount: float) -> None: ...

class Counter:
    def __init__(
        self,
        name: str,
        documentation: str,
        *,
        labelnames: tuple[str, ...],
        namespace: str,
    ) -> None: ...
    def labels(self, **labels: str) -> _LabeledMetric: ...

class Histogram:
    def __init__(
        self,
        name: str,
        documentation: str,
        *,
        labelnames: tuple[str, ...],
        namespace: str,
    ) -> None: ...
    def labels(self, **labels: str) -> _LabeledMetric: ...

def generate_latest() -> bytes: ...
