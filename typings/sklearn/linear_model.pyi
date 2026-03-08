from typing import Any, Self

class LogisticRegression:
    coef_: list[list[float]]
    intercept_: list[float]

    def __init__(
        self,
        *,
        random_state: int,
        max_iter: int,
        solver: str,
    ) -> None: ...

    def fit(self, x: Any, y: Any) -> Self: ...
    def predict_proba(self, x: Any) -> Any: ...
