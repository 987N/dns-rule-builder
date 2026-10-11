"""Render a review-only Mihomo policy shell around a verified rule release."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
from typing import Any

import yaml

from .engine import BuildError, verify_manifest


PLACEHOLDERS = {"DIRECT", "REJECT"}


def _load_yaml(path: Path, label: str) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BuildError(f"cannot read {label}: {type(exc).__name__}") from exc


def validate_policy_template(project_root: Path, template_path: Path | None = None) -> dict[str, Any]:
    root = project_root.resolve()
    template = _load_yaml(template_path or root / "config/mihomo-policy-template.yaml", "Mihomo policy template")
    policies = _load_yaml(root / "config/policies.yaml", "policy groups")
    if not isinstance(template, dict) or template.get("schema_version") != 1:
        raise BuildError("Mihomo policy template must use schema_version 1")
    configured_policies = policies.get("policies", {}) if isinstance(policies, dict) else None
    if not isinstance(configured_policies, dict) or not all(isinstance(name, str) for name in configured_policies):
        raise BuildError("policy groups configuration has an invalid shape")
    policy_names = set(configured_policies)
    regional = template.get("regional_groups", [])
    if not isinstance(regional, list) or any(not isinstance(name, str) or not name for name in regional):
        raise BuildError("regional_groups must be a list of non-empty group names")
    if len(regional) != len(set(regional)):
        raise BuildError("regional_groups contains duplicate names")
    if set(regional) & (policy_names | {"Proxy"}):
        raise BuildError("regional group names must not collide with configured policy groups or Proxy")
    regional_names = set(regional)
    known = policy_names | regional_names | {"Proxy"}
    groups = template.get("group_members")
    if not isinstance(groups, dict) or set(groups) != known:
        raise BuildError("policy template must define Proxy and every configured policy group exactly once")
    if template.get("proxy_group_placeholder") != "__USER_SELECT_PROXY_NODE__":
        raise BuildError("policy template must preserve the unresolved proxy-node placeholder")
    edges: dict[str, set[str]] = {}
    for name, members in groups.items():
        if not isinstance(name, str) or not isinstance(members, list) or not members or any(not isinstance(x, str) or not x for x in members):
            raise BuildError(f"invalid members for policy group {name!r}")
        if len(members) != len(set(members)):
            raise BuildError(f"duplicate members in policy group {name}")
        if "DIRECT" in members:
            raise BuildError(f"policy group {name} must not fall back directly")
        edges[name] = {member for member in members if member in known}
        for member in members:
            if member not in known | PLACEHOLDERS | {template["proxy_group_placeholder"]}:
                raise BuildError(f"policy group {name} references unknown member {member}")
            if member == template["proxy_group_placeholder"]:
                if regional_names:
                    if name not in regional_names:
                        raise BuildError("unbound proxy-node placeholder is only allowed in declared regional selectors")
                elif name != "Proxy":
                    # Schema-1 templates created before regional_groups used Proxy
                    # as the direct manual node selector. Keep those templates valid.
                    raise BuildError("unbound proxy-node placeholder is only allowed in the Proxy selector")
    if regional_names:
        if "REJECT" not in groups["Proxy"] or not regional_names.issubset(groups["Proxy"]):
            raise BuildError("Proxy selector must expose every regional group and REJECT")
        for name in regional_names:
            members = groups[name]
            if template["proxy_group_placeholder"] not in members or "REJECT" not in members:
                raise BuildError(f"regional selector {name} must expose the unbound node slot and REJECT")
    elif template["proxy_group_placeholder"] not in groups["Proxy"] or "REJECT" not in groups["Proxy"]:
        raise BuildError("Proxy selector must expose the unbound node slot and REJECT")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise BuildError(f"policy group reference cycle includes {node}")
        if node in visited:
            return
        visiting.add(node)
        for child in edges[node]:
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for group in groups:
        visit(group)
    return template


def _read_release_inputs(release_root: Path) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    release = release_root.resolve()
    manifest = verify_manifest(release)
    mihomo = release / "mihomo"
    provider_doc = _load_yaml(mihomo / "rule-providers.yaml", "release rule-providers")
    rules_doc = _load_yaml(mihomo / "rules.yaml", "release rules")
    providers = provider_doc.get("rule-providers") if isinstance(provider_doc, dict) else None
    rules = rules_doc.get("rules") if isinstance(rules_doc, dict) else None
    if not isinstance(providers, dict) or not isinstance(rules, list) or not all(isinstance(x, str) for x in rules):
        raise BuildError("release Mihomo provider or rule document has an invalid shape")
    expected_order = manifest["mihomo_rule_order"]
    if list(providers) != expected_order:
        raise BuildError("release providers do not match the manifest rule order")
    for name, provider in providers.items():
        if not isinstance(provider, dict) or not isinstance(provider.get("path"), str):
            raise BuildError(f"provider {name} has no safe local path")
        raw_path = provider["path"]
        relative = PurePosixPath(raw_path)
        if "\\" in raw_path or any(char in raw_path for char in ",\r\n") or relative.is_absolute() or ".." in relative.parts:
            raise BuildError(f"provider {name} path escapes HomeDir")
        path = mihomo.joinpath(*relative.parts).resolve()
        if not path.is_relative_to(mihomo.resolve()) or not path.is_file() or path.is_symlink():
            raise BuildError(f"provider {name} file is missing or unsafe")
    if not rules:
        raise BuildError("release rule sequence is empty")
    seen_regular = False
    provider_order = []
    for rule in rules:
        parts = rule.split(",")
        if len(parts) != 3 or parts[0] != "RULE-SET" or parts[1] not in providers:
            raise BuildError(f"unsupported or unresolved generated rule: {rule}")
        provider_order.append(parts[1])
        if rule.startswith("RULE-SET,CustomExact-"):
            if seen_regular:
                raise BuildError("CustomExact providers must precede every regular provider")
        else:
            seen_regular = True
    if provider_order != expected_order:
        raise BuildError("generated Mihomo rules do not exactly match the manifest provider order")
    return manifest, provider_doc, rules


def render_policy_config(
    project_root: Path,
    release_root: Path,
    output_path: Path,
    *,
    test_proxy_reject: bool = False,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Write a full Mihomo HomeDir config; production output remains unbound."""
    root = project_root.resolve()
    release = release_root.resolve()
    home = release / "mihomo"
    destination = output_path.resolve()
    if destination.is_relative_to(release):
        raise BuildError("rendered policy bundle must be outside the immutable rule release")
    if output_path.exists() or output_path.is_symlink():
        raise BuildError("render destination already exists")
    template = validate_policy_template(root, template_path)
    manifest, provider_doc, rules = _read_release_inputs(release)
    group_members = {key: list(value) for key, value in template["group_members"].items()}
    if test_proxy_reject:
        group_members["Proxy"] = ["REJECT"]
        placeholder = template["proxy_group_placeholder"]
        for name in template.get("regional_groups", []):
            if placeholder in group_members[name]:
                group_members[name] = ["REJECT"]
    group_names = set(group_members)
    active_rules = []
    for rule in rules:
        _, provider_name, group = rule.split(",")
        if group not in group_names:
            raise BuildError(f"rule {provider_name} references missing policy group {group}")
        active_rules.append(rule)
    for name, members in group_members.items():
        if any(member in group_names and member == name for member in members):
            raise BuildError(f"policy group {name} references itself")
    output = {
        "mixed-port": 17890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "external-controller": "127.0.0.1:19090",
        "proxies": [],
        "proxy-groups": [{"name": name, "type": "select", "proxies": members} for name, members in group_members.items()],
        "rule-providers": provider_doc["rule-providers"],
        "rules": [*active_rules, "MATCH,DIRECT"],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    home_dir = destination.parent
    for name, provider in provider_doc["rule-providers"].items():
        relative = PurePosixPath(provider["path"])
        source_provider = release.joinpath("mihomo", *relative.parts)
        target_provider = home_dir.joinpath(*relative.parts)
        target_provider.parent.mkdir(parents=True, exist_ok=True)
        if target_provider.exists() or target_provider.is_symlink():
            raise BuildError(f"render target already contains provider file {target_provider.name}")
        shutil.copyfile(source_provider, target_provider)
    destination.write_text(yaml.safe_dump(output, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {
        "release_id": manifest["release_id"],
        "output": str(destination),
        "test_proxy_reject": test_proxy_reject,
        "deployable": False,
        "blocker": "production proxy-node membership has not been selected" if not test_proxy_reject else "isolated test profile routes Proxy to REJECT",
        "provider_count": len(provider_doc["rule-providers"]),
        "rule_count": len(output["rules"]),
    }
