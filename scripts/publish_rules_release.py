#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

from dns_rule_builder.engine import BuildError, verify_manifest
from dns_rule_builder.github import GitHubClient


def _zip_candidate(release_dir: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in release_dir.rglob("*") if p.is_file()):
            relative = path.relative_to(release_dir).as_posix()
            info = zipfile.ZipInfo(f"rules/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def main() -> int:
    release_dir = Path(os.environ["RULE_RELEASE_DIR"]).resolve()
    data_commit = os.environ["DATA_COMMIT"]
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    manifest = verify_manifest(release_dir)
    if manifest["data_revision"] != data_commit:
        raise BuildError("candidate data revision does not match workflow input")
    if not manifest["publish_allowed"]:
        print(json.dumps({"published": False, "reason": "license gate is closed for third-party rule output", "release_id": manifest["release_id"]}))
        return 0
    owner, repo = repository.split("/", 1)
    client = GitHubClient(owner, repo, token)
    tag = "rules-" + manifest["release_id"]
    draft = client.create_or_get_draft_release(tag, tag, f"build_purpose={manifest['build_purpose']}\nRules data commit `{data_commit}`; upstream source `{manifest['source_revision']}`.")
    upload_url = draft.get("upload_url")
    release_id = draft.get("id")
    if not isinstance(upload_url, str) or not isinstance(release_id, int):
        raise BuildError("GitHub draft response omitted upload URL or release ID")
    temporary_dir = release_dir.parent / "release-assets"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    zip_path = temporary_dir / "rules.zip"
    _zip_candidate(release_dir, zip_path)
    manifest_path = temporary_dir / "manifest.json"
    manifest_path.write_bytes((release_dir / "manifest.json").read_bytes())
    assets = [zip_path, manifest_path]
    if manifest["identity_inputs"].get("normalized_rules") and any(not rule["custom"] for rule in manifest["identity_inputs"]["normalized_rules"]):
        notices = Path(__file__).resolve().parents[1] / "THIRD_PARTY_NOTICES.md"
        license_file = Path(__file__).resolve().parents[1] / "licenses/blackmatrix7-GPL-2.0.txt"
        assets.extend([notices, license_file])
    sums_path = temporary_dir / "SHA256SUMS"
    sums_path.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in assets), encoding="ascii")
    assets.append(sums_path)
    for path in assets:
        client.ensure_release_asset(draft, upload_url, path)
    make_latest = manifest["build_purpose"] == "rules"
    published = client.publish_release(release_id, tag, "rules-data", data_commit, [path.name for path in assets], "main", os.environ.get("CODE_COMMIT"), make_latest=make_latest)
    print(json.dumps({"published": not published.get("draft", True), "release_id": manifest["release_id"], "release_url": published.get("html_url")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, KeyError, ValueError) as exc:
        print(f"publish-rules: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
