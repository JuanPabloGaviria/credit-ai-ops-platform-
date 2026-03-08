"""Centralized test-only defaults for local integration endpoints."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_POSTGRES_DSN = "postgresql://credit:credit@localhost:5432/credit_ai_ops"
DEFAULT_RABBITMQ_URL = "amqp://guest:guest@localhost:5672/"
TEST_MODEL_SIGNING_KEY_ID = "test-ed25519-key"

_TEST_MODEL_SIGNING_PRIVATE_KEY = Ed25519PrivateKey.generate()

TEST_MODEL_SIGNING_PRIVATE_KEY_PEM = _TEST_MODEL_SIGNING_PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("ascii")

TEST_MODEL_SIGNING_PUBLIC_KEY_PEM = _TEST_MODEL_SIGNING_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("ascii")
