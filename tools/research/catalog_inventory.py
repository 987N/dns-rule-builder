#!/usr/bin/env python3
"""Read-only, checksum-pinned inventories for DNS service-rule research.

The tool inventories complete GitHub source archives rather than Contents API
pages, whose directory listing is capped. It reports counts and provenance,
never copies source domain sets into tracked output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import tempfile
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache"


@dataclass(frozen=True)
class Source:
    source_id: str
    repository: str
    revision: str
    commit_date: str
    archive_url: str
    archive_sha256: str
    archive_name: str
    archive_root: str
    max_archive_bytes: int
    max_expanded_bytes: int


SOURCES = (
    Source(
        "blackmatrix7",
        "https://github.com/blackmatrix7/ios_rule_script",
        "83f37120c155f67c8eef43221dabe15cc3c95fec",
        "2026-10-09T22:09:47Z",
        "https://codeload.github.com/blackmatrix7/ios_rule_script/tar.gz/83f37120c155f67c8eef43221dabe15cc3c95fec",
        "e4249eb848059306541eea99ce5ecc1c0a988810afc716cb76db456159c6bf99",
        "ios_rule_script-83f37120.tar.gz",
        "ios_rule_script-83f37120c155f67c8eef43221dabe15cc3c95fec",
        200 * 1024 * 1024,
        2 * 1024 * 1024 * 1024,
    ),
    Source(
        "v2fly",
        "https://github.com/v2fly/domain-list-community",
        "c6adacf29cc4fb05e04651fcf03e00989ef9f797",
        "2026-10-09T10:11:47Z",
        "https://codeload.github.com/v2fly/domain-list-community/tar.gz/c6adacf29cc4fb05e04651fcf03e00989ef9f797",
        "d5a59ddc7b1577741ab910b1b5b0aa662f1456d41477bbfe056599f9e97f8ef0",
        "v2fly-c6adacf.tar.gz",
        "domain-list-community-c6adacf29cc4fb05e04651fcf03e00989ef9f797",
        10 * 1024 * 1024,
        100 * 1024 * 1024,
    ),
)


class InventoryError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _archive_path(source: Source, cache: Path, offline: bool) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / source.archive_name
    if not path.exists():
        if offline:
            raise InventoryError(f"missing cached archive in offline mode: {path}")
        request = urllib.request.Request(
            source.archive_url,
            headers={"User-Agent": "dns-rule-builder-readonly-research"},
        )
        temporary = path.with_suffix(path.suffix + ".partial")
        try:
            received = 0
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > source.max_archive_bytes:
                        raise InventoryError(f"archive exceeds size limit for {source.source_id}")
                    output.write(block)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    actual = _digest(path)
    if actual != source.archive_sha256:
        raise InventoryError(
            f"archive SHA-256 mismatch for {source.source_id}: expected "
            f"{source.archive_sha256}, got {actual}"
        )
    return path


def _safe_members(archive: tarfile.TarFile, expected_root: str) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members:
        raise InventoryError("source archive is empty")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or path.parts[0] != expected_root:
            raise InventoryError(f"unsafe archive member path: {member.name!r}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise InventoryError(f"unexpected link or special archive member: {member.name!r}")
    return members


def _safe_source_tree(source: Source, archive_path: Path, temporary: Path) -> tuple[Path, int]:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = _safe_members(archive, source.archive_root)
        expanded_bytes = sum(member.size for member in members if member.isfile())
        if expanded_bytes > source.max_expanded_bytes:
            raise InventoryError(f"expanded archive exceeds size limit for {source.source_id}")
        archive.extractall(temporary, members=members)
    extracted_root = temporary / source.archive_root
    if not extracted_root.is_dir():
        raise InventoryError(f"expected archive root is missing: {source.archive_root}")
    return extracted_root, len(members)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _blackmatrix_inventory(tree: Path, source: Source) -> dict[str, Any]:
    rule_root = tree / "rule/Surge"
    directories = sorted(
        child.name for child in rule_root.iterdir() if child.is_dir()
    )
    categories: dict[str, Any] = {}
    for name in directories:
        rel = f"rule/Surge/{name}/{name}.list"
        rule_file = tree / rel
        if not rule_file.is_file():
            continue
        text = _read_text(rule_file)
        counts: Counter[str] = Counter()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";", "//")):
                continue
            kind = line.split(",", 1)[0].upper() if "," in line else "BARE"
            counts[kind] += 1
        header = re.search(r"^#\s*UPDATED:\s*(.+?)\s*$", text, re.MULTILINE)
        readme = tree / f"rule/Surge/{name}/README.md"
        readme_date = None
        if readme.is_file():
            match = re.search(r"最后更新时间\s*[：:]\s*([^\n]+)", _read_text(readme))
            if match:
                readme_date = match.group(1).strip()
        categories[name] = {
            "path": rel,
            "rule_header_updated": header.group(1).strip() if header else None,
            "readme_last_updated": readme_date,
            "counts": dict(sorted(counts.items())),
            "line_count": sum(counts.values()),
        }
    return {
        "category_directory_count": len(directories),
        "primary_rule_file_count": len(categories),
        "directories_without_primary_file": sorted(set(directories) - set(categories)),
        "categories": categories,
    }


def _v2fly_inventory(tree: Path, source: Source) -> dict[str, Any]:
    files = {path.name: path for path in (tree / "data").iterdir() if path.is_file()}
    lists: dict[str, Any] = {}
    for key, path in sorted(files.items()):
        counts: Counter[str] = Counter()
        includes: list[str] = []
        attributes: set[str] = set()
        affiliations: set[str] = set()
        for raw in _read_text(path).splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            token = line.split(None, 1)[0]
            if token.startswith("include:"):
                kind = "include"
                includes.append(token.split(":", 1)[1])
            elif ":" in token:
                kind = token.split(":", 1)[0].lower()
            else:
                kind = "bare-domain"
            counts[kind] += 1
            attributes.update(re.findall(r"(?<!\S)@([\w.!-]+)", line))
            affiliations.update(re.findall(r"(?<!\S)&([\w.!-]+)", line))
        lists[key] = {
            "path": f"data/{key}",
            "counts": dict(sorted(counts.items())),
            "line_count": sum(counts.values()),
            "includes": includes,
            "attribute_names": sorted(attributes),
            "affiliation_targets": sorted(affiliations),
            "rule_header_updated": None,
        }
    return {"key_count": len(lists), "keys": lists}


def inventory(cache: Path = CACHE, offline: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"inventory_schema": 1, "sources": {}}
    for source in SOURCES:
        path = _archive_path(source, cache, offline)
        with tempfile.TemporaryDirectory(prefix=".catalog-inventory-", dir=cache) as temporary_name:
            tree, member_count = _safe_source_tree(source, path, Path(temporary_name))
            details = (
                _blackmatrix_inventory(tree, source)
                if source.source_id == "blackmatrix7"
                else _v2fly_inventory(tree, source)
            )
        result["sources"][source.source_id] = {
            "repository": source.repository,
            "revision": source.revision,
            "commit_date": source.commit_date,
            "archive_sha256": source.archive_sha256,
            "archive_bytes": path.stat().st_size,
            "archive_member_count": member_count,
            **details,
        }
    return result


def self_check(data: dict[str, Any]) -> None:
    blackmatrix = data["sources"]["blackmatrix7"]
    v2fly = data["sources"]["v2fly"]
    if blackmatrix["category_directory_count"] != 669:
        raise InventoryError(f"blackmatrix directory count changed: {blackmatrix['category_directory_count']}")
    if blackmatrix["directories_without_primary_file"] != ["AdGuardSDNSFilter", "Assassin'sCreed", "Cloud", "EasyPrivacy"]:
        raise InventoryError("blackmatrix primary-file gap inventory changed")
    if v2fly["key_count"] != 1545:
        raise InventoryError(f"v2fly data-key count changed: {v2fly['key_count']}")
    requested = {
        "perplexity": True,
        "xai": True,
        "cursor": True,
        "huggingface": True,
        "netlify": True,
        "pypi": False,
        "tmdb": True,
        "fanart": False,
        "pornhub": True,
        "xvideos": True,
        "xnxx": True,
        "xhamster": True,
        "spankbang": True,
        "javdb": True,
        "javbus": True,
        "missav": True,
        "jable": True,
    }
    for key, expected in requested.items():
        actual = key in v2fly["keys"]
        if actual != expected:
            raise InventoryError(f"v2fly key inventory mismatch for {key}: expected {expected}, got {actual}")
    category_porn = v2fly["keys"].get("category-porn")
    if category_porn is None or not {"javdb", "javbus", "missav", "jable", "xvideos", "xnxx", "xhamster", "spankbang"}.issubset(set(category_porn["includes"])):
        raise InventoryError("v2fly category-porn include inventory is incomplete")
    try:
        import yaml

        catalog = yaml.safe_load((ROOT / "config/service-catalog.yaml").read_text(encoding="utf-8"))
    except Exception as exc:
        raise InventoryError(f"service catalog cannot be loaded: {exc}") from exc
    if catalog.get("default_enabled") is not False:
        raise InventoryError("catalog must not enable unreviewed services by default")
    required_groups = {"AI", "Development", "Communication", "Social", "Streaming", "Music", "Gaming", "Media-Metadata", "Cloud", "Knowledge", "Payments", "Apple-Overseas", "Adult", "Foreign-General"}
    if not required_groups.issubset(set(catalog.get("group_model", {}).get("purpose_groups", []))):
        raise InventoryError("service catalog is missing an approved purpose group")
    services = catalog.get("services", {})
    if "tiktok" in services:
        raise InventoryError("TikTok must remain excluded from the active research catalog")
    for candidate in ("pypi", "tvdb", "moviepilot_integration"):
        if services.get(candidate, {}).get("missing") is not True:
            raise InventoryError(f"manual candidate {candidate} must remain explicitly source-missing")
    available_by_source = {"blackmatrix7": blackmatrix["categories"], "v2fly": v2fly["keys"]}
    if catalog.get("group_model", {}).get("apple_default") is None or "direct" not in catalog["group_model"]["apple_default"].lower():
        raise InventoryError("common Apple services must retain explicit direct-by-default behavior")
    if "Apple-Overseas" not in catalog["group_model"].get("apple_overseas", "") or "disabled" not in catalog["group_model"]["apple_overseas"].lower():
        raise InventoryError("Apple-Overseas must remain an optional disabled selector")
    for service, spec in services.items():
        if spec.get("review_state") not in {"pending", "approved", "manual_missing"} or not isinstance(spec.get("default_enabled"), bool):
            raise InventoryError(f"service review state/default is missing: {service}")
        if spec.get("route_intent") not in {"proxy", "direct", "review"}:
            raise InventoryError(f"service route intent is missing: {service}")
        if not isinstance(spec.get("label"), str) or not isinstance(spec.get("use_group"), str) or not isinstance(spec.get("policy_group"), str):
            raise InventoryError(f"service group metadata is incomplete: {service}")
        if not isinstance(spec.get("typed_counts"), dict) or not isinstance(spec.get("risks"), list) or not isinstance(spec.get("dependencies"), list) or not isinstance(spec.get("evidence"), list):
            raise InventoryError(f"service enriched report metadata is incomplete: {service}")
        candidates = spec.get("source_candidates", [])
        if not isinstance(candidates, list):
            raise InventoryError(f"source candidates must be a list: {service}")
        for source_id, available in available_by_source.items():
            key = spec.get(source_id)
            if key and key not in available:
                raise InventoryError(f"catalog reference does not exist: {service} -> {source_id}:{key}")
            if key:
                expected_path = available[key]["path"] if source_id == "blackmatrix7" else f"data/{key}"
                if not any(candidate.get("source_id") == source_id and candidate.get("key") == key and candidate.get("path") == expected_path for candidate in candidates):
                    raise InventoryError(f"catalog candidate metadata is incomplete: {service} -> {source_id}:{key}")
        selected = spec.get("selected_ref")
        if selected is not None and not any(
            candidate.get("source_id") == selected.get("source_id")
            and (candidate.get("path") == selected.get("file") or candidate.get("key") == selected.get("file") or (candidate.get("source_id") == "manual_curated" and selected.get("file") == "fanart.list"))
            for candidate in candidates
        ):
            raise InventoryError(f"selected source ref does not match a catalog candidate: {service}")
        if spec.get("implementation_status") == "prohibited_aggregate" and spec.get("default_enabled"):
            raise InventoryError(f"prohibited aggregate must stay disabled: {service}")
        if spec.get("missing") is not True and not candidates:
            raise InventoryError(f"catalog service has neither a source candidate nor explicit missing status: {service}")
    if services.get("tiktok") is not None:
        raise InventoryError("TikTok must remain absent from the active catalog")
    categories = yaml.safe_load((ROOT / "config/categories.yaml").read_text(encoding="utf-8")).get("categories", {})
    for category, cfg in categories.items():
        service = cfg.get("catalog_service_id")
        if service and service not in services:
            raise InventoryError(f"runtime category references an unknown service: {category} -> {service}")
        if service and cfg.get("enabled") != services[service].get("default_enabled"):
            raise InventoryError(f"runtime/category default mismatch: {category} -> {service}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE)
    parser.add_argument("--offline", action="store_true", help="use only checksum-verified archives already in cache")
    parser.add_argument("--check", action="store_true", help="assert pinned inventory facts before printing")
    args = parser.parse_args(argv)
    try:
        data = inventory(args.cache_dir, args.offline)
        if args.check:
            self_check(data)
    except (InventoryError, OSError, tarfile.TarError) as exc:
        print(f"catalog inventory failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
