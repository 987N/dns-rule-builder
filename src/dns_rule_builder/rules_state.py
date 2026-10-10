"""Validation and application of the data-only rules-data/state.yaml snapshot."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .engine import BuildError, load_yaml


def apply_state(project_root: Path, state_file: Path) -> dict[str, Any]:
    raw = state_file.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise BuildError("rules-data state exceeds the 2 MiB limit")
    try:
        state = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise BuildError(f"invalid rules-data YAML: {exc}") from exc
    if not isinstance(state, dict) or set(state) != {"schema_version", "categories", "rules", "force_direct", "approved_exclusions", "shared_domain_reviews"}:
        raise BuildError("rules-data state must contain exactly the schema v1 fields")
    if state.get("schema_version") != 1:
        raise BuildError("unsupported rules-data state schema")
    if not isinstance(state.get("categories"), dict) or not isinstance(state.get("rules"), list) or not isinstance(state.get("force_direct"), list) or not isinstance(state.get("approved_exclusions"), list) or not isinstance(state.get("shared_domain_reviews"), list):
        raise BuildError("rules-data state has invalid field types")
    if len(state["approved_exclusions"]) > 1000:
        raise BuildError("approved_exclusions exceeds the 1000-entry limit")

    root = project_root.resolve()
    category_path = root / "config/categories.yaml"
    config = load_yaml(category_path)
    categories = config.get("categories")
    if not isinstance(categories, dict):
        raise BuildError("main category configuration is invalid")
    for category, override in state["categories"].items():
        if category not in categories or not isinstance(override, dict) or set(override) - {"enabled", "policy_group", "priority"}:
            raise BuildError(f"invalid rules-data category override: {category!r}")
        if "enabled" in override and not isinstance(override["enabled"], bool):
            raise BuildError(f"category {category}: enabled must be boolean")
        if "policy_group" in override and (not isinstance(override["policy_group"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", override["policy_group"])):
            raise BuildError(f"category {category}: unsafe policy group")
        if "priority" in override and (not isinstance(override["priority"], int) or isinstance(override["priority"], bool)):
            raise BuildError(f"category {category}: priority must be an integer")
        categories[category].update(override)
    policies = load_yaml(root / "config/policies.yaml").get("policies", {})
    for category, override in state["categories"].items():
        group = categories[category].get("policy_group")
        if group not in policies:
            raise BuildError(f"category {category}: unknown logical policy group {group!r}")

    configured_reviews = {item["id"] for item in load_yaml(root / "config/shared-domains.yaml").get("shared_domains", [])}
    seen_reviews = set()
    for review in state["shared_domain_reviews"]:
        if not isinstance(review, dict) or set(review) - {"id", "status", "reviewed_by", "reviewed_at", "rationale"}:
            raise BuildError("shared-domain review entries have invalid fields")
        identity = review.get("id")
        if identity not in configured_reviews or identity in seen_reviews:
            raise BuildError(f"unknown or duplicate shared-domain review ID: {identity!r}")
        seen_reviews.add(identity)
        if review.get("status") != "approved" or not all(isinstance(review.get(field), str) and review[field].strip() for field in ("reviewed_by", "reviewed_at", "rationale")):
            raise BuildError(f"shared-domain review {identity}: explicit approval, reviewer, date, and rationale are required")

    custom_path = root / "data/custom-rules.yaml"
    custom_path.parent.mkdir(parents=True, exist_ok=True)
    custom = {"rules": state["rules"], "force_direct": state["force_direct"]}
    custom_path.write_text(yaml.safe_dump(custom, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (root / "data/shared-domain-reviews.yaml").write_text(yaml.safe_dump({"reviews": state["shared_domain_reviews"]}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (root / "data/approved-exclusions.yaml").write_text(yaml.safe_dump({"exclusions": state["approved_exclusions"]}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    category_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"schema_version": 1, "category_overrides": len(state["categories"]), "custom_rules": len(state["rules"]), "force_direct": len(state["force_direct"]), "approved_exclusions": len(state["approved_exclusions"]), "shared_domain_reviews": len(state["shared_domain_reviews"])}
