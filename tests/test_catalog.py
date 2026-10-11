from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from dns_rule_builder.catalog import load_service_catalog, service_metadata_for_api
from dns_rule_builder.engine import BuildError, Builder, explain
from dns_rule_builder.rules_state import apply_state
from test_rules_state_snapshot import tiny_project


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_refs_groups_defaults_and_tiktok_exclusion():
    catalog = load_service_catalog(ROOT)
    services = catalog["services"]
    categories = yaml.safe_load((ROOT / "config/categories.yaml").read_text())["categories"]
    policies = yaml.safe_load((ROOT / "config/policies.yaml").read_text())["policies"]
    assert "tiktok" not in services
    assert {"AI", "Development", "Social", "Communication", "Streaming", "Music", "Gaming", "Media-Metadata", "Cloud", "Knowledge", "Payments", "Apple-Overseas", "Adult", "Foreign-General"}.issubset(catalog["group_model"]["purpose_groups"])
    assert {"Google", "YouTube", "Gemini", "Telegram", "Netflix", "DisneyPlus", "PrimeVideo", "HBO"}.issubset(policies)
    assert categories["gemini"]["policy_group"] == services["gemini"]["policy_group"] == "Gemini"
    for category, cfg in categories.items():
        service_id = cfg.get("catalog_service_id")
        if service_id:
            spec = services[service_id]
            assert cfg["enabled"] == spec["default_enabled"], category
            if cfg.get("source_refs"):
                assert cfg["source_refs"] == spec["source_refs"]
    for service_id, spec in services.items():
        selected = spec.get("selected_ref")
        if selected:
            assert any(
                row["source_id"] == selected["source_id"]
                and (row.get("path") == selected["file"] or row.get("path", "").endswith(f"/{selected['file']}") or row.get("key") == selected["file"])
                for row in spec["source_candidates"]
            ), service_id
        assert spec["route_intent"] in {"direct", "proxy", "review"}
        assert spec.get("file_header_date") is None or isinstance(spec.get("file_header_date"), str)
        assert spec.get("source_commit_date_utc") is None or isinstance(spec.get("source_commit_date_utc"), str)
        if spec.get("selected_source") == "manual_curated":
            assert spec["file_header_date"] is None, service_id
            assert spec["source_commit_date_utc"] is None, service_id
    for service in ("pornhub", "xvideos", "xnxx", "xhamster", "spankbang", "missav", "jable", "javdb", "javbus"):
        assert services[service]["default_enabled"] is True
        assert services[service]["policy_group"] == "Adult"
        assert services[service]["implementation_status"] == "approved_candidate"
    for service in ("apple_id", "apple_hardware", "apple_media", "apple_music", "icloud"):
        assert services[service]["route_intent"] == "direct"
        assert services[service]["implementation_status"] == "reference_only"
        assert service not in categories
    assert services["apple_aggregate"]["implementation_status"] == "prohibited_aggregate"
    assert "apple" in categories and categories["apple"]["enabled"] is False


def test_catalog_api_metadata_includes_read_only_disabled_and_missing_records():
    metadata = {row["id"]: row for row in service_metadata_for_api(ROOT)}
    assert metadata["hbo"]["default_enabled"] is True
    assert metadata["hbo"]["implementation_status"] == "approved_candidate"
    assert metadata["hbo"].get("file_header_date") is None
    assert metadata["hbo"].get("source_commit_date_utc") is None
    assert load_service_catalog(ROOT)["services"]["hbo"]["source_commit_date_utc"] == "2026-10-09T10:11:47Z"
    assert metadata["fanart"]["default_enabled"] is True
    assert metadata["fanart"]["missing"] is False
    assert metadata["fanart"]["upstream_key_missing"] is True
    assert metadata["pypi"]["implementation_status"] == "manual_missing"
    assert "tiktok" not in metadata


def _metadata_project(root: Path, pending: bool = False) -> tuple[Path, Path]:
    project = tiny_project(root)
    source = project / ".cache/source"
    tmdb = source / "rule/Surge/Tmdb/Tmdb.list"
    tmdb.parent.mkdir(parents=True)
    tmdb.write_text("DOMAIN-SUFFIX,themoviedb.org\nDOMAIN-SUFFIX,tmdb.org\n")
    manual = project / "config/curated"
    manual.mkdir(parents=True)
    (manual / "fanart.list").write_text("DOMAIN,webservice.fanart.tv\nDOMAIN,assets.fanart.tv\n")
    sources = yaml.safe_load((project / "config/sources.yaml").read_text())
    sources["sources"]["manual_curated"] = {"kind": "local", "repository": "local", "path": "config/curated", "format": "surge", "adapter_version": 1}
    (project / "config/sources.yaml").write_text(yaml.safe_dump(sources, sort_keys=False))
    (project / "config/service-catalog.yaml").write_text(yaml.safe_dump({"schema_version": 1, "services": {
        "tmdb": {"review_state": "pending" if pending else "approved", "default_enabled": True, "route_intent": "proxy", "implementation_status": "approved_candidate"},
        "fanart": {"review_state": "approved", "default_enabled": True, "route_intent": "proxy", "implementation_status": "approved_candidate"},
    }}))
    cats = {
        "tmdb": {"provider_name": "TMDB", "policy_group": "Media-Metadata", "priority": 10, "enabled": True, "source_refs": [{"source_id": "blackmatrix7", "file": "rule/Surge/Tmdb/Tmdb.list", "format": "surge"}], "catalog_service_id": "tmdb"},
        "fanart": {"provider_name": "Fanart", "policy_group": "Media-Metadata", "priority": 11, "enabled": True, "source_refs": [{"source_id": "manual_curated", "file": "fanart.list", "format": "surge"}], "catalog_service_id": "fanart"},
        "foreign_general": {"provider_name": "Foreign-General", "policy_group": "Foreign-General", "priority": 900, "enabled": True, "source_file": None},
    }
    (project / "config/categories.yaml").write_text(yaml.safe_dump({"categories": cats}, sort_keys=False))
    (project / "config/policies.yaml").write_text(yaml.safe_dump({"policies": {"Media-Metadata": {}, "Foreign-General": {}}}))
    return project, source


def test_tmdb_and_fanart_explain_api_and_image_hosts(tmp_path: Path):
    project, source = _metadata_project(tmp_path / "project")
    locked = {
        "blackmatrix7": {"root": source, "revision": "83f37120c155f67c8eef43221dabe15cc3c95fec", "repository": "fixture", "archive_sha256": None},
        "manual_curated": {"root": project, "revision": "c" * 40, "repository": "local", "archive_sha256": "d" * 64, "kind": "local"},
    }
    result = Builder(project, source_trees=locked).build(tmp_path / "out")
    expected = {
        "api.themoviedb.org": ("tmdb", "themoviedb.org", "SUFFIX"),
        "image.tmdb.org": ("tmdb", "tmdb.org", "SUFFIX"),
        "webservice.fanart.tv": ("fanart", "webservice.fanart.tv", "EXACT"),
        "assets.fanart.tv": ("fanart", "assets.fanart.tv", "EXACT"),
    }
    for host, (category, domain, match_type) in expected.items():
        winner = explain(result.output_dir, host)[0]
        assert (winner["category"], winner["domain"], winner["match_type"]) == (category, domain, match_type)
    assert explain(result.output_dir, "child.webservice.fanart.tv") == []
    assert explain(result.output_dir, "api.fanart.tv") == []
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert manifest["category_status"]


def test_enabled_pending_or_direct_catalog_service_blocks_deployment(tmp_path: Path):
    project, source = _metadata_project(tmp_path / "pending", pending=True)
    locked = {
        "blackmatrix7": {"root": source, "revision": "83f37120c155f67c8eef43221dabe15cc3c95fec", "repository": "fixture", "archive_sha256": None},
        "manual_curated": {"root": project, "revision": "c" * 40, "repository": "local", "archive_sha256": "d" * 64, "kind": "local"},
    }
    settings = yaml.safe_load((project / "config/settings.yaml").read_text())
    settings.update({"target_semantics_verified": True, "deployment_enabled": True, "license_release_approved": True})
    (project / "config/settings.yaml").write_text(yaml.safe_dump(settings))
    result = Builder(project, source_trees=locked).build(tmp_path / "out")
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert not result.deployment_allowed
    assert any(item.get("reason") == "enabled service catalog entry requires review" and item.get("service") == "tmdb" for item in manifest["deployment_blockers"])

    catalog = yaml.safe_load((project / "config/service-catalog.yaml").read_text())
    catalog["services"]["tmdb"].update({"review_state": "approved", "route_intent": "direct"})
    (project / "config/service-catalog.yaml").write_text(yaml.safe_dump(catalog))
    result = Builder(project, source_trees=locked).build(tmp_path / "out-direct")
    manifest = json.loads((result.output_dir / "manifest.json").read_text())
    assert not result.deployment_allowed
    assert any(item.get("reason") == "enabled catalog entry is direct-only or not runnable" and item.get("service") == "tmdb" for item in manifest["deployment_blockers"])


def test_disabled_candidate_is_omitted_and_empty_legacy_state_keeps_policy_aliases(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    source = project / ".cache/source"
    folder = source / "rule/Surge/Netflix"
    folder.mkdir(parents=True)
    (folder / "Netflix.list").write_text("DOMAIN-SUFFIX,netflix.com\n")
    categories = {"netflix": {"provider_name": "Netflix", "policy_group": "Netflix", "priority": 20, "enabled": False, "source_file": "Netflix/Netflix.list"},
                 "foreign_general": {"provider_name": "Foreign-General", "policy_group": "Foreign-General", "priority": 900, "enabled": True, "source_file": None}}
    (project / "config/categories.yaml").write_text(yaml.safe_dump({"categories": categories}))
    (project / "config/policies.yaml").write_text(yaml.safe_dump({"policies": {"Netflix": {}, "Streaming": {}, "Microsoft": {}, "Foreign-General": {}}}))
    state = {"schema_version": 1, "categories": {}, "rules": [], "force_direct": [], "approved_exclusions": [], "shared_domain_reviews": []}
    state_path = tmp_path / "empty-state.yaml"; state_path.write_text(yaml.safe_dump(state))
    apply_state(project, state_path)
    candidate = Builder(project, source_root=source).build(tmp_path / "out")
    assert not (candidate.output_dir / "mihomo/providers/Netflix.yaml").exists()
    assert "Streaming" in yaml.safe_load((project / "config/policies.yaml").read_text())["policies"]
    assert "Microsoft" in yaml.safe_load((project / "config/policies.yaml").read_text())["policies"]


def test_project_custom_only_smoke_fixture_disables_all_upstream_categories(tmp_path: Path):
    project = tiny_project(tmp_path / "project")
    for name in ("categories.yaml", "policies.yaml", "sources.yaml", "settings.yaml", "shared-domains.yaml", "service-catalog.yaml"):
        shutil.copy2(ROOT / "config" / name, project / "config" / name)
    shutil.copytree(ROOT / "config/curated", project / "config/curated", dirs_exist_ok=True)

    result = apply_state(project, ROOT / "tests/fixtures/custom-only-state.yaml")
    assert result["custom_rules"] == 1
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())["categories"]
    catalog = yaml.safe_load((project / "config/service-catalog.yaml").read_text())
    for category, value in categories.items():
        if category == "foreign_general":
            value.update({"enabled": True, "source_file": None})
            value.pop("source_refs", None)
            continue
        if value.get("source_file") is not None or value.get("source_refs"):
            value["enabled"] = False
            service_id = value.get("catalog_service_id")
            if service_id in catalog.get("services", {}):
                catalog["services"][service_id]["default_enabled"] = False
    (project / "config/categories.yaml").write_text(yaml.safe_dump({"categories": categories}, sort_keys=False))
    (project / "config/service-catalog.yaml").write_text(yaml.safe_dump(catalog, sort_keys=False))
    categories = yaml.safe_load((project / "config/categories.yaml").read_text())["categories"]
    enabled_upstream = [key for key, value in categories.items() if value.get("enabled") and (value.get("source_file") is not None or value.get("source_refs"))]
    assert enabled_upstream == []
    assert categories["foreign_general"]["enabled"] is True

    source = project / ".cache/source"
    source.mkdir(parents=True, exist_ok=True)
    (source / ".source-revision").write_text("83f37120c155f67c8eef43221dabe15cc3c95fec\n")
    candidate = Builder(project, source_root=source, build_purpose="smoke").build(tmp_path / "candidate")
    manifest = json.loads((candidate.output_dir / "manifest.json").read_text())
    assert manifest["build_purpose"] == "smoke"
    assert manifest["statistics"]["rule_count"] == 1
    assert manifest["statistics"]["unique_semantic_rule_count"] == 1
    assert all(row["source"] == "custom" for row in manifest["identity_inputs"]["normalized_rules"])
    assert manifest["publish_allowed"] is True
