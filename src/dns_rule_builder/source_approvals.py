"""Strict loading and signature helpers for reviewed source decisions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any

import yaml


class ApprovalError(ValueError):
    pass


def raw_sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def approval_scope_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def shared_scope_for_category(ledger: dict[str, Any], category: str, shared_domains: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    baseline = ledger.get("shared_scope_baseline", {})
    if not isinstance(baseline, dict):
        raise ApprovalError("shared_scope_baseline must be a mapping")
    return {
        "excluded_exact_rules": sorted(
            (item for item in baseline.get("excluded_exact_rules", []) if item.get("category") == category),
            key=lambda item: (item.get("source_file", ""), item.get("domain", ""), item.get("raw_sha256", "")),
        ),
        "service_exact_additions": sorted(set(baseline.get("service_exact_additions", {}).get(category, []))),
        "broad_shared_suffixes_prohibited": sorted(set(baseline.get("broad_shared_suffixes_prohibited", []))),
        "recognized_shared_roots": sorted(set(baseline.get("recognized_shared_roots", []))),
        "configured_shared_domains": sorted(
            ({key: item.get(key) for key in ("id", "domain", "match_type", "expected_categories", "risk")} for item in (shared_domains or [])),
            key=lambda item: str(item.get("id", "")),
        ),
    }


def _required_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


_REVIEWABLE_UNSUPPORTED_TYPES = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD",
    "IP-CIDR", "IP-CIDR6", "IP-ASN", "GEOIP", "DST-PORT", "SRC-IP",
    "PROCESS-NAME", "PROCESS-PATH", "USER-AGENT", "URL-REGEX", "REGEXP",
    "KEYWORD", "AND", "OR", "NOT", "RULE-SET", "SCRIPT",
}


def validate_unsupported_dispositions(records: Any, *, data_only: bool = False) -> list[dict[str, Any]]:
    """Validate exact, signed review records from the code ledger or rules-data.

    Data-branch records may acknowledge only known rule syntax that this builder
    cannot represent. They cannot add arbitrary parser type names.
    """
    if not isinstance(records, list) or len(records) > 1000:
        raise ApprovalError("unsupported dispositions must be a list of at most 1000 entries")
    signatures: set[tuple[str, ...]] = set()
    for item in records:
        if not isinstance(item, dict):
            raise ApprovalError("unsupported disposition must be a mapping")
        required = ("category", "source_id", "source_key", "source_file", "raw_sha256", "type", "reason", "review_status", "disposition", "reviewer", "reviewed_at")
        if not all(_required_text(item.get(key)) for key in required):
            raise ApprovalError("unsupported disposition lacks exact signature or review metadata")
        allowed_fields = set(required) | {"raw", "line", "source_revision", "note"}
        if set(item) - allowed_fields:
            raise ApprovalError("unsupported disposition contains unknown fields")
        if len(item["reviewer"]) > 100 or len(item["reason"]) > 500 or ("raw" in item and len(item["raw"]) > 4096):
            raise ApprovalError("unsupported disposition review text exceeds limits")
        if not re.fullmatch(r"[0-9a-f]{64}", item["raw_sha256"]):
            raise ApprovalError("unsupported disposition raw hash is invalid")
        if "raw" in item and (not _required_text(item["raw"]) or raw_sha256(item["raw"].strip()) != item["raw_sha256"]):
            raise ApprovalError("unsupported disposition raw text does not match its hash")
        try:
            reviewed_at = item["reviewed_at"]
            parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
            if data_only and parsed.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ApprovalError("unsupported disposition review time must include a timezone") from exc
        allowed = {("approved_signature", "report_only_nonrepresentable"), ("reviewed_invalid_keep_reported", "keep_rejected_in_report")}
        if (item["review_status"], item["disposition"]) not in allowed:
            raise ApprovalError("unsupported disposition may not approve this syntax signature")
        if data_only and item["type"] not in _REVIEWABLE_UNSUPPORTED_TYPES:
            raise ApprovalError("rules-data disposition cannot approve an unknown rule type")
        if data_only and set(item) - (set(required) | {"raw"}):
            raise ApprovalError("rules-data disposition contains fields outside the supported review schema")
        signature = unsupported_signature(item)
        if signature in signatures:
            raise ApprovalError("duplicate unsupported disposition signature")
        signatures.add(signature)
    return records


def load_source_approvals(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        empty = {"schema_version": 1, "source_profiles": [], "approved_exclusions": [], "unsupported_dispositions": []}
        return empty, hashlib.sha256(b"source-approvals: none\n").hexdigest()
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ApprovalError("source approval ledger exceeds the 4 MiB limit")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ApprovalError("source approval ledger is invalid YAML") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ApprovalError("source approval ledger must use schema_version 1")
    for field in ("source_profiles", "approved_exclusions", "unsupported_dispositions"):
        if not isinstance(data.get(field, []), list):
            raise ApprovalError(f"source approval ledger {field} must be a list")
    source_records = data.get("sources", {})
    if not isinstance(source_records, dict):
        raise ApprovalError("source approval ledger sources must be a mapping")
    for source_id, source in source_records.items():
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source_id) or not isinstance(source, dict):
            raise ApprovalError("source license profile entry is malformed")
        if not all(_required_text(source.get(key)) for key in ("license", "license_file", "license_scope")):
            raise ApprovalError(f"source license profile lacks license scope: {source_id}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("license_sha256", ""))):
            raise ApprovalError(f"source license profile hash is invalid: {source_id}")
        if not isinstance(source.get("evidence"), list) or not source["evidence"] or not all(_required_text(item) for item in source["evidence"]):
            raise ApprovalError(f"source license profile evidence is missing: {source_id}")
        if bool(source.get("notice_file")) != bool(source.get("notice_sha256")):
            raise ApprovalError(f"source notice approval path/hash must be supplied together: {source_id}")
        if source.get("notice_sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(source["notice_sha256"])):
            raise ApprovalError(f"source notice profile hash is invalid: {source_id}")
    seen_profiles: set[str] = set()
    for profile in data.get("source_profiles", []):
        if not isinstance(profile, dict) or not _required_text(profile.get("profile_id")) or not _required_text(profile.get("category")):
            raise ApprovalError("source profile entries require profile_id and category")
        if profile["category"] in seen_profiles:
            raise ApprovalError(f"duplicate source approval profile for {profile['category']}")
        seen_profiles.add(profile["category"])
        if profile.get("review_status") not in {"approved", "pending"} or not isinstance(profile.get("selected_sources"), list):
            raise ApprovalError(f"invalid review status or selected_sources in profile {profile['profile_id']}")
        if profile.get("review_status") == "approved" and not all(_required_text(profile.get(key)) for key in ("reviewer", "reviewed_at")):
            raise ApprovalError(f"approved source profile lacks reviewer metadata: {profile['profile_id']}")
        seen_refs: set[tuple[str, str]] = set()
        for ref in profile["selected_sources"]:
            if not isinstance(ref, dict) or not _required_text(ref.get("source_id")) or not _required_text(ref.get("format")):
                raise ApprovalError(f"invalid selected source in profile {profile['profile_id']}")
            reference = ref.get("key", ref.get("file"))
            if not _required_text(reference):
                raise ApprovalError(f"selected source has no key/file in profile {profile['profile_id']}")
            key = (ref["source_id"], reference)
            if key in seen_refs:
                raise ApprovalError(f"duplicate selected source reference in profile {profile['profile_id']}")
            seen_refs.add(key)
            selector_hash = ref.get("include_selector_sha256")
            if selector_hash is not None and (not isinstance(selector_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", selector_hash)):
                raise ApprovalError(f"invalid include selector approval in profile {profile['profile_id']}")
        shared_hash = profile.get("shared_scope_sha256")
        if shared_hash is not None and (not isinstance(shared_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", shared_hash)):
            raise ApprovalError(f"invalid shared scope approval in profile {profile['profile_id']}")
    exclusion_signatures: set[tuple[str, ...]] = set()
    for item in data.get("approved_exclusions", []):
        if not isinstance(item, dict):
            raise ApprovalError("approved exclusion must be a mapping")
        required = ("category", "domain", "match_type", "source_id", "source_key", "source_file", "raw_sha256", "review_status", "reviewer", "reviewed_at", "reason")
        if not all(_required_text(item.get(key)) for key in required):
            raise ApprovalError("source-approved exclusion lacks precise scope or review metadata")
        if item["match_type"] not in {"EXACT", "SUFFIX"} or not re.fullmatch(r"[0-9a-f]{64}", item["raw_sha256"]) or item["review_status"] != "approved":
            raise ApprovalError("source-approved exclusion has invalid match type, raw hash, or review state")
        if "raw" in item and (not _required_text(item["raw"]) or raw_sha256(item["raw"].strip()) != item["raw_sha256"]):
            raise ApprovalError("source-approved exclusion raw text does not match its hash")
        signature = tuple(str(item[key]) for key in ("category", "source_id", "source_key", "source_file", "domain", "match_type", "raw_sha256"))
        if signature in exclusion_signatures:
            raise ApprovalError("duplicate source-approved exclusion signature")
        exclusion_signatures.add(signature)
    validate_unsupported_dispositions(data.get("unsupported_dispositions", []))
    return data, hashlib.sha256(raw).hexdigest()


def validate_rules_data_dispositions(project_root: Path, records: Any) -> list[dict[str, Any]]:
    """Validate user additions against selected, code-reviewed source scopes."""
    try:
        validated = validate_unsupported_dispositions(records, data_only=True)
        ledger, _ = load_source_approvals(project_root / "config/source-approvals.yaml")
        categories_doc = yaml.safe_load((project_root / "config/categories.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ApprovalError("cannot validate rules-data unsupported dispositions against project configuration") from exc
    categories = categories_doc.get("categories") if isinstance(categories_doc, dict) else None
    if not isinstance(categories, dict):
        raise ApprovalError("category configuration is invalid")
    profiles = {item["category"]: item for item in ledger.get("source_profiles", [])}
    code_signatures = {unsupported_signature(item) for item in ledger.get("unsupported_dispositions", [])}
    for item in validated:
        if unsupported_signature(item) in code_signatures:
            raise ApprovalError("rules-data unsupported disposition duplicates a code-reviewed signature")
        category = item["category"]
        if category not in categories:
            raise ApprovalError(f"unsupported disposition references unknown category {category!r}")
        profile = profiles.get(category)
        selected = [] if not profile else profile.get("selected_sources", [])
        matching = [ref for ref in selected if ref.get("source_id") == item["source_id"] and ref.get("key", ref.get("file")) == item["source_key"]]
        if profile is None or not matching:
            raise ApprovalError(f"unsupported disposition references an unselected source key for {category}")
        source_file = Path(item["source_file"])
        if source_file.is_absolute() or ".." in source_file.parts or any(char in item["source_file"] for char in ",\r\n"):
            raise ApprovalError("unsupported disposition contains an unsafe source file")
        source_config = (yaml.safe_load((project_root / "config/sources.yaml").read_text(encoding="utf-8")) or {}).get("sources", {}).get(item["source_id"], {})
        permitted_files: set[str] = set()
        for ref in matching:
            if source_config.get("format") == "v2fly-domain-list":
                closure = ref.get("include_closure_file_sha256", {})
                if isinstance(closure, dict):
                    permitted_files.update(closure)
            elif isinstance(ref.get("file"), str):
                name = ref["file"]
                if source_config.get("kind") == "local":
                    name = f"{source_config.get('path', '')}/{name}".strip("/")
                permitted_files.add(name)
        if item["source_file"] not in permitted_files:
            raise ApprovalError("unsupported disposition source file is outside the selected key/include closure")
    return validated


def exclusion_signature(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(item.get(key, "")) for key in ("category", "source_id", "source_key", "source_file", "domain", "match_type", "raw_sha256"))


def unsupported_signature(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(item.get(key, "")) for key in ("category", "source_id", "source_key", "source_file", "type", "raw_sha256", "reason"))
