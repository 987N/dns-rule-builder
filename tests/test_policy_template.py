from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dns_rule_builder.controller import validate_state
from dns_rule_builder.engine import BuildError
from dns_rule_builder.policy_template import render_policy_config, validate_policy_template
from test_controller_web import custom_state, fixture_project, write_candidate


def _template_for_fixture(root: Path) -> None:
    (root / "config/mihomo-policy-template.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "proxy_group_placeholder": "__USER_SELECT_PROXY_NODE__",
        "group_members": {
            "Proxy": ["__USER_SELECT_PROXY_NODE__", "REJECT"],
            "Foreign-General": ["Proxy", "REJECT"],
        },
    }, sort_keys=False))


def _regional_template_for_fixture(root: Path) -> None:
    (root / "config/mihomo-policy-template.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "proxy_group_placeholder": "__USER_SELECT_PROXY_NODE__",
        "regional_groups": ["香港节点", "美国节点"],
        "group_members": {
            "Proxy": ["香港节点", "美国节点", "REJECT"],
            "香港节点": ["__USER_SELECT_PROXY_NODE__", "REJECT"],
            "美国节点": ["__USER_SELECT_PROXY_NODE__", "REJECT"],
            "Foreign-General": ["Proxy", "香港节点", "美国节点", "REJECT"],
        },
    }, sort_keys=False))


def test_policy_template_graph_matches_configured_groups_and_renders_safe_release(tmp_path: Path, monkeypatch, capsys):
    root = fixture_project(tmp_path / "project")
    _template_for_fixture(root)
    state = custom_state()
    state["categories"]["demo"]["enabled"] = True
    state["rules"].append({"domain": "test.invalid", "match_type": "SUFFIX", "category": "demo", "enabled": True})
    archive = write_candidate(root, state)
    import io, zipfile
    release = tmp_path / "release"
    release.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        zipped.extractall(release)
    release_root = release / "rules"
    manifest = json.loads((release_root / "manifest.json").read_text())
    release_root.rename(release / manifest["release_id"])
    release_root = release / manifest["release_id"]

    prod_home = tmp_path / "production-render" / "mihomo"
    prod = render_policy_config(root, release_root, prod_home / "config.yaml")
    assert prod["deployable"] is False
    assert "not been selected" in prod["blocker"]
    generated = yaml.safe_load((prod_home / "config.yaml").read_text())
    groups = {row["name"]: row["proxies"] for row in generated["proxy-groups"]}
    assert groups["Proxy"] == ["__USER_SELECT_PROXY_NODE__", "REJECT"]
    assert all("DIRECT" not in members for name, members in groups.items() if name != "Proxy")
    assert generated["rules"][0].startswith("RULE-SET,CustomExact-")
    assert generated["rules"][1] == "RULE-SET,Demo,Proxy"
    assert generated["rules"][-1] == "MATCH,DIRECT"
    assert [rule.split(",")[1] for rule in generated["rules"][:-1]] == manifest["mihomo_rule_order"]
    assert generated["rule-providers"]["CustomExact-Foreign-General"]["path"] == "./providers/CustomExact-Foreign-General.yaml"

    test_home = tmp_path / "isolated-test" / "mihomo"
    isolated = render_policy_config(root, release_root, test_home / "config.yaml", test_proxy_reject=True)
    test_config = yaml.safe_load((test_home / "config.yaml").read_text())
    assert isolated["test_proxy_reject"] is True
    assert {row["name"]: row["proxies"] for row in test_config["proxy-groups"]}["Proxy"] == ["REJECT"]
    assert test_config["rules"][-1] == "MATCH,DIRECT"
    assert validate_policy_template(root)["schema_version"] == 1

    import shutil, sys
    from dns_rule_builder.cli import main
    cli_output = tmp_path / "cli-render" / "config.yaml"
    shutil.rmtree(root / ".cache")
    monkeypatch.setattr(sys, "argv", ["dns-rule-builder", "--project-root", str(root), "render-policy", str(release_root), "--output", str(cli_output), "--test-proxy-reject"])
    main()
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["deployable"] is False
    assert yaml.safe_load(cli_output.read_text())["rules"][-1] == "MATCH,DIRECT"


def test_template_rejects_cycles_missing_groups_and_direct_fallback(tmp_path: Path):
    root = fixture_project(tmp_path / "project")
    _template_for_fixture(root)
    valid = yaml.safe_load((root / "config/mihomo-policy-template.yaml").read_text())
    valid["group_members"]["Proxy"] = ["__USER_SELECT_PROXY_NODE__", "Proxy", "REJECT"]
    (root / "config/mihomo-policy-template.yaml").write_text(yaml.safe_dump(valid))
    with pytest.raises(BuildError, match="cycle"):
        validate_policy_template(root)
    valid["group_members"]["Proxy"] = ["__USER_SELECT_PROXY_NODE__", "REJECT"]
    valid["group_members"]["Foreign-General"] = ["DIRECT", "REJECT"]
    (root / "config/mihomo-policy-template.yaml").write_text(yaml.safe_dump(valid))
    with pytest.raises(BuildError, match="must not fall back"):
        validate_policy_template(root)
    valid["group_members"]["Foreign-General"] = ["__USER_SELECT_PROXY_NODE__", "REJECT"]
    (root / "config/mihomo-policy-template.yaml").write_text(yaml.safe_dump(valid))
    with pytest.raises(BuildError, match="only allowed in the Proxy selector"):
        validate_policy_template(root)
    valid["group_members"]["Foreign-General"] = ["Proxy", "REJECT"]
    valid["group_members"]["Proxy"] = ["__USER_SELECT_PROXY_NODE__", "DIRECT"]
    (root / "config/mihomo-policy-template.yaml").write_text(yaml.safe_dump(valid))
    with pytest.raises(BuildError, match="must not fall back directly"):
        validate_policy_template(root)
    valid["group_members"]["Proxy"] = ["__USER_SELECT_PROXY_NODE__", "REJECT"]
    valid["group_members"]["Foreign-General"] = ["Proxy", "REJECT"]
    del valid["group_members"]["Proxy"]
    (root / "config/mihomo-policy-template.yaml").write_text(yaml.safe_dump(valid))
    with pytest.raises(BuildError, match="every configured policy group"):
        validate_policy_template(root)


def test_regional_selectors_are_manual_placeholders_and_isolated_render_rejects_them(tmp_path: Path):
    root = fixture_project(tmp_path / "project")
    _regional_template_for_fixture(root)
    template = validate_policy_template(root)
    assert template["regional_groups"] == ["香港节点", "美国节点"]

    state = custom_state()
    state["categories"]["demo"]["enabled"] = True
    state["rules"].append({"domain": "test.invalid", "match_type": "SUFFIX", "category": "demo", "enabled": True})
    archive = write_candidate(root, state)
    import io, zipfile
    release = tmp_path / "release"
    release.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        zipped.extractall(release)
    release_root = release / "rules"
    manifest = json.loads((release_root / "manifest.json").read_text())
    release_root.rename(release / manifest["release_id"])
    release_root = release / manifest["release_id"]

    production_home = tmp_path / "production" / "mihomo"
    production = render_policy_config(root, release_root, production_home / "config.yaml")
    assert production["deployable"] is False
    production_groups = {row["name"]: row["proxies"] for row in yaml.safe_load((production_home / "config.yaml").read_text())["proxy-groups"]}
    assert production_groups["Proxy"] == ["香港节点", "美国节点", "REJECT"]
    assert production_groups["香港节点"] == ["__USER_SELECT_PROXY_NODE__", "REJECT"]
    assert production_groups["美国节点"] == ["__USER_SELECT_PROXY_NODE__", "REJECT"]
    assert production_groups["Foreign-General"] == ["Proxy", "香港节点", "美国节点", "REJECT"]

    test_home = tmp_path / "isolated" / "mihomo"
    render_policy_config(root, release_root, test_home / "config.yaml", test_proxy_reject=True)
    isolated = yaml.safe_load((test_home / "config.yaml").read_text())
    isolated_groups = {row["name"]: row["proxies"] for row in isolated["proxy-groups"]}
    assert isolated_groups["Proxy"] == ["REJECT"]
    assert isolated_groups["香港节点"] == ["REJECT"]
    assert isolated_groups["美国节点"] == ["REJECT"]
    assert "__USER_SELECT_PROXY_NODE__" not in str(isolated_groups)
    assert isolated["rules"] == yaml.safe_load((production_home / "config.yaml").read_text())["rules"]


@pytest.mark.parametrize("change,match", [
    (lambda item: item.update(regional_groups=["香港节点", "香港节点"]), "duplicate"),
    (lambda item: item.update(regional_groups=["Foreign-General"]), "collide"),
    (lambda item: item["group_members"].update(Proxy=["未知区域", "REJECT"]), "unknown member"),
    (lambda item: item["group_members"].update(Proxy=["香港节点", "REJECT"]), "every regional group"),
    (lambda item: item["group_members"].update({"香港节点": ["美国节点", "__USER_SELECT_PROXY_NODE__", "REJECT"], "美国节点": ["香港节点", "__USER_SELECT_PROXY_NODE__", "REJECT"]}), "cycle"),
])
def test_regional_template_rejects_invalid_names_references_and_cycles(tmp_path: Path, change, match: str):
    root = fixture_project(tmp_path / "project")
    _regional_template_for_fixture(root)
    template_path = root / "config/mihomo-policy-template.yaml"
    value = yaml.safe_load(template_path.read_text())
    change(value)
    template_path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
    with pytest.raises(BuildError, match=match):
        validate_policy_template(root)


def test_legacy_tiktok_state_only_accepts_disabled_alias_and_rules(tmp_path: Path):
    root = fixture_project(tmp_path / "project")
    state = custom_state()
    state["categories"]["tiktok"] = {"enabled": False}
    state["rules"].append({"domain": "old-tiktok.example", "match_type": "EXACT", "category": "tiktok", "enabled": False})
    normalized = validate_state(state, root)
    assert "tiktok" not in normalized["categories"]
    assert normalized["rules"][-1]["domain"] == "old-tiktok.example"

    invalid = custom_state()
    invalid["categories"]["tiktok"] = {"enabled": True}
    with pytest.raises(BuildError, match="TikTok"):
        validate_state(invalid, root)
    invalid = custom_state()
    invalid["rules"].append({"domain": "old-tiktok.example", "match_type": "EXACT", "category": "tiktok", "enabled": True})
    with pytest.raises(BuildError, match="TikTok"):
        validate_state(invalid, root)
