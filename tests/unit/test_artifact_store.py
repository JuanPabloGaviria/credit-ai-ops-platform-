from __future__ import annotations

from pathlib import Path

import pytest

from shared_kernel.artifacts import (
    ArtifactStoreError,
    AzureBlobArtifactStore,
    FileSystemArtifactStore,
)


class ResourceExistsError(Exception):
    pass


class ResourceNotFoundError(Exception):
    pass


class _FakeDownloader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def readall(self) -> bytes:
        return self._payload


class _FakeBlobClient:
    def __init__(self, blobs: dict[str, bytes], blob_name: str) -> None:
        self._blobs = blobs
        self._blob_name = blob_name

    def upload_blob(self, payload: bytes, *, overwrite: bool) -> None:
        if self._blob_name in self._blobs and not overwrite:
            raise ResourceExistsError(self._blob_name)
        self._blobs[self._blob_name] = payload

    def download_blob(self) -> _FakeDownloader:
        payload = self._blobs.get(self._blob_name)
        if payload is None:
            raise ResourceNotFoundError(self._blob_name)
        return _FakeDownloader(payload)


class _FakeContainerClient:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def get_blob_client(self, blob_name: str) -> _FakeBlobClient:
        return _FakeBlobClient(self._blobs, blob_name)


class _FakeBlobServiceClient:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def get_container_client(self, container_name: str) -> _FakeContainerClient:
        _ = container_name
        return _FakeContainerClient(self._blobs)


def _fake_blob_service_builder(_store: AzureBlobArtifactStore) -> _FakeBlobServiceClient:
    return _FakeBlobServiceClient()


@pytest.mark.unit
def test_filesystem_artifact_store_writes_and_reads(tmp_path: Path) -> None:
    store = FileSystemArtifactStore(root=tmp_path)

    artifact_uri = store.write_bytes(
        directory="artifacts",
        stem="credit-risk-v1",
        payload=b'{"model":"credit-risk"}',
    )

    assert Path(artifact_uri).exists()
    assert store.read_bytes(artifact_uri) == b'{"model":"credit-risk"}'


@pytest.mark.unit
def test_filesystem_artifact_store_rejects_escape(tmp_path: Path) -> None:
    store = FileSystemArtifactStore(root=tmp_path)
    escaped_path = tmp_path.parent / "escaped.json"
    escaped_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ArtifactStoreError) as error:
        store.read_bytes(str(escaped_path))

    assert error.value.kind == "invalid_uri"


@pytest.mark.unit
def test_azure_blob_artifact_store_writes_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = _FakeBlobServiceClient()

    def _patched_builder(_store: AzureBlobArtifactStore) -> _FakeBlobServiceClient:
        return fake_service

    monkeypatch.setattr(
        AzureBlobArtifactStore,
        "_build_blob_service_client",
        _patched_builder,
    )
    store = AzureBlobArtifactStore(
        account_url="https://creditaiopsartifacts.blob.core.windows.net",
        container_name="mlops-artifacts",
        managed_identity_client_id="00000000-0000-0000-0000-000000000001",
    )

    artifact_uri = store.write_bytes(
        directory="registered_artifacts",
        stem="credit-risk--v1.0.0--1234567890abcdef",
        payload=b'{"schema_version":"credit-model-package.v2"}',
    )

    assert (
        artifact_uri
        == "https://creditaiopsartifacts.blob.core.windows.net/mlops-artifacts/"
        "registered_artifacts/credit-risk--v1.0.0--1234567890abcdef.json"
    )
    assert store.read_bytes(artifact_uri) == b'{"schema_version":"credit-model-package.v2"}'


@pytest.mark.unit
def test_azure_blob_artifact_store_rejects_foreign_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        AzureBlobArtifactStore,
        "_build_blob_service_client",
        _fake_blob_service_builder,
    )
    store = AzureBlobArtifactStore(
        account_url="https://creditaiopsartifacts.blob.core.windows.net",
        container_name="mlops-artifacts",
    )

    with pytest.raises(ArtifactStoreError) as error:
        store.read_bytes("https://other.blob.core.windows.net/mlops-artifacts/artifacts/model.json")

    assert error.value.kind == "invalid_uri"
