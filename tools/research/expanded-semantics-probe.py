#!/usr/bin/env python3
"""Create local-only source syntax and binary probe inputs from locked archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from dns_rule_builder.engine import Builder, BuildError, explain, verify_manifest
from dns_rule_builder.policy_template import render_policy_config
from dns_rule_builder.rules_state import apply_state


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = ROOT / ".cache/root-expansion-sources.json"
MIHOMO_IMAGE = "metacubex/mihomo@sha256:bac1a74de365ea59270ba134016762c6be05654d55246518de256113bd1c225e"
SMARTDNS_IMAGE = "pikuzheng/smartdns@sha256:2144b28fc74db476661d5a12c5b741eb1316451bb703f9dec9e1794f98eb28ff"
REPRESENTATIVE_CATEGORIES = ("perplexity", "xai", "pornhub", "tmdb", "fanart")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_locked_sources(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("sources"), dict):
        raise BuildError("source snapshot must be schema v1 with a sources object")
    sources = snapshot["sources"]
    for source_id, source in sources.items():
        root = Path(source["source_root"]).resolve()
        if not root.is_dir():
            raise BuildError(f"locked {source_id} source root is missing: {root}")
        if source.get("kind") != "local":
            marker = root / ".source-revision"
            if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != source["revision"]:
                raise BuildError(f"locked {source_id} revision marker is missing or mismatched")
        source["root"] = str(root)
    archives = {
        "blackmatrix7": ROOT / ".cache/ios_rule_script-83f37120.tar.gz",
        "v2fly": ROOT / ".cache/v2fly-c6adacf.tar.gz",
    }
    for source_id, archive in archives.items():
        if not archive.is_file() or _sha256(archive) != sources[source_id]["archive_sha256"]:
            raise BuildError(f"locked {source_id} archive is missing or has the wrong SHA-256")
    return snapshot, sources


def _shadow_project(project: Path, categories: dict[str, Any], enabled: set[str]) -> Path:
    (project / "config").mkdir(parents=True)
    for source in (ROOT / "config").glob("*.yaml"):
        shutil.copy2(source, project / "config" / source.name)
    if (ROOT / "config/curated").exists():
        shutil.copytree(ROOT / "config/curated", project / "config/curated")
    shutil.copytree(ROOT / "src", project / "src")
    overrides = {category: {"enabled": category in enabled} for category in categories}
    state = {"schema_version": 1, "categories": overrides, "rules": [], "force_direct": [], "approved_exclusions": [], "shared_domain_reviews": []}
    (project / "rules-data").mkdir()
    state_path = project / "rules-data/state.yaml"
    state_path.write_text(yaml.safe_dump(state, allow_unicode=True, sort_keys=False), encoding="utf-8")
    apply_state(project, state_path)
    return project


def _write_all_category_syntax(project: Path, output: Path, categories: dict[str, Any], source_trees: dict[str, Any]) -> dict[str, Any]:
    """Parse each source-backed category once; skip the full O(n²) conflict scan."""
    builder = Builder(project, source_revision=source_trees["blackmatrix7"]["revision"], source_trees=source_trees)
    rules, unsupported, _hashes, category_rows = builder._load_rules()
    enabled_categories = {row["category"] for row in category_rows}
    expected = {key for key, config in categories.items() if config.get("source_file") or config.get("source_refs")}
    expected.add("foreign_general")
    if enabled_categories != expected:
        raise BuildError(f"parser omitted categories: {sorted(expected - enabled_categories)}")

    import dns_rule_builder.engine as engine

    provider_root = output / "providers"
    provider_root.mkdir(parents=True)
    grouped: dict[str, list[Any]] = {}
    for rule in rules:
        if rule.custom:
            continue
        grouped.setdefault(rule.category, []).append(rule)
    providers: dict[str, Any] = {}
    rule_rows = []
    for category in sorted(expected, key=lambda key: (categories[key]["priority"], categories[key]["provider_name"].casefold())):
        config = categories[category]
        rows = grouped.get(category, [])
        if not rows:
            continue
        name = config["provider_name"]
        provider_file = provider_root / f"{name}.yaml"
        provider_file.write_text(engine._provider_yaml(rows), encoding="utf-8")
        providers[name] = {"type": "file", "behavior": "classical", "path": f"./providers/{name}.yaml", "interval": 86400}
        rule_rows.append(f"RULE-SET,{name},{config['policy_group']}")

    (output / "rule-providers.yaml").write_text(yaml.safe_dump({"rule-providers": providers}, sort_keys=False), encoding="utf-8")
    (output / "rules.yaml").write_text(yaml.safe_dump({"rules": [*rule_rows, "MATCH,REJECT"]}, sort_keys=False), encoding="utf-8")
    policy_groups = set(builder.policies) | {"Proxy"}
    config_doc = {
        "mixed-port": 17890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": [],
        "proxy-groups": [{"name": group, "type": "select", "proxies": ["REJECT"]} for group in sorted(policy_groups)],
        "rule-providers": providers,
        "rules": [*rule_rows, "MATCH,REJECT"],
    }
    (output / "config.yaml").write_text(yaml.safe_dump(config_doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    unsupported_path = output / "unsupported.json"
    unsupported_path.write_text(json.dumps(unsupported, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    category_status = [
        {key: row[key] for key in ("category", "provider_name", "policy_group", "rule_count", "unsupported_count", "catalog_review_state", "catalog_route_intent", "status")}
        for row in category_rows
    ]
    categories_path = output / "categories.json"
    categories_path.write_text(json.dumps(category_status, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {
        "category_count": len(expected),
        "provider_count": len(providers),
        "parsed_rule_count": len(rules),
        "unsupported_count": len(unsupported),
        "unsupported_by_type": _count_types(unsupported),
        "empty_categories": sorted(expected - set(grouped)),
        "category_status": category_status,
        "unsupported_report": "all-category-syntax/unsupported.json",
        "categories_report": "all-category-syntax/categories.json",
    }


def _count_types(unsupported: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in unsupported:
        key = str(row.get("type") or row.get("reason") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _sample_domain(payload: list[str], wanted: str) -> str | None:
    prefix = f"{wanted},"
    values = sorted(row.split(",", 1)[1] for row in payload if row.startswith(prefix))
    return values[0] if values else None


def _provider_rules(release: Path) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    providers_doc = yaml.safe_load((release / "mihomo/rule-providers.yaml").read_text(encoding="utf-8"))
    rules_doc = yaml.safe_load((release / "mihomo/rules.yaml").read_text(encoding="utf-8"))
    payloads = {}
    for name, provider in providers_doc["rule-providers"].items():
        provider_path = release / "mihomo" / provider["path"].removeprefix("./")
        payloads[name] = yaml.safe_load(provider_path.read_text(encoding="utf-8")).get("payload", [])
    rules = []
    for row in rules_doc["rules"]:
        parts = row.split(",")
        if len(parts) != 3 or parts[0] != "RULE-SET":
            raise BuildError(f"unexpected generated candidate rule: {row}")
        rules.append((parts[1], parts[2]))
    return payloads, rules


def _representative_candidate(root: Path, categories: dict[str, Any], sources: dict[str, Any], build_time: str) -> tuple[Path, dict[str, Any]]:
    selected = set(REPRESENTATIVE_CATEGORIES) | {"foreign_general"}
    with tempfile.TemporaryDirectory(prefix="dns-rule-builder-r5-representative-") as work:
        project = _shadow_project(Path(work) / "project", categories, selected)
        source_trees = {key: {**meta, "root": Path(meta["root"]), "kind": yaml.safe_load((ROOT / "config/sources.yaml").read_text())["sources"][key].get("kind", "remote")} for key, meta in sources.items()}
        initial = Builder(project, source_revision=sources["blackmatrix7"]["revision"], input_snapshot_time_utc=build_time, source_trees=source_trees).build(root / "representative-build")
        payloads, _ = _provider_rules(initial.output_dir)
        perplexity = next((row.split(",", 1)[1] for row in payloads.get("Perplexity", []) if row.startswith("DOMAIN-SUFFIX,")), None)
        if not perplexity:
            raise BuildError("Perplexity representative suffix is absent")
        custom_path = project / "data/custom-rules.yaml"
        custom_path.write_text(yaml.safe_dump({"rules": [{"domain": perplexity, "match_type": "EXACT", "category": "foreign_general", "enabled": True, "note": "R5 isolated probe only"}], "force_direct": []}, allow_unicode=True, sort_keys=False), encoding="utf-8")
        result = Builder(project, source_revision=sources["blackmatrix7"]["revision"], input_snapshot_time_utc=build_time, source_trees=source_trees).build(root / "representative-build")
        rendered_dir = root / "mihomo-representative"
        rendered_dir.mkdir()
        rendered = render_policy_config(project, result.output_dir, rendered_dir / "config.yaml", test_proxy_reject=True)
        config_path = rendered_dir / "config.yaml"
        config_doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config_doc["log-level"] = "info"
        config_doc["rules"][-1] = "MATCH,REJECT"
        config_path.write_text(yaml.safe_dump(config_doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        actual = explain(result.output_dir, perplexity)
        child = explain(result.output_dir, "probe." + perplexity)
        if not actual or actual[0]["_provider"] != "CustomExact-Foreign-General":
            raise BuildError("CustomExact did not win the cross-category exact probe")
        if not child or child[0]["_provider"] != "Perplexity":
            raise BuildError("CustomExact exact rule incorrectly captured its suffix child")
        return result.output_dir, {
            "custom_exact_domain": perplexity,
        "custom_exact_winner": actual[0]["_provider"],
        "custom_exact_child_winner": child[0]["_provider"],
            "renderer_deployable": rendered["deployable"],
            "release_id": result.release_id,
        }


def run(snapshot_path: Path, output_root: Path) -> dict[str, Any]:
    snapshot, sources = _load_locked_sources(snapshot_path)
    categories = yaml.safe_load((ROOT / "config/categories.yaml").read_text(encoding="utf-8"))["categories"]
    if len(categories) != 124:
        raise BuildError(f"expected reviewed 124-category catalog, found {len(categories)}")
    if output_root.exists() and any(output_root.iterdir()):
        raise BuildError(f"output directory is not empty: {output_root}; choose a fresh --output-root")
    output_root.mkdir(parents=True, exist_ok=True)
    all_source_categories = {key for key, spec in categories.items() if spec.get("source_file") or spec.get("source_refs")}
    with tempfile.TemporaryDirectory(prefix="dns-rule-builder-expanded-source-shadow-") as work:
        all_project = _shadow_project(Path(work) / "project", categories, all_source_categories | {"foreign_general"})
        source_config = yaml.safe_load((ROOT / "config/sources.yaml").read_text(encoding="utf-8"))["sources"]
        source_trees = {key: {**metadata, "root": Path(metadata["root"]), "kind": source_config[key].get("kind", "remote")} for key, metadata in sources.items()}
        input_time = max((source.get("source_date_utc") for source in sources.values() if source.get("source_date_utc")), default="2026-10-09T22:09:47Z")
        full = _write_all_category_syntax(all_project, output_root / "all-category-syntax", categories, source_trees)
    representative_dir, custom = _representative_candidate(output_root, categories, sources, input_time)
    representative_manifest = verify_manifest(representative_dir)
    payloads, order = _provider_rules(representative_dir)
    provider_by_category = {key: spec["provider_name"] for key, spec in categories.items()}
    probes = []

    def add_probe(domain: str, category: str, label: str) -> None:
        winner = next((name for name, _ in order if any(_matches(domain, row) for row in payloads.get(name, []))), None)
        probes.append({"domain": domain, "category": category, "label": label, "expected_provider": winner, "expected_policy": dict(order).get(winner) if winner else "MATCH"})

    representatives = {}
    for category in REPRESENTATIVE_CATEGORIES:
        name = provider_by_category[category]
        rows = payloads.get(name, [])
        if not rows:
            raise BuildError(f"representative {category} has no candidate rules")
        exact = _sample_domain(rows, "DOMAIN")
        suffix = _sample_domain(rows, "DOMAIN-SUFFIX")
        representatives[category] = {"provider": name, "rule_count": len(rows), "exact_sample": exact, "suffix_sample": suffix}
        for sample, label in ((exact, "exact"), (suffix, "suffix")):
            if sample:
                add_probe(sample, category, label)
                add_probe("child." + sample, category, label + "-child")
    add_probe(custom["custom_exact_domain"], "foreign_general", "custom-exact-cross-category")
    add_probe("probe." + custom["custom_exact_domain"], "perplexity", "custom-exact-cross-category-child")
    smartdns_dir = output_root / "smartdns-probe"
    smartdns_dir.mkdir()
    shutil.copy2(representative_dir / "smartdns/proxy-domains.txt", smartdns_dir / "proxy-domains.txt")
    fanart_exact = representatives["fanart"]["exact_sample"]
    xai_suffix = representatives["xai"]["suffix_sample"]
    if not fanart_exact or not xai_suffix:
        raise BuildError("Fanart exact and xAI suffix samples are required")
    config = "\n".join(("bind 0.0.0.0:53", "cache-size 0", "speed-check-mode none", "dualstack-ip-selection no", "log-console yes", "log-level error", "address /fanart.tv/192.0.2.20", "domain-set -name generated -file /probe/proxy-domains.txt", "address /domain-set:generated/192.0.2.10", ""))
    (smartdns_dir / "smartdns.conf").write_text(config, encoding="utf-8")
    plan = {
        "shadow_only": True,
        "production_approval_changed": False,
        "deployment_allowed": representative_manifest["deployment_allowed"],
        "deployment_blockers": representative_manifest["deployment_blockers"],
        "source_snapshot": str(snapshot_path.resolve()),
        "source_revisions": {key: value["revision"] for key, value in sources.items()},
        "source_archive_sha256": {key: value["archive_sha256"] for key, value in sources.items()},
        "input_snapshot_time_utc": input_time,
        "category_total": 124,
        "source_backed_categories": len(all_source_categories),
        "full_source_parse": full,
        "representative_candidate": {"release_id": representative_manifest["release_id"], "custom_exact": custom, "providers": representatives},
        "mihomo_probes": probes,
        "mihomo_image": MIHOMO_IMAGE,
        "mihomo_full_syntax_dir": str((output_root / "all-category-syntax").resolve()),
        "mihomo_representative_dir": str((output_root / "mihomo-representative").resolve()),
        "smartdns_image": SMARTDNS_IMAGE,
        "smartdns_probe_dir": str(smartdns_dir.resolve()),
        "smartdns_exact_host": fanart_exact,
        "smartdns_suffix_root": xai_suffix,
    }
    (output_root / "probe-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"category_total": 124, "source_backed_categories": len(all_source_categories), "parsed_source_rules": full["parsed_rule_count"], "providers": full["provider_count"], "unsupported": full["unsupported_count"], "representative_release_id": representative_manifest["release_id"], "custom_exact": custom}


def _matches(domain: str, row: str) -> bool:
    kind, value = row.split(",", 1)
    return (kind == "DOMAIN" and domain == value) or (kind == "DOMAIN-SUFFIX" and (domain == value or domain.endswith("." + value)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output-root", type=Path, default=ROOT / ".cache/expanded-semantics")
    args = parser.parse_args()
    print(json.dumps(run(args.sources_snapshot, args.output_root), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
