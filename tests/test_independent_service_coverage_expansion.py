from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from dns_rule_builder.source_approvals import approval_scope_sha256, shared_scope_for_category


ROOT = Path(__file__).resolve().parents[1]
V2FLY_REVISION = "c6adacf29cc4fb05e04651fcf03e00989ef9f797"


def _yaml(path: str):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_expanded_independent_service_sources_are_reviewed_and_scoped() -> None:
    categories = _yaml("config/categories.yaml")["categories"]
    catalog = _yaml("config/service-catalog.yaml")["services"]
    approvals = _yaml("config/source-approvals.yaml")
    profiles = {row["category"]: row for row in approvals["source_profiles"]}

    selected_keys = {
        "netflix": "netflix",
        "disney": "disney",
        "prime_video": "primevideo",
        "hbo": "hbo",
        "hulu": "hulu",
        "twitch": "twitch",
        "dailymotion": "dailymotion",
        "vimeo": "vimeo",
        "spotify": "spotify",
        "deezer": "deezer",
        "tidal": "tidal",
        "soundcloud": "soundcloud",
        "lastfm": "lastfm",
        "nyaa": "nyaa",
        "rutracker": "rutracker",
        "piratebay": "piratebay",
        "x1337": "1337x",
        "pornhub": "pornhub",
        "xvideos": "xvideos",
        "xnxx": "xnxx",
        "xhamster": "xhamster",
        "spankbang": "spankbang",
        "javdb": "javdb",
        "javbus": "javbus",
        "missav": "missav",
        "jable": "jable",
        "ehgallery": "ehentai",
        "dmm": "dmm-porn",
        "linkedin": "linkedin",
        "instagram": "instagram",
        "facebook": "facebook",
        "x_twitter": "twitter",
        "npmjs": "npmjs",
        "python": "python",
        "anaconda": "anaconda",
        "sourceforge": "sourceforge",
        "atlassian": "atlassian",
        "figma": "figma",
        "bbc": "bbc",
        "cnn": "cnn",
        "nytimes": "nytimes",
    }

    for category, key in selected_keys.items():
        assert categories[category]["enabled"] is True
        service_id = categories[category]["catalog_service_id"]
        assert service_id in catalog
        assert catalog[service_id]["review_state"] == "approved"
        assert catalog[service_id]["default_enabled"] is True
        profile = profiles[category]
        assert profile["review_status"] == "approved"
        assert len(profile["selected_sources"]) == 1
        ref = profile["selected_sources"][0]
        assert ref["source_id"] == "v2fly"
        assert ref["key"] == key
        assert ref["format"] == "v2fly-domain-list"
        assert ref["revision"] == V2FLY_REVISION
        assert len(ref["include_selector_sha256"]) == 64
        assert len(ref["include_closure_sha256"]) == 64
        assert len(ref["include_closure_file_sha256"]) > 0
        assert all(path.startswith("data/") for path in ref["include_closure_file_sha256"])
        assert all(len(value) == 64 for value in ref["include_closure_file_sha256"].values())
        assert isinstance(ref["file_declarations"], list)
        assert profile["coverage_status"] == "partial"

    assert "tiktok" not in categories
    if "apple_music" in categories:
        assert categories["apple_music"]["enabled"] is False
    assert catalog["apple_music"]["default_enabled"] is False
    assert categories["foreign_general"]["enabled"] is True
    assert categories["foreign_general"].get("source_refs") in (None, [])
    assert "adult_aggregate" not in categories or categories["adult_aggregate"]["enabled"] is False
    assert catalog["adult_aggregate"]["default_enabled"] is False
    assert categories["hulu"]["priority"] < categories["disney"]["priority"]
    additions = approvals["shared_scope_baseline"]["service_exact_additions"]
    assert additions["twitch"] == ["d3aqoihi2n8ty8.cloudfront.net"]
    assert "cloudfront.net" in approvals["shared_scope_baseline"]["broad_shared_suffixes_prohibited"]
    exact_cdn_exclusions = {
        row["domain"]
        for row in approvals["approved_exclusions"]
        if row["category"] in {"twitch", "prime_video"} and row["domain"].endswith(".cloudfront.net")
    }
    assert len(exact_cdn_exclusions) == 31


def test_october_25_v2fly_selector_and_shared_host_dispositions_are_exact() -> None:
    approvals = _yaml("config/source-approvals.yaml")
    profiles = {row["category"]: row for row in approvals["source_profiles"]}

    microsoft = profiles["microsoft"]["selected_sources"][0]
    xbox = profiles["xbox"]["selected_sources"][0]
    assert microsoft["revision"] == xbox["revision"] == "664598567e92d797bea11bb83cf939d1f37e9512"
    assert microsoft["include_selector_sha256"] == "87fe8dd142003e6f10fc91f32c8e847ea3342daa825b1665025323d06e4f8f7d"
    assert xbox["include_selector_sha256"] == "9d105432013ad28cf284c34030f3beb76b99a3375e1913514ff5a3e07684af11"
    assert "data/asobo" not in microsoft["include_closure_file_sha256"]
    assert "data/asobo" not in xbox["include_closure_file_sha256"]

    exclusions = approvals["shared_scope_baseline"]["excluded_exact_rules"]
    exact = {(row["category"], row["domain"], row["source_file"], row["raw_sha256"]) for row in exclusions}
    assert ("openai", "o33249.ingest.us.sentry.io", "data/openai", "a5e1824adb3bbeb229047e0e270bcdda9ee57f4f2602c1e88ef6020df5c802c2") in exact
    assert ("twitch", "d3stzm2eumvgb4.cloudfront.net", "data/twitch", "f6340187385b45c19e961ec9a747dac189a3bbd65c3b359d3ac7dcfd02393ac2") in exact
    assert "cloudfront.net" in approvals["shared_scope_baseline"]["broad_shared_suffixes_prohibited"]

    shared_domains = _yaml("config/shared-domains.yaml")["shared_domains"]
    expected_scope_hashes = {
        "openai": "19a2ad6e6160982d8d51a35edd19b7d664d482398768ed5eb926589d4e31b5ea",
        "twitch": "88222b1f4c18b0c567de91d601a0ebc53654d5bdd4df6cc4e52f6f0f52eb1d16",
        "claude": "5c5468d02dd707ed03b99ff1d3efa9ed06b098e65fb733c459b50e85d2703f42",
        "vimeo": "5c5468d02dd707ed03b99ff1d3efa9ed06b098e65fb733c459b50e85d2703f42",
    }
    for category, expected in expected_scope_hashes.items():
        assert profiles[category]["shared_scope_sha256"] == expected
        assert approval_scope_sha256(shared_scope_for_category(approvals, category, shared_domains)) == expected


def test_v2fly_telemetry_annotation_refresh_keeps_four_exact_exclusions_scoped() -> None:
    approvals = _yaml("config/source-approvals.yaml")
    expected = {
        ("openai", "browser-intake-datadoghq.com"): "full:browser-intake-datadoghq.com @ads @telemetry",
        ("openai", "o33249.ingest.sentry.io"): "full:o33249.ingest.sentry.io @ads @telemetry",
        ("openai", "o33249.ingest.us.sentry.io"): "full:o33249.ingest.us.sentry.io @ads @telemetry",
        ("disney", "disney.my.sentry.io"): "full:disney.my.sentry.io @ads @telemetry",
    }
    entries = {
        (item["category"], item["domain"]): item
        for item in approvals["approved_exclusions"]
        if (item.get("category"), item.get("domain")) in expected
    }
    assert set(entries) == set(expected)
    for identity, raw in expected.items():
        item = entries[identity]
        assert item["match_type"] == "EXACT"
        assert item["source_id"] == "v2fly"
        assert item["source_key"] == identity[0]
        assert item["source_file"] == f"data/{identity[0]}"
        assert item["raw"] == raw
        assert item["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
        assert item["review_status"] == "approved"
        assert item["reviewer"] == "Codex (same-scope source signature review)"
        assert item["reviewed_at"] == "2026-11-09"
        assert "adds @telemetry" in item["reason"]
        assert "e8d7a10a15bf662c927a40a3aa680bae86379608" in item["reason"]

    scope_entries = approvals["shared_scope_baseline"]["excluded_exact_rules"]
    refreshed_openai = {(item["category"], item["domain"], item["match_type"]) for item in scope_entries if item["category"] == "openai" and item["domain"] in {domain for category, domain in expected if category == "openai"}}
    assert refreshed_openai == {("openai", domain, "EXACT") for category, domain in expected if category == "openai"}
    assert all(item["match_type"] == "EXACT" for item in entries.values())


def test_manual_opensubtitles_and_paramount_plus_scopes_are_exact_and_signed() -> None:
    categories = _yaml("config/categories.yaml")["categories"]
    catalog = _yaml("config/service-catalog.yaml")
    services = catalog["services"]
    policies = _yaml("config/policies.yaml")["policies"]
    approvals = _yaml("config/source-approvals.yaml")
    profiles = {row["category"]: row for row in approvals["source_profiles"]}
    template_groups = set(_yaml("config/mihomo-policy-template.yaml")["group_members"])

    expected = {
        "opensubtitles": ("OpenSubtitles", "opensubtitles.list", [
            "opensubtitles.com", "www.opensubtitles.com", "api.opensubtitles.com",
            "vip-api.opensubtitles.com", "ai.opensubtitles.com",
        ]),
        "paramountplus": ("ParamountPlus", "paramountplus.list", [
            "paramountplus.com", "www.paramountplus.com",
        ]),
    }
    for category, (policy, filename, domains) in expected.items():
        cfg = categories[category]
        spec = services[cfg["catalog_service_id"]]
        profile = profiles[category]
        content = (ROOT / "config/curated" / filename).read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        ref = profile["selected_sources"][0]

        assert cfg["enabled"] is True
        assert cfg["policy_group"] == policy
        assert cfg["source_refs"] == [{"source_id": "manual_curated", "file": filename, "format": "surge"}]
        assert spec["review_state"] == "approved"
        assert spec["default_enabled"] is True
        assert spec["coverage_status"] == profile["coverage_status"] == "partial"
        assert spec["policy_group"] == policy
        assert profile["review_status"] == "approved"
        assert ref["source_id"] == "manual_curated"
        assert ref["file"] == filename
        assert ref["file_sha256"] == digest
        assert ref["license"] == "MIT"
        assert ref["license_sha256"] == hashlib.sha256((ROOT / ref["license_file"]).read_bytes()).hexdigest()
        assert ref["notice_sha256"] == hashlib.sha256((ROOT / ref["notice_file"]).read_bytes()).hexdigest()
        assert ref["include_selector_sha256"] and len(ref["include_selector_sha256"]) == 64
        assert ref["file_declarations"] == []
        assert all(f"DOMAIN,{domain}" in content.splitlines() for domain in domains)
        assert len([line for line in content.splitlines() if line.startswith("DOMAIN,")]) == len(domains)
        assert policy in policies
        assert policy in template_groups

    assert catalog["group_model"]["service_groups"][-2:] == ["OpenSubtitles", "ParamountPlus"]
