"""Deterministic Mihomo provider ordering and provenance map compiler."""

from __future__ import annotations

import re
from typing import Any

from .engine import BuildError


def _overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a, b = left["domain"], right["domain"]
    ta, tb = left["match_type"], right["match_type"]
    if ta == tb == "EXACT":
        return a == b
    if ta == tb == "SUFFIX":
        return a == b or a.endswith("." + b) or b.endswith("." + a)
    exact, suffix = (left, right) if ta == "EXACT" else (right, left)
    return exact["domain"] == suffix["domain"] or exact["domain"].endswith("." + suffix["domain"])


def _strictly_narrower(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """True when left has a strict subset of right's matching domain space."""
    a, b = left["domain"], right["domain"]
    ta, tb = left["match_type"], right["match_type"]
    if ta == tb == "SUFFIX":
        return a != b and a.endswith("." + b)
    if ta == "EXACT" and tb == "SUFFIX":
        return a == b or a.endswith("." + b)
    return False


def _node_key(rule: dict[str, Any]) -> tuple[Any, ...]:
    return (bool(rule.get("custom")), rule["category"], rule["policy_group"], rule["domain"], rule["match_type"])


def _sort_key(node: dict[str, Any]) -> tuple[Any, ...]:
    first = node["rules"][0]
    custom_exact = first["custom"] and first["match_type"] == "EXACT"
    return (
        0 if custom_exact else 1,
        first["priority"],
        first["category"].casefold(),
        -len(first["domain"]),
        0 if first["match_type"] == "EXACT" else 1,
        first["domain"],
        first["custom"],
    )


def compile_provider_plan(normalized_rules: list[dict[str, Any]], categories: dict[str, Any]) -> dict[str, Any]:
    """Order semantic rules, then compact adjacent category/policy runs.

    The returned member indices partition ``normalized_rules`` exactly once.
    Source provenance stays in normalized rules while output providers contain
    only unique rule syntax.
    """
    nodes_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for index, rule in enumerate(normalized_rules):
        key = _node_key(rule)
        node = nodes_by_key.setdefault(key, {"key": key, "rules": [], "indices": []})
        node["rules"].append(rule)
        node["indices"].append(index)
    nodes = list(nodes_by_key.values())
    edges: dict[tuple[Any, ...], set[tuple[Any, ...]]] = {node["key"]: set() for node in nodes}
    indegree = {node["key"]: 0 for node in nodes}
    conflicts: list[dict[str, Any]] = []

    def add_edge(before: dict[str, Any], after: dict[str, Any]) -> None:
        a, b = before["key"], after["key"]
        if a != b and b not in edges[a]:
            edges[a].add(b)
            indegree[b] += 1

    for i, left_node in enumerate(nodes):
        left = left_node["rules"][0]
        for right_node in nodes[i + 1:]:
            right = right_node["rules"][0]
            if not _overlap(left, right):
                continue
            left_custom_exact = left["custom"] and left["match_type"] == "EXACT"
            right_custom_exact = right["custom"] and right["match_type"] == "EXACT"
            before = after = None
            reason = None
            if left_custom_exact != right_custom_exact:
                before, after = (left_node, right_node) if left_custom_exact else (right_node, left_node)
                reason = "custom EXACT provider precedes regular providers"
            elif _strictly_narrower(left, right) or _strictly_narrower(right, left):
                before, after = (left_node, right_node) if _strictly_narrower(left, right) else (right_node, left_node)
                reason = "more-specific rule precedes broader rule"
            elif left["policy_group"] == right["policy_group"]:
                continue
            elif left["domain"] == right["domain"] and left["match_type"] == right["match_type"]:
                if left["priority"] != right["priority"]:
                    before, after = (left_node, right_node) if left["priority"] < right["priority"] else (right_node, left_node)
                    reason = "same-scope overlap resolved by explicit priority"
            if before is not None:
                add_edge(before, after)
                if left["policy_group"] != right["policy_group"]:
                    conflicts.append({
                        "category_a": left["category"], "policy_a": left["policy_group"], "domain_a": left["domain"], "match_a": left["match_type"],
                        "category_b": right["category"], "policy_b": right["policy_group"], "domain_b": right["domain"], "match_b": right["match_type"],
                        "resolved": True, "winner": before["rules"][0]["category"], "shadowed": after["rules"][0]["category"], "reason": reason,
                    })
            else:
                conflicts.append({
                    "category_a": left["category"], "policy_a": left["policy_group"], "domain_a": left["domain"], "match_a": left["match_type"],
                    "category_b": right["category"], "policy_b": right["policy_group"], "domain_b": right["domain"], "match_b": right["match_type"],
                    "resolved": False, "winner": None, "shadowed": None, "reason": "overlapping policies have equal priority or unresolved same-scope order",
                })

    ready = sorted((node for node in nodes if indegree[node["key"]] == 0), key=_sort_key)
    ordered: list[dict[str, Any]] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for target in edges[node["key"]]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(nodes_by_key[target])
                ready.sort(key=_sort_key)
    cyclic = [node for node in nodes if indegree[node["key"]] > 0]
    if cyclic:
        ordered.extend(sorted(cyclic, key=_sort_key))

    custom_groups: dict[str, dict[str, Any]] = {}
    regular_nodes: list[dict[str, Any]] = []
    for node in ordered:
        rule = node["rules"][0]
        if rule["custom"] and rule["match_type"] == "EXACT":
            category = rule["category"]
            group = custom_groups.setdefault(category, {"category": category, "policy_group": rule["policy_group"], "member_indices": []})
            group["member_indices"].extend(node["indices"])
        else:
            regular_nodes.append(node)

    runs: list[dict[str, Any]] = []
    for node in regular_nodes:
        rule = node["rules"][0]
        if runs and runs[-1]["category"] == rule["category"] and runs[-1]["policy_group"] == rule["policy_group"]:
            runs[-1]["member_indices"].extend(node["indices"])
        else:
            runs.append({"category": rule["category"], "policy_group": rule["policy_group"], "member_indices": list(node["indices"])})

    run_count: dict[str, int] = {}
    for run in runs:
        run_count[run["category"]] = run_count.get(run["category"], 0) + 1
    run_number: dict[str, int] = {}
    providers: list[dict[str, Any]] = []
    for category, group in custom_groups.items():
        cfg = categories[category]
        providers.append({"name": f"CustomExact-{cfg['provider_name']}", "kind": "custom-exact", "policy_group": group["policy_group"], "categories": [category], "member_indices": sorted(group["member_indices"])})
    for run in runs:
        category = run["category"]
        cfg = categories[category]
        run_number[category] = run_number.get(category, 0) + 1
        name = cfg["provider_name"] if run_count[category] == 1 else f"{cfg['provider_name']}-Part-{run_number[category]:02d}"
        providers.append({"name": name, "kind": "regular", "policy_group": run["policy_group"], "categories": [category], "member_indices": sorted(run["member_indices"])})

    names = [provider["name"].casefold() for provider in providers]
    if len(names) != len(set(names)) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", provider["name"]) for provider in providers):
        raise BuildError("generated provider fragments have unsafe or duplicate names")
    member_indices = [index for provider in providers for index in provider["member_indices"]]
    if sorted(member_indices) != list(range(len(normalized_rules))):
        raise BuildError("provider plan does not map every normalized rule exactly once")
    provider_cycles = []
    if cyclic:
        provider_cycles.append({"categories": sorted({node["rules"][0]["category"] for node in cyclic}), "reason": "semantic specificity constraints form a rule-node cycle"})
    edge_rows = []
    for source, targets in edges.items():
        for target in targets:
            before, after = nodes_by_key[source]["rules"][0], nodes_by_key[target]["rules"][0]
            edge_rows.append({"before": {"category": before["category"], "policy_group": before["policy_group"], "domain": before["domain"], "match_type": before["match_type"], "custom": before["custom"]}, "after": {"category": after["category"], "policy_group": after["policy_group"], "domain": after["domain"], "match_type": after["match_type"], "custom": after["custom"]}})
    edge_rows.sort(key=lambda row: (row["before"]["category"], row["before"]["domain"], row["after"]["category"], row["after"]["domain"]))
    return {"providers": providers, "order": [item["name"] for item in providers], "conflicts": conflicts, "cycles": provider_cycles, "precedence_edges": edge_rows}
