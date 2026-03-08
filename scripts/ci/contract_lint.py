"""Contract lint checks for naming, parseability, and route coverage."""

from __future__ import annotations

import importlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi.routing import APIRoute

ROOT = Path(__file__).resolve().parents[2]
OPENAPI_DIR = ROOT / "schemas" / "openapi"
ASYNCAPI_DIR = ROOT / "schemas" / "asyncapi"
JSONSCHEMA_DIR = ROOT / "schemas" / "jsonschema"

OPENAPI_PATTERN = re.compile(r"^[a-z0-9-]+-v\d+\.ya?ml$")
ASYNCAPI_PATTERN = re.compile(r"^[a-z0-9-]+-v\d+\.ya?ml$")
JSONSCHEMA_PATTERN = re.compile(r"^[a-z0-9-]+-v\d+\.json$")
EVENT_PATTERN = re.compile(r"^[a-z]+\.[a-z_]+\.[a-z_]+\.v\d+$")
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
SYSTEM_PATHS = frozenset({"/health", "/ready", "/metrics"})
OPENAPI_ROUTE_MODULES = {
    "api-gateway-v1.yaml": "api_gateway.routes",
    "application-v1.yaml": "application_service.routes",
    "collab-assistant-v1.yaml": "collab_assistant.routes",
    "decision-v1.yaml": "decision_service.routes",
    "feature-v1.yaml": "feature_service.routes",
    "mlops-v1.yaml": "mlops_service.routes",
    "observability-audit-v1.yaml": "observability_audit.routes",
    "scoring-v1.yaml": "scoring_service.routes",
}


def _bootstrap_pythonpath() -> None:
    for source_root in sorted((ROOT / "packages").glob("*/src")) + sorted(
        (ROOT / "services").glob("*/src")
    ):
        source_str = str(source_root)
        if source_str not in sys.path:
            sys.path.insert(0, source_str)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded: object = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        _fail(f"YAML root must be an object in {path.name}")
    return cast(dict[str, Any], loaded)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    return cast(dict[str, Any], loaded)


def _fail(message: str) -> None:
    print(f"[contract-lint] {message}")
    sys.exit(1)


def _ensure_mapping(value: object, *, context: str) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    _fail(f"{context} must be an object")
    raise AssertionError("unreachable")


def _route_methods(path_item: Mapping[str, object]) -> set[str]:
    return {method for method in path_item if method in HTTP_METHODS}


def _load_router_routes(module_name: str) -> dict[str, set[str]]:
    _bootstrap_pythonpath()
    module = importlib.import_module(module_name)
    router = getattr(module, "router", None)
    if router is None:
        _fail(f"Module {module_name} does not expose a router")

    documented_routes: dict[str, set[str]] = {}
    for route in getattr(router, "routes", []):
        if not isinstance(route, APIRoute):
            continue
        methods = {
            method.lower()
            for method in route.methods or set()
            if method not in {"HEAD", "OPTIONS"}
        }
        documented_routes.setdefault(route.path, set()).update(methods)
    return documented_routes


def _contains_bearer_security(requirements: object) -> bool:
    if not isinstance(requirements, list):
        return False
    typed_requirements = cast(list[object], requirements)
    for requirement in typed_requirements:
        if isinstance(requirement, Mapping):
            requirement_mapping = cast(Mapping[str, object], requirement)
            if "bearerAuth" in requirement_mapping:
                return True
    return False


def _require_route_module(file_path: Path) -> str:
    module_name = OPENAPI_ROUTE_MODULES.get(file_path.name)
    if module_name is None:
        _fail(f"No route coverage module mapping configured for {file_path.name}")
        raise AssertionError("unreachable")
    return module_name


def _lint_openapi_route_coverage(file_path: Path, document: Mapping[str, object]) -> None:
    module_name = _require_route_module(file_path)

    paths = _ensure_mapping(document.get("paths"), context=f"{file_path.name} paths")
    for system_path in SYSTEM_PATHS:
        if system_path not in paths:
            _fail(f"Missing system path {system_path} in {file_path.name}")

    documented_routes: dict[str, set[str]] = {}
    document_security = document.get("security")
    components = _ensure_mapping(
        document.get("components", {}),
        context=f"{file_path.name} components",
    )
    security_schemes = _ensure_mapping(
        components.get("securitySchemes", {}),
        context=f"{file_path.name} components.securitySchemes",
    )
    if "bearerAuth" not in security_schemes:
        _fail(f"Missing bearerAuth security scheme in {file_path.name}")

    for path_name, raw_path_item in paths.items():
        path_item = _ensure_mapping(raw_path_item, context=f"{file_path.name} path {path_name}")
        route_methods = _route_methods(path_item)
        if path_name.startswith("/v1/"):
            documented_routes[path_name] = route_methods
        for method in route_methods:
            operation = _ensure_mapping(
                path_item.get(method),
                context=f"{file_path.name} path {path_name} {method}",
            )
            if path_name.startswith("/v1/") and "security" not in operation:
                _fail(
                    f"Missing explicit operation-level security for "
                    f"{method.upper()} {path_name} in {file_path.name}"
                )
            effective_security = operation.get("security", document_security)
            if path_name.startswith("/v1/") and not _contains_bearer_security(effective_security):
                _fail(
                    f"Missing bearerAuth security for {method.upper()} "
                    f"{path_name} in {file_path.name}"
                )
            if path_name in SYSTEM_PATHS and _contains_bearer_security(effective_security):
                _fail(
                    f"System path {method.upper()} {path_name} must not "
                    f"require bearerAuth in {file_path.name}"
                )

    implemented_routes = _load_router_routes(module_name)
    missing_routes = sorted(set(implemented_routes) - set(documented_routes))
    extra_routes = sorted(set(documented_routes) - set(implemented_routes))
    if missing_routes:
        _fail(
            f"OpenAPI missing implemented routes in {file_path.name}: "
            f"{', '.join(missing_routes)}"
        )
    if extra_routes:
        _fail(
            f"OpenAPI documents routes not implemented in {file_path.name}: "
            f"{', '.join(extra_routes)}"
        )
    for path_name in sorted(implemented_routes):
        if implemented_routes[path_name] != documented_routes[path_name]:
            implemented = ", ".join(sorted(implemented_routes[path_name]))
            documented = ", ".join(sorted(documented_routes[path_name]))
            _fail(
                f"OpenAPI method mismatch for {path_name} in {file_path.name}: "
                f"implemented=[{implemented}] documented=[{documented}]"
            )


def lint_openapi() -> None:
    files = sorted(OPENAPI_DIR.glob("*.yml")) + sorted(OPENAPI_DIR.glob("*.yaml"))
    if not files:
        _fail("No OpenAPI files found")
    for file_path in files:
        if OPENAPI_PATTERN.match(file_path.name) is None:
            _fail(f"Invalid OpenAPI file name: {file_path.name}")
        document = _load_yaml(file_path)
        if "openapi" not in document:
            _fail(f"Missing 'openapi' key in {file_path.name}")
        _lint_openapi_route_coverage(file_path, document)


def lint_asyncapi() -> None:
    files = sorted(ASYNCAPI_DIR.glob("*.yml")) + sorted(ASYNCAPI_DIR.glob("*.yaml"))
    if not files:
        _fail("No AsyncAPI files found")
    for file_path in files:
        if ASYNCAPI_PATTERN.match(file_path.name) is None:
            _fail(f"Invalid AsyncAPI file name: {file_path.name}")
        document = _load_yaml(file_path)
        if "asyncapi" not in document:
            _fail(f"Missing 'asyncapi' key in {file_path.name}")
        channels = document.get("channels", {})
        for channel_name in channels:
            if EVENT_PATTERN.match(channel_name) is None:
                _fail(f"Invalid event channel naming: {channel_name}")


def lint_jsonschema() -> None:
    files = sorted(JSONSCHEMA_DIR.glob("*.json"))
    if not files:
        _fail("No JSON Schema files found")
    for file_path in files:
        if JSONSCHEMA_PATTERN.match(file_path.name) is None:
            _fail(f"Invalid JSON Schema file name: {file_path.name}")
        document = _load_json(file_path)
        if "$schema" not in document:
            _fail(f"Missing '$schema' key in {file_path.name}")


def main() -> int:
    lint_openapi()
    lint_asyncapi()
    lint_jsonschema()
    print("[contract-lint] All contracts passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
