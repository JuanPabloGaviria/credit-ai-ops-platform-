"""Ed25519 signing helpers for immutable model package promotion."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MODEL_SIGNATURE_SCHEMA_VERSION = "credit-model-signature.v1"
MODEL_SIGNATURE_ALGORITHM = "ed25519"


@dataclass(frozen=True, slots=True)
class ModelSignature:
    """Signature block stored alongside promoted model package metadata."""

    schema_version: str
    algorithm: str
    key_id: str
    value: str


def sign_model_package(
    *,
    payload: bytes,
    private_key_pem: str,
    key_id: str,
) -> ModelSignature:
    """Sign a canonical model package payload with an Ed25519 private key."""
    if key_id.strip() == "":
        raise ValueError("Model signing key ID must be non-empty")
    private_key = _load_private_key(private_key_pem)
    signature_bytes = private_key.sign(payload)
    return ModelSignature(
        schema_version=MODEL_SIGNATURE_SCHEMA_VERSION,
        algorithm=MODEL_SIGNATURE_ALGORITHM,
        key_id=key_id,
        value=base64.b64encode(signature_bytes).decode("ascii"),
    )


def verify_model_package_signature(
    *,
    payload: bytes,
    signature: ModelSignature,
    public_key_pem: str,
) -> None:
    """Verify a canonical model package payload against the recorded signature."""
    if signature.schema_version != MODEL_SIGNATURE_SCHEMA_VERSION:
        raise ValueError("Unsupported model signature schema version")
    if signature.algorithm != MODEL_SIGNATURE_ALGORITHM:
        raise ValueError("Unsupported model signature algorithm")
    public_key = _load_public_key(public_key_pem)
    try:
        signature_bytes = base64.b64decode(signature.value.encode("ascii"), validate=True)
    except ValueError as exc:
        raise ValueError("Model signature is not valid base64") from exc
    try:
        public_key.verify(signature_bytes, payload)
    except InvalidSignature as exc:
        raise ValueError("Model signature verification failed") from exc


def _load_private_key(private_key_pem: str) -> Ed25519PrivateKey:
    encoded_key = private_key_pem.strip().encode("utf-8")
    if encoded_key == b"":
        raise ValueError("Model signing private key must be configured")
    loaded_key = serialization.load_pem_private_key(encoded_key, password=None)
    if not isinstance(loaded_key, Ed25519PrivateKey):
        raise ValueError("Model signing private key must be an Ed25519 key")
    return loaded_key


def _load_public_key(public_key_pem: str) -> Ed25519PublicKey:
    encoded_key = public_key_pem.strip().encode("utf-8")
    if encoded_key == b"":
        raise ValueError("Model signing public key must be configured")
    loaded_key = serialization.load_pem_public_key(encoded_key)
    if not isinstance(loaded_key, Ed25519PublicKey):
        raise ValueError("Model signing public key must be an Ed25519 key")
    return loaded_key
