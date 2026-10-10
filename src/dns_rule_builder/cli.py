from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine import BuildError, Builder, explain, verify_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _release_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_dir():
        return path.resolve()
    return (root / "data/releases" / value).resolve()


def _dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="dns-rule-builder")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-revision", help="full commit SHA for the already supplied source snapshot")
    parser.add_argument("--data-revision", help="full commit SHA of the rules-data state used for this build")
    parser.add_argument("--code-commit", help="full commit SHA of the builder code snapshot")
    parser.add_argument("--snapshot-time-utc", help="stable input snapshot time from immutable commit dates (not CI completion time)")
    parser.add_argument("--build-purpose", choices=("rules", "smoke"), default="rules", help="separate normal rule releases from CI smoke releases")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory", help="scan actual source directories and file hashes")
    build = sub.add_parser("build", help="build and validate a versioned candidate")
    build.add_argument("--output-root", type=Path)
    validate = sub.add_parser("validate", help="verify a release manifest, identity, and every artifact hash")
    validate.add_argument("release")
    why = sub.add_parser("explain", help="show every rule matching a domain")
    why.add_argument("release")
    why.add_argument("domain")
    diff = sub.add_parser("diff", help="compare two release manifests and artifact hashes")
    diff.add_argument("left")
    diff.add_argument("right")
    report = sub.add_parser("report", help="print the manifest build and review summary")
    report.add_argument("release")
    args = parser.parse_args()
    root = args.project_root.resolve()
    builder = Builder(root, args.source_root, args.source_revision, args.data_revision, args.code_commit, args.snapshot_time_utc, args.build_purpose)
    try:
        if args.command == "inventory":
            _dump(builder.inventory())
        elif args.command == "build":
            result = builder.build(args.output_root)
            _dump({"release_id": result.release_id, "output_dir": str(result.output_dir), "deployment_allowed": result.deployment_allowed, "summary": result.summary})
        elif args.command == "validate":
            manifest = verify_manifest(_release_path(root, args.release))
            _dump({"valid": True, "release_id": manifest["release_id"], "deployment_allowed": manifest["deployment_allowed"], "publish_allowed": manifest["publish_allowed"]})
        elif args.command == "explain":
            release = _release_path(root, args.release)
            matches = explain(release, args.domain)
            _dump({"release_id": verify_manifest(release)["release_id"], "domain": args.domain, "winner": matches[0] if matches else None, "matches_in_provider_order": matches})
        elif args.command == "diff":
            left, right = _release_path(root, args.left), _release_path(root, args.right)
            a, b = verify_manifest(left), verify_manifest(right)
            _dump({"left": a["release_id"], "right": b["release_id"], "rule_count_delta": b["statistics"]["rule_count"] - a["statistics"]["rule_count"], "added_artifacts": sorted(set(b["artifact_sha256"]) - set(a["artifact_sha256"])), "removed_artifacts": sorted(set(a["artifact_sha256"]) - set(b["artifact_sha256"])), "changed_artifacts": sorted(name for name in set(a["artifact_sha256"]) & set(b["artifact_sha256"]) if a["artifact_sha256"][name] != b["artifact_sha256"][name])})
        elif args.command == "report":
            manifest = verify_manifest(_release_path(root, args.release))
            _dump({"release_id": manifest["release_id"], "statistics": manifest["statistics"], "review_required": manifest["review_required"], "deployment_allowed": manifest["deployment_allowed"], "publish_allowed": manifest["publish_allowed"], "deployment_blockers": manifest["deployment_blockers"]})
    except (BuildError, OSError, KeyError, ValueError) as exc:
        print(f"dns-rule-builder: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
