from __future__ import annotations

import hashlib
import json
import yaml
from pathlib import Path

import pytest

from dns_rule_builder.engine import BuildError, Builder, _read_source_file, canonical_domain, canonical_json, explain, verify_manifest


SOURCE_REVISION = "83f37120c155f67c8eef43221dabe15cc3c95fec"


def test_domain_canonicalization_preserves_exact_semantics():
    assert canonical_domain("  BÜCHER.example. ") == "xn--bcher-kva.example"
    for invalid in ("example", "com", "https://example.com", "example.com:443", "127.0.0.1", "nas.home.arpa", "foo..example.com"):
        with pytest.raises(ValueError):
            canonical_domain(invalid)


def test_surge_parses_supported_types_and_reports_others(tmp_path: Path):
    src = tmp_path / "sample.list"
    src.write_text("DOMAIN,api.example.com\nDOMAIN-SUFFIX,example.org\nDOMAIN-KEYWORD,other\n", encoding="utf-8")
    rules, unsupported = _read_source_file(src, "rule/Surge/Demo/Demo.list", "demo", {"provider_name": "Demo", "policy_group": "AI", "priority": 10}, SOURCE_REVISION)
    assert [(r.domain, r.match_type) for r in rules] == [("api.example.com", "EXACT"), ("example.org", "SUFFIX")]
    assert rules[0].source == "blackmatrix7"
    assert rules[0].source_revision == SOURCE_REVISION
    assert unsupported[0]["type"] == "DOMAIN-KEYWORD"


def test_domain_set_adapter_matches_audited_dot_convention(tmp_path: Path):
    src = tmp_path / "domains.list"
    src.write_text(".example.org\napi.example.com\n", encoding="utf-8")
    rules, unsupported = _read_source_file(src, "rule/Surge/Demo/domains.list", "demo", {"provider_name": "Demo", "policy_group": "AI", "priority": 10, "format": "surge-domain-set"}, SOURCE_REVISION)
    assert [(r.domain, r.match_type) for r in rules] == [("example.org", "SUFFIX"), ("api.example.com", "EXACT")]
    assert not unsupported


def make_project(root: Path, force_direct: list[str] | None = None) -> tuple[Path, Path]:
    for name in ("config", "data", "src/dns_rule_builder"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "src/dns_rule_builder/__init__.py").write_text('__version__ = "0.1.0"\n')
    (root / "config/sources.yaml").write_text(
        "sources:\n  blackmatrix7:\n    repository: fixture\n    revision: " + SOURCE_REVISION + "\n"
        "    archive: absent.tar.gz\n    archive_sha256: null\n    rules_path: rule/Surge\n    format: surge\n"
    )
    (root / "config/categories.yaml").write_text(
        "categories:\n"
        "  demo:\n    provider_name: Demo\n    policy_group: AI\n    priority: 10\n    enabled: true\n    source_file: Demo/Demo.list\n"
        "  foreign_general:\n    provider_name: Foreign-General\n    policy_group: Foreign-General\n    priority: 900\n    enabled: true\n    source_file: null\n"
    )
    (root / "config/policies.yaml").write_text("policies:\n  AI: {}\n  Foreign-General: {}\n")
    (root / "config/shared-domains.yaml").write_text("shared_domains: []\n")
    (root / "config/settings.yaml").write_text("schema_version: 4\ntarget_semantics_verified: false\ndeployment_enabled: false\nlicense_release_approved: false\napproved_unsupported_types: [DOMAIN-KEYWORD]\n")
    (root / "data/custom-rules.yaml").write_text(json.dumps({"rules": [{"domain": "api.example.com", "match_type": "EXACT", "category": "foreign_general"}], "force_direct": force_direct or []}))
    srcroot = root / ".cache/source"
    folder = srcroot / "rule/Surge/Demo"
    folder.mkdir(parents=True)
    (srcroot / ".source-revision").write_text(SOURCE_REVISION)
    (folder / "Demo.list").write_text("DOMAIN-SUFFIX,example.com\nDOMAIN-KEYWORD,logged\n")
    return root, srcroot


def test_build_custom_exact_precedes_every_regular_provider_and_verifies(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    output = tmp_path / "out"
    result = Builder(root, source_root).build(output)
    manifest = verify_manifest(result.output_dir)
    assert len(result.release_id) == 64
    assert manifest["mihomo_rule_order"] == ["CustomExact-Foreign-General", "Demo"]
    rule_lines = (result.output_dir / "mihomo/rules.yaml").read_text()
    assert rule_lines.index("CustomExact-Foreign-General") < rule_lines.index("RULE-SET,Demo")
    providers = (result.output_dir / "mihomo/rule-providers.yaml").read_text()
    assert "./providers/CustomExact-Foreign-General.yaml" in providers
    assert not (result.output_dir / "mihomo/providers/Foreign-General.yaml").exists()
    matches = explain(result.output_dir, "api.example.com")
    assert matches[0]["category"] == "foreign_general"
    assert matches[0]["source"] == "custom"
    assert not result.deployment_allowed
    assert result.release_id == Builder(root, source_root).build(output).release_id


def test_manifest_rejects_tampering_and_extra_files(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    release = Builder(root, source_root).build(tmp_path / "out").output_dir
    manifest_path = release / "manifest.json"
    original = json.loads(manifest_path.read_text())
    changed = dict(original)
    changed["deployment_allowed"] = True
    manifest_path.write_text(json.dumps(changed))
    with pytest.raises(BuildError, match="manifest field mismatch"):
        verify_manifest(release)
    manifest_path.write_text(json.dumps(original))
    (release / "unexpected.txt").write_text("extra")
    with pytest.raises(BuildError, match="artifact hash mismatch"):
        verify_manifest(release)


def test_manifest_rejects_provider_policy_mapping_even_with_recomputed_identity(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    release = Builder(root, source_root).build(tmp_path / "out").output_dir
    manifest_path = release / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["provider_map"][0]["policy_group"] = "ForgedPolicy"
    manifest["identity_inputs"]["provider_map"] = manifest["provider_map"]
    manifest["release_id"] = hashlib.sha256(canonical_json({"identity_inputs": manifest["identity_inputs"], "artifact_sha256": manifest["artifact_sha256"]})).hexdigest()
    new_release = release.parent / manifest["release_id"]
    manifest_path.write_text(json.dumps(manifest))
    release.rename(new_release)
    with pytest.raises(BuildError, match="maps a category or policy incorrectly"):
        verify_manifest(new_release)


def test_direct_scope_conflict_covers_parent_and_child_overlap(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project", force_direct=["api.example.com"])
    result = Builder(root, source_root).build(tmp_path / "out")
    conflicts = json.loads((result.output_dir / "reports/conflicts.json").read_text())
    assert conflicts["force_direct_conflicts"]
    assert not result.deployment_allowed


def test_custom_duplicate_retains_upstream_and_custom_provenance(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    source = source_root / "rule/Surge/Demo/Demo.list"
    source.write_text(source.read_text() + "DOMAIN,api.example.com\n")
    # Put a custom exact rule on top of the same upstream semantic rule.
    custom_path = root / "data/custom-rules.yaml"
    custom_path.write_text(json.dumps({"rules": [{"domain": "example.com", "match_type": "SUFFIX", "category": "demo"}, {"domain": "api.example.com", "match_type": "EXACT", "category": "demo"}], "force_direct": []}))
    result = Builder(root, source_root).build(tmp_path / "out")
    manifest = verify_manifest(result.output_dir)
    matching = [row for row in manifest["identity_inputs"]["normalized_rules"] if row["domain"] == "api.example.com"]
    assert {row["source"] for row in matching} == {"blackmatrix7", "custom"}
    assert len(matching) == 2
    assert any(row["custom"] for row in matching)


def test_approved_telemetry_exclusion_is_exact_and_parent_still_blocks_direct_exception(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project", force_direct=["telemetry.example.net"])
    source = source_root / "rule/Surge/Demo/Demo.list"
    source.write_text("DOMAIN,telemetry.example.net\nDOMAIN-SUFFIX,telemetry.example.net\n")
    # A custom exact rule with the same semantic key survives upstream exclusion.
    (root / "data/custom-rules.yaml").write_text(json.dumps({"rules": [{"domain": "telemetry.example.net", "match_type": "EXACT", "category": "demo"}], "force_direct": ["telemetry.example.net"]}))
    exclusion = {"category": "demo", "domain": "telemetry.example.net", "match_type": "EXACT", "reviewer": "admin", "reviewed_at": "2026-10-10T00:00:00Z", "reason": "排除已审核的统计主机"}
    (root / "data/approved-exclusions.yaml").write_text(yaml.safe_dump({"exclusions": [exclusion]}, allow_unicode=True, sort_keys=False))
    result = Builder(root, source_root).build(tmp_path / "out")
    manifest = verify_manifest(result.output_dir)
    normalized = manifest["identity_inputs"]["normalized_rules"]
    same_key = [row for row in normalized if row["domain"] == "telemetry.example.net" and row["match_type"] == "EXACT"]
    assert len(same_key) == 1 and same_key[0]["custom"] is True
    report = json.loads((result.output_dir / "reports/excluded-rules.json").read_text())
    assert report[0]["status"] == "applied"
    assert report[0]["excluded_sources"][0]["source"] == "blackmatrix7"
    assert report[0]["excluded_sources"][0]["match_type"] == "EXACT"
    conflicts = json.loads((result.output_dir / "reports/conflicts.json").read_text())
    assert any(row["domain"] == "telemetry.example.net" and any(item["match_type"] == "SUFFIX" for item in row["covered_by"]) for row in conflicts["force_direct_conflicts"])
    assert not result.deployment_allowed


def test_unmatched_approved_exclusion_blocks_review_and_is_in_release_identity(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    exclusion = {"category": "demo", "domain": "missing.example.net", "match_type": "EXACT", "reviewer": "admin", "reviewed_at": "2026-10-10T00:00:00Z", "reason": "reviewed"}
    (root / "data/approved-exclusions.yaml").write_text(yaml.safe_dump({"exclusions": [exclusion]}, sort_keys=False))
    result = Builder(root, source_root).build(tmp_path / "out")
    manifest = verify_manifest(result.output_dir)
    assert result.summary["review_required"] and not result.deployment_allowed
    assert manifest["identity_inputs"]["approved_exclusions"] == [exclusion]
    assert any(row["reason"] == "approved exclusion invalid or unmatched" for row in manifest["deployment_blockers"])
    assert json.loads((result.output_dir / "reports/excluded-rules.json").read_text())[0]["status"] == "unmatched"
    explained = explain(result.output_dir, "api.example.com")
    assert explained[0]["custom"] is True


def test_equal_priority_cross_policy_overlap_blocks_deployment(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    categories_path = root / "config/categories.yaml"
    categories = yaml.safe_load(categories_path.read_text())
    categories["categories"]["other"] = {"provider_name": "Other", "policy_group": "OtherPolicy", "priority": 10, "enabled": True, "source_file": "Other/Other.list"}
    categories_path.write_text(yaml.safe_dump(categories, sort_keys=False))
    policies_path = root / "config/policies.yaml"
    policies = yaml.safe_load(policies_path.read_text())
    policies["policies"]["OtherPolicy"] = {}
    policies_path.write_text(yaml.safe_dump(policies, sort_keys=False))
    demo_source = source_root / "rule/Surge/Demo/Demo.list"
    demo_source.write_text(demo_source.read_text() + "DOMAIN,api.example.com\n")
    folder = source_root / "rule/Surge/Other"
    folder.mkdir()
    (folder / "Other.list").write_text("DOMAIN,api.example.com\n")
    result = Builder(root, source_root).build(tmp_path / "out")
    conflict_report = json.loads((result.output_dir / "reports/conflicts.json").read_text())
    assert any(not row["resolved"] for row in conflict_report["policy_conflicts"])
    assert result.summary["review_required"]
    assert not result.deployment_allowed


def test_specificity_constraints_split_category_runs_instead_of_category_cycles(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    categories_path = root / "config/categories.yaml"
    categories = yaml.safe_load(categories_path.read_text())
    categories["categories"]["other"] = {"provider_name": "Other", "policy_group": "OtherPolicy", "priority": 5, "enabled": True, "source_file": "Other/Other.list"}
    categories_path.write_text(yaml.safe_dump(categories, sort_keys=False))
    policies_path = root / "config/policies.yaml"
    policies = yaml.safe_load(policies_path.read_text())
    policies["policies"]["OtherPolicy"] = {}
    policies_path.write_text(yaml.safe_dump(policies, sort_keys=False))
    source_a = source_root / "rule/Surge/Demo/Demo.list"
    source_a.write_text("DOMAIN-SUFFIX,a.com\nDOMAIN-SUFFIX,child.b.com\n")
    folder = source_root / "rule/Surge/Other"
    folder.mkdir()
    (folder / "Other.list").write_text("DOMAIN-SUFFIX,b.com\nDOMAIN-SUFFIX,child.a.com\n")
    result = Builder(root, source_root).build(tmp_path / "out")
    report = json.loads((result.output_dir / "reports/conflicts.json").read_text())
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert not report["provider_order_cycles"]
    provider_map = manifest["provider_map"]
    assert any(item["name"].startswith("Demo-Part-") for item in provider_map)
    assert any(item["name"].startswith("Other-Part-") for item in provider_map)
    index_for_domain = {
        row["domain"]: provider_index
        for provider_index, provider in enumerate(provider_map)
        for row in (manifest["identity_inputs"]["normalized_rules"][i] for i in provider["member_indices"])
        if row["domain"] in {"a.com", "b.com", "child.a.com", "child.b.com"}
    }
    assert index_for_domain["child.a.com"] < index_for_domain["a.com"]
    assert index_for_domain["child.b.com"] < index_for_domain["b.com"]


def test_google_youtube_gemini_specificity_routes_are_ordered_by_rule_node():
    from dns_rule_builder.provider_plan import compile_provider_plan

    categories = {
        "youtube": {"provider_name": "YouTube"},
        "google": {"provider_name": "Google"},
        "gemini": {"provider_name": "Gemini"},
        "foreign_general": {"provider_name": "Foreign-General"},
    }
    rows = [
        {"category": "youtube", "policy_group": "YouTube", "priority": 20, "domain": "gvt2.com", "match_type": "SUFFIX", "custom": False},
        {"category": "google", "policy_group": "Google", "priority": 20, "domain": "beacons.gvt2.com", "match_type": "EXACT", "custom": False},
        {"category": "google", "policy_group": "Google", "priority": 30, "domain": "google.com", "match_type": "SUFFIX", "custom": False},
        {"category": "gemini", "policy_group": "Gemini", "priority": 20, "domain": "gemini.google.com", "match_type": "SUFFIX", "custom": False},
        {"category": "foreign_general", "policy_group": "Foreign-General", "priority": -1, "domain": "special.google.com", "match_type": "EXACT", "custom": True},
    ]
    plan = compile_provider_plan(rows, categories)
    assert not plan["cycles"]
    assert plan["order"][0] == "CustomExact-Foreign-General"
    position = {name: i for i, name in enumerate(plan["order"])}
    member_provider = {index: name for provider in plan["providers"] for index in provider["member_indices"] for name in [provider["name"]]}
    assert position[member_provider[1]] < position[member_provider[0]]  # Google beacon exact before YouTube family suffix.
    assert position[member_provider[3]] < position[member_provider[2]]  # Gemini service before broad Google.


def test_built_gemini_google_youtube_rules_use_three_independent_policy_groups(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    categories = {
        "gemini": {"provider_name": "Gemini", "policy_group": "Gemini", "priority": 10, "enabled": True, "source_file": "Gemini/Gemini.list"},
        "google": {"provider_name": "Google", "policy_group": "Google", "priority": 20, "enabled": True, "source_file": "Google/Google.list"},
        "youtube": {"provider_name": "YouTube", "policy_group": "YouTube", "priority": 20, "enabled": True, "source_file": "YouTube/YouTube.list"},
        "foreign_general": {"provider_name": "Foreign-General", "policy_group": "Foreign-General", "priority": 900, "enabled": True, "source_file": None},
    }
    (root / "config/categories.yaml").write_text(yaml.safe_dump({"categories": categories}, sort_keys=False))
    policies = {"AI": {}, "Gemini": {}, "Google": {}, "YouTube": {}, "Foreign-General": {}}
    (root / "config/policies.yaml").write_text(yaml.safe_dump({"policies": policies}, sort_keys=False))
    fixtures = {
        "Gemini/Gemini.list": "DOMAIN-SUFFIX,gemini.google.com\n",
        "Google/Google.list": "DOMAIN-SUFFIX,google.com\nDOMAIN,beacons.gvt2.com\n",
        "YouTube/YouTube.list": "DOMAIN-SUFFIX,gvt2.com\n",
    }
    for relative, contents in fixtures.items():
        path = source_root / "rule/Surge" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    result = Builder(root, source_root).build(tmp_path / "out")
    manifest = verify_manifest(result.output_dir)
    mapping = {}
    for provider in manifest["provider_map"]:
        for index in provider["member_indices"]:
            mapping[index] = provider["policy_group"]
    normalized = manifest["identity_inputs"]["normalized_rules"]
    groups_by_category = {
        category: {mapping[index] for index, row in enumerate(normalized) if row["category"] == category}
        for category in ("gemini", "google", "youtube")
    }
    assert groups_by_category == {"gemini": {"Gemini"}, "google": {"Google"}, "youtube": {"YouTube"}}
    gemini_matches = explain(result.output_dir, "gemini.google.com")
    assert gemini_matches[0]["category"] == "gemini"
    assert gemini_matches[0]["policy_group"] == "Gemini"
    assert gemini_matches[0]["_provider"] == "Gemini"
    assert explain(result.output_dir, "www.google.com")[0]["policy_group"] == "Google"
    assert explain(result.output_dir, "video.gvt2.com")[0]["policy_group"] == "YouTube"
    assert explain(result.output_dir, "beacons.gvt2.com")[0]["policy_group"] == "Google"


def test_new_unsupported_type_blocks_review_and_deployment(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    source = source_root / "rule/Surge/Demo/Demo.list"
    source.write_text(source.read_text() + "DOMAIN-REGEX,^foo\\.example$\n")
    result = Builder(root, source_root).build(tmp_path / "out")
    assert result.summary["review_required"]
    assert not result.deployment_allowed
    blockers = json.loads((result.output_dir / "manifest.json").read_text())["deployment_blockers"]
    assert any(item["reason"] == "unsupported input rules require review" for item in blockers)


def test_zero_valid_rules_in_enabled_category_blocks_even_known_partial_types(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    source = source_root / "rule/Surge/Demo/Demo.list"
    source.write_text("DOMAIN-KEYWORD,expected-partial\n")
    result = Builder(root, source_root).build(tmp_path / "out")
    assert result.summary["review_required"]
    categories = json.loads((result.output_dir / "reports/categories.json").read_text())
    assert next(item for item in categories if item["category"] == "demo")["status"] == "empty"
    assert not result.deployment_allowed


def test_empty_category_and_large_source_drop_block_candidate_deployment(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    source = source_root / "rule/Surge/Demo/Demo.list"
    source.write_text("".join(f"DOMAIN-SUFFIX,d{i}.example.com\n" for i in range(10)))
    out = tmp_path / "out"
    first = Builder(root, source_root).build(out)
    assert first.summary["category_count"] > 0
    source.write_text("DOMAIN-SUFFIX,only.example.com\n")
    second = Builder(root, source_root).build(out)
    assert second.summary["review_required"]
    assert not second.deployment_allowed
    blockers = json.loads((second.output_dir / "manifest.json").read_text())["deployment_blockers"]
    assert any(item["reason"] == "category decreased by more than 30 percent" for item in blockers)


def test_source_failure_keeps_previous_candidate_current(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    out = tmp_path / "out"
    first = Builder(root, source_root).build(out)
    current = out / "current"
    previous_target = current.resolve()
    source = source_root / "rule/Surge/Demo/Demo.list"
    source.unlink()
    with pytest.raises(BuildError, match="missing or empty"):
        Builder(root, source_root).build(out)
    assert current.resolve() == previous_target
    source.write_text("\n")
    with pytest.raises(BuildError, match="missing or empty"):
        Builder(root, source_root).build(out)
    assert current.resolve() == previous_target
    assert verify_manifest(previous_target)["release_id"] == first.release_id


def test_manifest_rejects_symlink_artifact(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    release = Builder(root, source_root).build(tmp_path / "out").output_dir
    target = release / "reports/statistics.json"
    backup = release / "reports/statistics.backup"
    target.rename(backup)
    target.symlink_to(backup.name)
    with pytest.raises(BuildError, match="contains symlink"):
        verify_manifest(release)


def test_configured_shared_domain_requires_review_and_identity_tracks_approval(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    (root / "config/shared-domains.yaml").write_text(
        "shared_domains:\n"
        "  - id: shared-telemetry\n"
        "    domain: telemetry.example.com\n"
        "    match_type: EXACT\n"
        "    expected_categories: [demo]\n"
        "    risk: shared telemetry host\n"
    )
    (source_root / "rule/Surge/Demo/Demo.list").write_text("DOMAIN,telemetry.example.com\n")
    out = tmp_path / "out"
    pending = Builder(root, source_root).build(out)
    report = json.loads((pending.output_dir / "reports/shared-domains.json").read_text())
    assert report[0]["active"] is True
    assert report[0]["review_status"] == "pending"
    assert not pending.deployment_allowed
    reviews_path = root / "data/shared-domain-reviews.yaml"
    reviews_path.write_text("reviews:\n  - id: shared-telemetry\n    status: approved\n    reviewed_by: operator\n    reviewed_at: '2026-10-10'\n    rationale: explicitly retained for this test\n")
    approved = Builder(root, source_root).build(out)
    approved_manifest = verify_manifest(approved.output_dir)
    assert approved_manifest["identity_inputs"]["shared_domain_reviews"]
    approved_report = json.loads((approved.output_dir / "reports/shared-domains.json").read_text())
    assert approved_report[0]["review_status"] == "approved"
    assert approved_manifest["review_required"] is True  # This fixture has no source license approval ledger.
    assert approved_manifest["release_id"] != pending.release_id


def test_same_immutable_inputs_and_build_timestamp_produce_identical_manifest_and_assets(tmp_path: Path):
    root, source_root = make_project(tmp_path / "project")
    stable = {"data_revision": "d" * 40, "code_commit": "e" * 40, "input_snapshot_time_utc": "2026-10-10T00:00:00Z"}
    first = Builder(root, source_root, **stable).build(tmp_path / "out-a")
    second = Builder(root, source_root, **stable).build(tmp_path / "out-b")
    assert first.release_id == second.release_id
    assert (first.output_dir / "manifest.json").read_bytes() == (second.output_dir / "manifest.json").read_bytes()

