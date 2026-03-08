"""Artifact storage backends for local and cloud runtime paths."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from urllib.parse import unquote, urlparse

from .config import ServiceSettings

_SAFE_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,255}$")


class _BlobDownload(Protocol):
    def readall(self) -> bytes: ...


class _BlobClient(Protocol):
    def upload_blob(self, payload: bytes, *, overwrite: bool) -> None: ...

    def download_blob(self) -> _BlobDownload: ...


class _BlobContainerClient(Protocol):
    def get_blob_client(self, blob_name: str) -> _BlobClient: ...


class _BlobServiceClient(Protocol):
    def get_container_client(self, container_name: str) -> _BlobContainerClient: ...


class ArtifactStore(Protocol):
    """Minimal read/write contract for persisted model artifacts."""

    def write_bytes(
        self,
        *,
        directory: str,
        stem: str,
        payload: bytes,
        overwrite: bool = False,
    ) -> str: ...

    def read_bytes(self, artifact_uri: str) -> bytes: ...


class ArtifactStoreError(Exception):
    """Typed artifact storage failure used for service-level mapping."""

    def __init__(self, *, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class FileSystemArtifactStore:
    """Artifact store backed by a configured local filesystem root."""

    def __init__(self, *, root: Path) -> None:
        self._root = root.resolve()

    def write_bytes(
        self,
        *,
        directory: str,
        stem: str,
        payload: bytes,
        overwrite: bool = False,
    ) -> str:
        target_path = self._resolve_write_path(directory=directory, stem=stem)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            existing_payload = target_path.read_bytes()
            if not overwrite and existing_payload != payload:
                raise ArtifactStoreError(kind="collision", detail=str(target_path))
            if existing_payload == payload:
                return str(target_path)
        target_path.write_bytes(payload)
        return str(target_path)

    def read_bytes(self, artifact_uri: str) -> bytes:
        target_path = self._resolve_existing_uri(artifact_uri)
        if not target_path.exists():
            raise ArtifactStoreError(kind="not_found", detail=str(target_path))
        return target_path.read_bytes()

    def _resolve_write_path(self, *, directory: str, stem: str) -> Path:
        safe_directory = _validated_component(directory)
        safe_stem = _validated_component(stem)
        target_path = (self._root / safe_directory / f"{safe_stem}.json").resolve()
        return _ensure_within_root(root=self._root, candidate=target_path)

    def _resolve_existing_uri(self, artifact_uri: str) -> Path:
        if artifact_uri.strip() == "":
            raise ArtifactStoreError(kind="invalid_uri", detail="artifact URI is empty")
        candidate = Path(artifact_uri)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self._root / candidate).resolve()
        )
        return _ensure_within_root(root=self._root, candidate=resolved)


class AzureBlobArtifactStore:
    """Artifact store backed by Azure Blob Storage with managed identity."""

    def __init__(
        self,
        *,
        account_url: str,
        container_name: str,
        managed_identity_client_id: str | None = None,
    ) -> None:
        self._account_url = account_url.rstrip("/")
        self._container_name = container_name
        self._managed_identity_client_id = managed_identity_client_id
        self._blob_service_client = self._build_blob_service_client()

    def write_bytes(
        self,
        *,
        directory: str,
        stem: str,
        payload: bytes,
        overwrite: bool = False,
    ) -> str:
        blob_name = self._build_blob_name(directory=directory, stem=stem)
        blob_client = self._get_blob_client(blob_name)
        try:
            blob_client.upload_blob(payload, overwrite=overwrite)
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceExistsError":
                existing_payload = self.read_bytes(self._build_uri(blob_name))
                if existing_payload != payload:
                    raise ArtifactStoreError(
                        kind="collision",
                        detail=self._build_uri(blob_name),
                    ) from exc
                return self._build_uri(blob_name)
            raise ArtifactStoreError(kind="backend_failure", detail=str(exc)) from exc
        return self._build_uri(blob_name)

    def read_bytes(self, artifact_uri: str) -> bytes:
        blob_name = self._parse_blob_uri(artifact_uri)
        blob_client = self._get_blob_client(blob_name)
        try:
            downloader = blob_client.download_blob()
            payload = downloader.readall()
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundError":
                raise ArtifactStoreError(kind="not_found", detail=artifact_uri) from exc
            raise ArtifactStoreError(kind="backend_failure", detail=str(exc)) from exc
        return payload

    def _build_blob_service_client(self) -> _BlobServiceClient:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
        except ModuleNotFoundError as exc:
            raise ArtifactStoreError(
                kind="backend_unavailable",
                detail=str(exc),
            ) from exc

        credential_kwargs: dict[str, str] = {}
        if self._managed_identity_client_id is not None:
            credential_kwargs["managed_identity_client_id"] = self._managed_identity_client_id
        credential = DefaultAzureCredential(**credential_kwargs)
        return cast(
            _BlobServiceClient,
            BlobServiceClient(
                account_url=self._account_url,
                credential=credential,
            ),
        )

    def _build_blob_name(self, *, directory: str, stem: str) -> str:
        safe_directory = _validated_component(directory)
        safe_stem = _validated_component(stem)
        return f"{safe_directory}/{safe_stem}.json"

    def _get_blob_client(self, blob_name: str) -> _BlobClient:
        container_client = self._blob_service_client.get_container_client(self._container_name)
        return container_client.get_blob_client(blob_name)

    def _build_uri(self, blob_name: str) -> str:
        return f"{self._account_url}/{self._container_name}/{blob_name}"

    def _parse_blob_uri(self, artifact_uri: str) -> str:
        if artifact_uri.strip() == "":
            raise ArtifactStoreError(kind="invalid_uri", detail="artifact URI is empty")
        parsed_uri = urlparse(artifact_uri)
        parsed_account = urlparse(self._account_url)
        if parsed_uri.scheme != "https":
            raise ArtifactStoreError(kind="invalid_uri", detail=artifact_uri)
        if parsed_uri.netloc != parsed_account.netloc:
            raise ArtifactStoreError(kind="invalid_uri", detail=artifact_uri)
        if parsed_uri.query != "" or parsed_uri.fragment != "":
            raise ArtifactStoreError(kind="invalid_uri", detail=artifact_uri)
        raw_path = unquote(parsed_uri.path).lstrip("/")
        path = PurePosixPath(raw_path)
        if len(path.parts) < 2:
            raise ArtifactStoreError(kind="invalid_uri", detail=artifact_uri)
        container_name, *blob_segments = path.parts
        if container_name != self._container_name:
            raise ArtifactStoreError(kind="invalid_uri", detail=artifact_uri)
        if any(segment in {"", ".", ".."} for segment in blob_segments):
            raise ArtifactStoreError(kind="invalid_uri", detail=artifact_uri)
        return "/".join(blob_segments)


def build_artifact_store(settings: ServiceSettings) -> ArtifactStore:
    """Build the configured artifact backend for a service."""
    if settings.artifact_storage_backend == "filesystem":
        return FileSystemArtifactStore(root=Path(settings.artifact_root_dir))
    if settings.artifact_storage_backend == "azure_blob":
        if settings.artifact_blob_account_url is None:
            raise ValueError("artifact_blob_account_url is required for azure_blob backend")
        if settings.artifact_blob_container_name is None:
            raise ValueError("artifact_blob_container_name is required for azure_blob backend")
        return AzureBlobArtifactStore(
            account_url=settings.artifact_blob_account_url,
            container_name=settings.artifact_blob_container_name,
            managed_identity_client_id=settings.artifact_blob_managed_identity_client_id,
        )
    raise ValueError(f"Unsupported artifact storage backend {settings.artifact_storage_backend!r}")


def build_filesystem_artifact_store(root: Path) -> ArtifactStore:
    """Convenience helper for tests and local scripts that use filesystem artifacts."""
    return FileSystemArtifactStore(root=root)


def _validated_component(value: str) -> str:
    if not _SAFE_COMPONENT_PATTERN.fullmatch(value):
        raise ArtifactStoreError(kind="invalid_uri", detail=value)
    return value


def _ensure_within_root(*, root: Path, candidate: Path) -> Path:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactStoreError(kind="invalid_uri", detail=str(candidate)) from exc
    return candidate
