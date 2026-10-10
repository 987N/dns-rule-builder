from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
import yaml

from dns_rule_builder.engine import BuildError, Builder
from dns_rule_builder.rules_state import apply_state
from dns_rule_builder.snapshot import _get, _safe_extract


def tiny_project(root: Path) -> Path:
    for part in ("config", "data", "src/dns_rule_builder"):
        (root / part).mkdir(parents=True, exist_ok=True)
    (root / "src/dns_rule_builder/__init__.py").write_text('__version__="0.1"\n')
    (root / "config/categories.yaml").write_text("categories:\n  demo:\n    provider_name: Demo\n    policy_group: Proxy\n    priority: 10\n    enabled: true\n    source_file: Demo/Demo.list\n  foreign_general:\n    provider_name: Foreign-General\n    policy_group: Foreign-General\n    priority: 900\n    enabled: true\n    source_file: null\n")
    (root / "config/policies.yaml").write_text("policies:\n  Proxy: {}\n  Foreign-General: {}\n")
    (root / "config/shared-domains.yaml").write_text("shared_domains: []\n")
    (root / "config/sources.yaml").write_text("sources:\n  blackmatrix7:\n    repository: fixture\n    revision: 83f37120c155f67c8eef43221dabe15cc3c95fec\n    archive: absent.tgz\n    archive_sha256: null\n    rules_path: rule/Surge\n")
    (root / "config/settings.yaml").write_text("schema_version: 4\ntarget_semantics_verified: false\ndeployment_enabled: false\nlicense_release_approved: false\napproved_unsupported_types: []\n")
    source = root / ".cache/source"
    (source / "rule/Surge").mkdir(parents=True)
    (source / ".source-revision").write_text("83f37120c155f67c8eef43221dabe15cc3c95fec\n")
    return root


def test_custom_only_state_is_data_only_and_license_fixture_can_publish(tmp_path: Path):
    root = tiny_project(tmp_path / "project")
    state = tmp_path / "state.yaml"
    state.write_text(yaml.safe_dump({
        "schema_version": 1,
        "categories": {"demo": {"enabled": False}},
        "rules": [{"domain": "example.net", "match_type": "EXACT", "category": "foreign_general"}],
        "force_direct": [],
        "approved_exclusions": [],
        "shared_domain_reviews": [],
    }, sort_keys=False))
    result = apply_state(root, state)
    assert result["custom_rules"] == 1
    candidate = Builder(root, source_root=root / ".cache/source", data_revision="d" * 40, code_commit="e" * 40, input_snapshot_time_utc="2026-10-10T00:00:00Z").build(tmp_path / "out")
    assert candidate.summary["rule_count"] == 1
    assert (candidate.output_dir / "manifest.json").is_file()
    assert __import__("json").loads((candidate.output_dir / "manifest.json").read_text())["publish_allowed"] is True


def test_state_rejects_unknown_category_and_persists_exclusion_for_build_review(tmp_path: Path):
    root = tiny_project(tmp_path / "project")
    state = tmp_path / "state.yaml"
    base = {"schema_version": 1, "categories": {"unknown": {"enabled": True}}, "rules": [], "force_direct": [], "approved_exclusions": [], "shared_domain_reviews": []}
    state.write_text(yaml.safe_dump(base))
    with pytest.raises(BuildError, match="invalid rules-data category"):
        apply_state(root, state)
    base["categories"] = {}
    base["approved_exclusions"] = [{"domain": "example.net"}]
    state.write_text(yaml.safe_dump(base))
    apply_state(root, state)
    assert yaml.safe_load((root / "data/approved-exclusions.yaml").read_text())["exclusions"] == base["approved_exclusions"]


def test_tar_extraction_rejects_path_traversal_and_links(tmp_path: Path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("root/../../escaped")
        payload = b"bad"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(BuildError, match="unsafe path"):
        _safe_extract(archive, tmp_path / "extract")
    assert not (tmp_path / "escaped").exists()


def test_upstream_token_is_confined_to_github_api(monkeypatch):
    captured = []

    class FakeResponse:
        headers = {"Content-Length": "2"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size=-1):
            if not hasattr(self, "done"):
                self.done = True
                return b"ok"
            return b""

    def fake_urlopen(request, timeout):
        captured.append((request.full_url, request.get_header("Authorization")))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert _get("https://api.github.com/repos/example/project/commits/master", 32, "test-token") == b"ok"
    assert _get("https://codeload.github.com/example/project/tar.gz/abc", 32) == b"ok"
    assert captured == [
        ("https://api.github.com/repos/example/project/commits/master", "Bearer test-token"),
        ("https://codeload.github.com/example/project/tar.gz/abc", None),
    ]
    with pytest.raises(BuildError, match="only be sent"):
        _get("https://example.org/", 32, "test-token")
