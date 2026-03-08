from collections.abc import Mapping, Sequence

class PyJWK:
    key: object

class PyJWKClient:
    def __init__(self, uri: str) -> None: ...
    def get_signing_key_from_jwt(self, token: str) -> PyJWK: ...

def encode(
    payload: Mapping[str, object],
    key: str,
    algorithm: str,
) -> str: ...

def decode(
    jwt: str,
    key: object,
    algorithms: Sequence[str],
    issuer: str | None = ...,
    options: Mapping[str, object] | None = ...,
    leeway: float | int = ...,
) -> Mapping[str, object]: ...
