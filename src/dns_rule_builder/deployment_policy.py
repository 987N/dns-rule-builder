"""Conservative, release-bound automatic deployment decisions shared by worker/UI."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

import yaml

from .engine import BuildError, verify_manifest
from .target_attestation import check_preparation_candidate

POLICY_VERSION = 1


def automatic_enabled(path: Path | None) -> bool:
    """Neither legacy enablement nor an absent maintenance flag admits automation."""
    if path is None:
        return False
    try:
        from .target_v4 import load_target_specs
        load_target_specs(path)
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        return (isinstance(config, dict) and config.get("maintenance_disabled") is False
                and config.get("automatic_deployment_enabled") is True)
    except (BuildError, OSError, ValueError, yaml.YAMLError):
        return False


def compare_releases(left: dict | None, right: dict) -> dict:
    """Inputs must already have passed manifest verification; counts are never truncated."""
    result = {"release_id": right["release_id"], "baseline_release_id": left.get("release_id") if left else None,
              "policy_version": POLICY_VERSION, "decision": "blocked", "reasons": [],
              "comparison_available": left is not None, "unchanged": False,
              "rule_count": right.get("statistics", {}).get("rule_count", 0),
              "added_count": 0, "removed_count": 0, "strategy_change_count": 0,
              "category_strategy_change_count": 0, "added_rules": [], "removed_rules": [],
              "strategy_changes": [], "category_strategy_changes": [], "category_counts": [],
              "deployment_blockers": right.get("deployment_blockers", [])}
    gate_reasons = []
    try:
        check_preparation_candidate(right)
    except BuildError:
        gate_reasons = ["候选未通过现有规则、来源或许可证检查"]
    if left is None:
        result["reasons"] = gate_reasons + ["无法确认两端一致且本机可验证的运行基准，请人工检查"]
        return result
    if left["release_id"] == right["release_id"]:
        if gate_reasons:
            result["reasons"] = gate_reasons
            return result
        result.update(decision="auto_allowed", unchanged=True, reasons=["两端已运行此版本，无需更新"])
        return result
    required = {"normalized_rules", "code_identity", "config_identity", "sources", "automatic_policy_inputs",
                "source_capability_version", "compiler_capability_version", "license_capability_version",
                "source_approvals_sha256", "approved_exclusions_sha256", "shared_domain_reviews_sha256",
                "unsupported_dispositions_sha256", "builder_version"}
    a, b = left.get("identity_inputs", {}), right.get("identity_inputs", {})
    if (not required <= a.keys() or not required <= b.keys()
            or not isinstance(a.get("automatic_policy_inputs"), dict) or not isinstance(b.get("automatic_policy_inputs"), dict)
            or not {"force_direct", "source_configuration"} <= a["automatic_policy_inputs"].keys()
            or not {"force_direct", "source_configuration"} <= b["automatic_policy_inputs"].keys()):
        result["reasons"] = gate_reasons + ["版本缺少自动部署比较信息，请人工检查并准备所选版本"]
        return result
    before = {(r["domain"], r["match_type"], r["category"]): r for r in a["normalized_rules"]}
    after = {(r["domain"], r["match_type"], r["category"]): r for r in b["normalized_rules"]}
    added = [after[k] for k in sorted(after.keys() - before.keys())]
    removed = [before[k] for k in sorted(before.keys() - after.keys())]
    strategy = [{"domain": k[0], "match_type": k[1], "category": k[2], "from": before[k].get("policy_group"), "to": after[k].get("policy_group")}
                for k in sorted(before.keys() & after.keys()) if before[k].get("policy_group") != after[k].get("policy_group")]
    counts = Counter(k[2] for k in before)
    changes = Counter(r["category"] for r in added + removed)
    category_counts = [{"category": key, "baseline_count": counts[key], "changed_count": changes[key],
                        "change_fraction": changes[key] / counts[key] if counts[key] else None} for key in sorted(counts.keys() | changes.keys())]
    old_categories = {r["category"]: r for r in left.get("category_status", [])}
    new_categories = {r["category"]: r for r in right.get("category_status", [])}
    category_strategy = [{"category": k, "from": old_categories[k].get("policy_group"), "to": new_categories[k].get("policy_group")}
                         for k in sorted(old_categories.keys() & new_categories.keys()) if old_categories[k].get("policy_group") != new_categories[k].get("policy_group")]
    result.update(added_count=len(added), removed_count=len(removed), rule_count_delta=len(added) - len(removed),
                  changed_count=len(added) + len(removed), baseline_count=len(before),
                  change_fraction=(len(added) + len(removed)) / len(before) if before else None,
                  strategy_change_count=len(strategy), category_strategy_change_count=len(category_strategy),
                  category_counts=category_counts, added_rules=added[:50], removed_rules=removed[:50],
                  strategy_changes=strategy[:50], category_strategy_changes=category_strategy[:50],
                  truncated={"added_rules": len(added) > 50, "removed_rules": len(removed) > 50, "strategy_changes": len(strategy) > 50})
    reasons = []
    if any(a[k] != b[k] for k in ("code_identity", "builder_version", "source_capability_version", "compiler_capability_version", "license_capability_version")):
        reasons.append("构建程序或规则语义能力变化")
    # sources.yaml contains the routinely refreshed upstream revision; compare its
    # semantic source descriptors below instead of its whole-file hash.
    configs = lambda x: {k: v for k, v in x["config_identity"].items() if k != "config/sources.yaml"}
    if configs(a) != configs(b):
        reasons.append("类别开关、策略、优先级或规则配置变化")
    source_profiles = lambda x: {sid: {k: row.get(k) for k in ("repository", "kind", "adapter_version", "selected_refs")}
                                 for sid, row in x["sources"].items()}
    if source_profiles(a) != source_profiles(b) or a.get("source_license_provenance") != b.get("source_license_provenance"):
        reasons.append("来源、来源引用或解析方式变化")
    if a["automatic_policy_inputs"] != b["automatic_policy_inputs"]:
        reasons.append("强制直连或自动部署语义配置变化")
    if any(a[k] != b[k] for k in ("source_approvals_sha256", "approved_exclusions_sha256", "shared_domain_reviews_sha256", "unsupported_dispositions_sha256")):
        reasons.append("来源审核、排除、共享域名或不支持规则处置变化")
    category_shape = lambda rows: {k: {f: row.get(f) for f in ("provider_name", "policy_group", "priority", "source_file")} for k, row in rows.items()}
    if category_shape(old_categories) != category_shape(new_categories) or strategy:
        reasons.append("启用类别、分流策略或优先级变化")
    total = len(added) + len(removed)
    if total > 50 and (not before or total * 100 > len(before) * 10):
        reasons.append("总体规则增删超过 10% 且超过 50 条")
    if any(row["changed_count"] > 20 and (not row["baseline_count"] or row["changed_count"] * 100 > row["baseline_count"] * 30) for row in category_counts):
        reasons.append("单类别规则增删超过 30% 且超过 20 条")
    result.update(decision="blocked" if gate_reasons else "approval_required" if reasons else "auto_allowed",
                  reasons=gate_reasons + reasons if gate_reasons or reasons else ["常规规则更新，通过自动部署判定"])
    def payload_hashes(manifest):
        return {path: digest for path, digest in manifest.get("artifact_sha256", {}).items()
                if path == "smartdns/proxy-domains.txt" or path.startswith("mihomo/")}
    old_payload, new_payload = payload_hashes(left), payload_hashes(right)
    required_payload = {"smartdns/proxy-domains.txt", "mihomo/rule-providers.yaml", "mihomo/rules.yaml"}
    if (not gate_reasons and not reasons and total == 0 and required_payload <= old_payload.keys()
            and old_payload == new_payload):
        result.update(unchanged=True, content_unchanged=True, reasons=["规则内容和配置未变化，无需重启服务"])
    return result


def observe_and_compare(coordinator, data_root: Path, right: dict) -> tuple[dict, dict]:
    """Read devices now; stale UI observations are never deployment authority."""
    actual = {}
    try:
        actual = {spec.target_id: coordinator.handlers[spec.target_id].current_release() for spec in coordinator.specs}
        values = set(actual.values())
        baseline = next(iter(values)) if len(actual) == 2 and len(values) == 1 else None
        if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-f]{64}", baseline):
            baseline = None
        left = verify_manifest(data_root / "releases" / baseline) if baseline else None
        if left is not None and left.get("release_id") != baseline:
            left = None
    except Exception:
        # A transport failure or an unfamiliar target response is an unknown
        # baseline, never evidence authorizing an automatic deployment.
        left = None
    return compare_releases(left, right), actual
