"""Validation and application of the data-only rules-data/state.yaml snapshot."""

from __future__ import annotations

import re
import copy
from pathlib import Path
from typing import Any

import yaml

from .engine import BuildError, load_yaml
from .source_approvals import ApprovalError, validate_rules_data_dispositions


def normalize_legacy_tiktok_state(state: object) -> tuple[object, list[str]]:
    """Drop only a disabled category alias; preserve disabled legacy rules with a warning."""
    if not isinstance(state, dict):
        return state, []
    state_categories = state.get("categories")
    state_rules = state.get("rules")
    if not (isinstance(state_categories, dict) and "tiktok" in state_categories) and not (isinstance(state_rules, list) and any(isinstance(item, dict) and item.get("category") == "tiktok" for item in state_rules)):
        return state, []
    normalized = copy.deepcopy(state)
    warnings: list[str] = []
    categories = normalized.get("categories")
    if isinstance(categories, dict) and "tiktok" in categories:
        override = categories["tiktok"]
        if not isinstance(override, dict) or override.get("enabled") is not False or set(override) - {"enabled"}:
            raise BuildError("旧 TikTok 分类只能以 enabled:false 兼容读取；启用或映射必须先手工迁移")
        categories.pop("tiktok")
        warnings.append("已兼容忽略旧的 TikTok 禁用分类覆盖；机器目录不再包含 TikTok。")
    rules = normalized.get("rules")
    if isinstance(rules, list):
        legacy = [item for item in rules if isinstance(item, dict) and item.get("category") == "tiktok"]
        for item in legacy:
            if item.get("enabled", True) is not False:
                raise BuildError("旧 TikTok 自定义规则仍为启用状态；请先手工迁移，不能静默删除")
        if legacy:
            warnings.append(f"保留了 {len(legacy)} 条已停用的旧 TikTok 自定义规则；请导出并手工迁移或删除。")
    return normalized, warnings


def apply_state(project_root: Path, state_file: Path) -> dict[str, Any]:
    raw = state_file.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise BuildError("rules-data state exceeds the 2 MiB limit")
    try:
        state = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise BuildError(f"invalid rules-data YAML: {exc}") from exc
    state, _legacy_warnings = normalize_legacy_tiktok_state(state)
    required_fields = {"schema_version", "categories", "rules", "force_direct", "approved_exclusions", "shared_domain_reviews"}
    if not isinstance(state, dict) or frozenset(state) not in {frozenset(required_fields), frozenset(required_fields | {"unsupported_dispositions"})}:
        raise BuildError("rules-data state must contain exactly the schema v1 fields, with optional unsupported_dispositions")
    state.setdefault("unsupported_dispositions", [])
    if state.get("schema_version") != 1:
        raise BuildError("unsupported rules-data state schema")
    if not isinstance(state.get("categories"), dict) or not isinstance(state.get("rules"), list) or not isinstance(state.get("force_direct"), list) or not isinstance(state.get("approved_exclusions"), list) or not isinstance(state.get("shared_domain_reviews"), list):
        raise BuildError("rules-data state has invalid field types")
    if len(state["approved_exclusions"]) > 1000:
        raise BuildError("approved_exclusions exceeds the 1000-entry limit")

    root = project_root.resolve()
    try:
        validate_rules_data_dispositions(root, state["unsupported_dispositions"])
    except ApprovalError as exc:
        raise BuildError(str(exc)) from exc
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
    (root / "data/unsupported-dispositions.yaml").write_text(yaml.safe_dump({"dispositions": state["unsupported_dispositions"]}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    category_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"schema_version": 1, "category_overrides": len(state["categories"]), "custom_rules": len(state["rules"]), "force_direct": len(state["force_direct"]), "approved_exclusions": len(state["approved_exclusions"]), "shared_domain_reviews": len(state["shared_domain_reviews"]), "unsupported_dispositions": len(state["unsupported_dispositions"])}
