"""Read the public, code-managed service catalog for builds and the Web API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class CatalogError(ValueError):
    pass


def load_service_catalog(project_root: Path) -> dict[str, Any]:
    path = project_root / "config/service-catalog.yaml"
    if not path.exists():
        return {"schema_version": 1, "services": {}}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"cannot load service catalog: {type(exc).__name__}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("services"), dict):
        raise CatalogError("service catalog must be a schema_version 1 mapping")
    for service_id, spec in value["services"].items():
        if not isinstance(service_id, str) or not isinstance(spec, dict):
            raise CatalogError("service catalog entries must be named mappings")
        if spec.get("review_state") not in {"pending", "approved", "manual_missing"}:
            raise CatalogError(f"service catalog entry {service_id} has invalid review_state")
        if not isinstance(spec.get("default_enabled"), bool):
            raise CatalogError(f"service catalog entry {service_id} must define default_enabled")
        if spec.get("route_intent") not in {"proxy", "direct", "review"}:
            raise CatalogError(f"service catalog entry {service_id} has invalid route_intent")
    return value


def service_metadata_for_api(project_root: Path) -> list[dict[str, Any]]:
    """Return bounded, non-secret catalog fields for a future management UI."""
    catalog = load_service_catalog(project_root)
    try:
        categories = yaml.safe_load((project_root / "config/categories.yaml").read_text(encoding="utf-8")).get("categories", {})
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"cannot load categories for service catalog: {type(exc).__name__}") from exc
    categories_for_service: dict[str, list[str]] = {}
    for category, cfg in categories.items():
        if isinstance(cfg, dict) and cfg.get("catalog_service_id"):
            categories_for_service.setdefault(cfg["catalog_service_id"], []).append(category)
    result = []
    for service_id, spec in sorted(catalog["services"].items()):
        result.append({
            "id": service_id,
            "category_id": (categories_for_service.get(service_id) or [None])[0],
            "category_ids": categories_for_service.get(service_id, []),
            "label": spec.get("label", service_id),
            "use_group": spec.get("use_group"),
            "policy_group": spec.get("policy_group"),
            "route_intent": spec.get("route_intent"),
            "review_state": spec.get("review_state"),
            "default_enabled": spec.get("default_enabled"),
            "implementation_status": spec.get("implementation_status", "candidate"),
            "missing": spec.get("missing", False),
            "upstream_key_missing": spec.get("upstream_key_missing", False),
            "coverage_status": spec.get("coverage_status", "unreviewed"),
            "risks": list(spec.get("risks", [])),
            "dependencies": list(spec.get("dependencies", [])),
            "source_refs": list(spec.get("source_refs", [])),
            "selected_ref": spec.get("selected_ref"),
            "source_candidates": list(spec.get("source_candidates", [])),
            "evidence": list(spec.get("evidence", [])),
        })
    return result
