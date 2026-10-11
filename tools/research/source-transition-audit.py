#!/usr/bin/env python3
"""Compare the currently selected Blackmatrix rules with approved v2fly keys.

This is a local research utility. It reads only the pinned archives named by
``.cache/root-expansion-sources.json`` and writes generated evidence beneath
``.cache/source-transition-audit``; it never edits project configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dns_rule_builder.sources import parse_source  # noqa: E402


PINNED_V2_KEYS = {
    "openai": "openai",
    "claude": "anthropic",
    "gemini": "google-gemini",
    "copilot": "github-copilot",
    "google": "google",
    "telegram": "telegram",
    "github": "github",
    "docker": "docker",
    "gitlab": "gitlab",
    "jetbrains": "jetbrains",
    "vercel": "vercel",
    "discord": "discord",
    "reddit": "reddit",
    "youtube": "youtube",
    "tmdb": "tmdb",
}
LEGACY_BM_FILES = {
    "openai": "rule/Surge/OpenAI/OpenAI.list",
    "claude": "rule/Surge/Claude/Claude.list",
    "gemini": "rule/Surge/Gemini/Gemini.list",
    "copilot": "rule/Surge/Copilot/Copilot.list",
    "google": "rule/Surge/Google/Google.list",
    "telegram": "rule/Surge/Telegram/Telegram.list",
    "github": "rule/Surge/GitHub/GitHub.list",
    "docker": "rule/Surge/Docker/Docker.list",
    "gitlab": "rule/Surge/GitLab/GitLab.list",
    "jetbrains": "rule/Surge/Jetbrains/Jetbrains.list",
    "vercel": "rule/Surge/Vercel/Vercel.list",
    "discord": "rule/Surge/Discord/Discord.list",
    "reddit": "rule/Surge/Reddit/Reddit.list",
    "youtube": "rule/Surge/YouTube/YouTube.list",
    "tmdb": "rule/Surge/Tmdb/Tmdb.list",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_rows(source_id: str, root: Path, ref: str, fmt: str, source_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    return parse_source(source_id, root, ref, {**source_cfg, "format": fmt})


def semantic_set(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(row["domain"], row["match_type"]) for row in rows}


def types(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(row["match_type"] for row in rows).items()))


def include_closure(root: Path, key: str) -> dict[str, str]:
    """Return file hashes that can contribute to one key's parsed output.

    v2fly affiliation keys are virtual and collect matching @affiliation rows
    across the tree, so those contributor files are included in this closure.
    Unrelated files scanned only to build the affiliation index are not approval
    scope.
    """
    base = root / "data"
    files = sorted(path for path in base.rglob("*") if path.is_file())
    includes: dict[str, list[str]] = {}
    affiliation_files: dict[str, set[str]] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        node_key = path.relative_to(base).as_posix().upper()
        includes.setdefault(node_key, [])
        for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            head = tokens[0]
            if head.lower().startswith("include:"):
                includes[node_key].append(head.split(":", 1)[1].upper())
            else:
                for token in tokens[1:]:
                    if re.fullmatch(r"&[A-Za-z0-9!-]+", token):
                        affiliation_files.setdefault(token[1:].upper(), set()).add(relative)
    closure: set[str] = set()
    visited: set[str] = set()

    def visit(current: str) -> None:
        if current in visited:
            return
        visited.add(current)
        physical = base / current.lower()
        # Key names in this snapshot are lowercase, but keep a case-insensitive
        # fallback so the closure matches the adapter's uppercase key lookup.
        if physical.is_file():
            rel = physical.relative_to(root).as_posix()
            closure.add(rel)
            for child in includes.get(current, []):
                visit(child)
        else:
            for rel in sorted(affiliation_files.get(current, set())):
                closure.add(rel)
                file_key = (root / rel).relative_to(base).as_posix().upper()
                for child in includes.get(file_key, []):
                    visit(child)
        for rel in sorted(affiliation_files.get(current, set())):
            closure.add(rel)
            file_key = (root / rel).relative_to(base).as_posix().upper()
            for child in includes.get(file_key, []):
                visit(child)

    visit(key.upper())
    return {rel: sha((root / rel).read_bytes()) for rel in sorted(closure)}


def audit(snapshot_path: Path, out_dir: Path) -> dict[str, Any]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    sources = snapshot["sources"]
    bm = sources["blackmatrix7"]
    v2 = sources["v2fly"]
    bm_root, v2_root = Path(bm["source_root"]), Path(v2["source_root"])
    project_sources = yaml.safe_load((ROOT / "config/sources.yaml").read_text(encoding="utf-8"))["sources"]
    categories = yaml.safe_load((ROOT / "config/categories.yaml").read_text(encoding="utf-8"))["categories"]
    approvals = yaml.safe_load((ROOT / "config/source-approvals.yaml").read_text(encoding="utf-8"))
    excluded = approvals.get("approved_exclusions", [])
    report_rows = []
    for category, key in PINNED_V2_KEYS.items():
        cfg = categories[category]
        bm_rel = LEGACY_BM_FILES[category]
        bm_records, bm_hashes, bm_unsupported = parse_rows("blackmatrix7", bm_root, bm_rel, cfg.get("format", "surge"), project_sources["blackmatrix7"])
        v2_records, v2_hashes, v2_unsupported = parse_rows("v2fly", v2_root, key, "v2fly-domain-list", project_sources["v2fly"])
        applicable = [item for item in excluded if item.get("category") == category and item.get("source_id") == "v2fly" and item.get("source_key") == key]
        applied_exclusions = []
        for item in applicable:
            raw_file = v2_root / item["source_file"]
            lines = raw_file.read_text(encoding="utf-8").splitlines()
            raw = lines[item["source_line"] - 1]
            if sha(raw.encode()) != item["raw_sha256"]:
                raise ValueError(f"approved source exclusion raw hash mismatch: {category}/{key}/{item['domain']}")
            candidates = [row for row in v2_records if row["domain"] == item["domain"] and row["match_type"] == item["match_type"] and row["source_file"] == item["source_file"] and row["line"] == item["source_line"]]
            if len(candidates) != 1:
                raise ValueError(f"approved source exclusion did not identify one exact source row: {category}/{key}/{item['domain']}")
            v2_records.remove(candidates[0])
            applied_exclusions.append({"domain": item["domain"], "match_type": item["match_type"], "source_file": item["source_file"], "line": item["source_line"], "raw_sha256": item["raw_sha256"], "reason": item["reason"]})
        selected_refs = cfg.get("source_refs", [])
        manual_records, manual_hashes = [], {}
        for ref in selected_refs:
            if ref.get("source_id") != "manual_curated":
                continue
            rows, hashes, unsupported_rows = parse_rows("manual_curated", ROOT, f"config/curated/{ref['file']}", ref["format"], project_sources["manual_curated"])
            manual_records.extend(rows)
            manual_hashes.update(hashes)
            if unsupported_rows:
                raise ValueError(f"manual curated source contains unsupported input: {category}/{ref['file']}")
        selected_records = v2_records + manual_records
        bm_sem, v2_sem, selected_sem = semantic_set(bm_records), semantic_set(v2_records), semantic_set(selected_records)
        bm_domains, v2_domains = {d for d, _ in bm_sem}, {d for d, _ in v2_sem}
        changed_match_type = sorted({d for d in bm_domains & v2_domains if {(d, t) for d2, t in bm_sem if d2 == d} != {(d, t) for d2, t in v2_sem if d2 == d}})
        closure_hashes = include_closure(v2_root, key)
        row = {
            "category": category,
            "selected_key": key,
            "blackmatrix7": {"revision": bm["revision"], "source_file": bm_rel, "rule_count": len(bm_records), "match_type_counts": types(bm_records), "unsupported_count": len(bm_unsupported), "file_sha256": bm_hashes},
            "v2fly_raw": {"revision": v2["revision"], "key": key, "rule_count": len(v2_records) + len(applied_exclusions), "match_type_counts": types(v2_records + [dict(domain=e["domain"], match_type=e["match_type"]) for e in applied_exclusions]), "unsupported_count": len(v2_unsupported), "include_closure_file_count": len(closure_hashes), "include_closure_file_sha256": closure_hashes},
            "selected_after_exact_exclusions": {"rule_count": len(v2_records), "match_type_counts": types(v2_records), "excluded_rule_count": len(applied_exclusions), "exclusions": applied_exclusions},
            "manual_curated_additions": {"rule_count": len(manual_records), "match_type_counts": types(manual_records), "file_sha256": manual_hashes},
            "selected_effective": {"rule_count": len(selected_records), "match_type_counts": types(selected_records)},
            "semantic_overlap_count": len(bm_sem & selected_sem),
            "semantic_added_count": len(selected_sem - bm_sem),
            "semantic_removed_count": len(bm_sem - selected_sem),
            "added": [{"domain": d, "match_type": t} for d, t in sorted(selected_sem - bm_sem)],
            "removed": [{"domain": d, "match_type": t} for d, t in sorted(bm_sem - selected_sem)],
            "match_type_changed_domains": changed_match_type,
            "shared_domains": sorted(bm_domains & v2_domains),
            "source_partial": bool(v2_unsupported),
            "unsupported": [{"source_id": "v2fly", "source_revision": v2["revision"], **item} for item in v2_unsupported],
        }
        row["shared_domains"] = sorted(bm_domains & {d for d, _ in selected_sem})
        row["match_type_changed_domains"] = sorted({d for d in bm_domains & {d for d, _ in selected_sem} if {(d, t) for d2, t in bm_sem if d2 == d} != {(d, t) for d2, t in selected_sem if d2 == d}})
        row["source_partial"] = bool(v2_unsupported)
        row["intentional_exclusion_count"] = len(applied_exclusions)
        report_rows.append(row)
    cross = []
    # Re-read selected rules only for overlap summary; this remains bounded to the reviewed 15 categories.
    for idx, left in enumerate(report_rows):
        left_service = categories[left["category"]]
        lr, _, _ = parse_rows("v2fly", v2_root, PINNED_V2_KEYS[left["category"]], "v2fly-domain-list", project_sources["v2fly"])
        for exclusion in excluded:
            if exclusion.get("category") == left["category"] and exclusion.get("source_id") == "v2fly":
                lr = [r for r in lr if not (r["domain"] == exclusion["domain"] and r["match_type"] == exclusion["match_type"])]
        for ref in left_service.get("source_refs", []):
            if ref.get("source_id") == "manual_curated":
                more, _, _ = parse_rows("manual_curated", ROOT, f"config/curated/{ref['file']}", ref["format"], project_sources["manual_curated"])
                lr.extend(more)
        lset = semantic_set(lr)
        for right in report_rows[idx+1:]:
            rr, _, _ = parse_rows("v2fly", v2_root, PINNED_V2_KEYS[right["category"]], "v2fly-domain-list", project_sources["v2fly"])
            for exclusion in excluded:
                if exclusion.get("category") == right["category"] and exclusion.get("source_id") == "v2fly":
                    rr = [r for r in rr if not (r["domain"] == exclusion["domain"] and r["match_type"] == exclusion["match_type"])]
            for ref in categories[right["category"]].get("source_refs", []):
                if ref.get("source_id") == "manual_curated":
                    more, _, _ = parse_rows("manual_curated", ROOT, f"config/curated/{ref['file']}", ref["format"], project_sources["manual_curated"])
                    rr.extend(more)
            overlap = sorted(lset & semantic_set(rr))
            if overlap:
                cross.append({"category_a":left["category"],"category_b":right["category"],"overlap_count":len(overlap),"examples":[{"domain":d,"match_type":t} for d,t in overlap[:25]]})
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "snapshot_file": str(snapshot_path),
        "sources": {source_id: {key: value for key, value in entry.items() if key != "source_root"} for source_id, entry in sources.items()},
        "categories": report_rows,
        "totals": {
            "category_count": len(report_rows),
            "blackmatrix7_rule_count": sum(row["blackmatrix7"]["rule_count"] for row in report_rows),
            "v2fly_raw_rule_count": sum(row["v2fly_raw"]["rule_count"] for row in report_rows),
            "excluded_exact_rule_count": sum(row["selected_after_exact_exclusions"]["excluded_rule_count"] for row in report_rows),
            "curated_exact_addition_count": sum(row["manual_curated_additions"]["rule_count"] for row in report_rows),
            "selected_effective_rule_count": sum(row["selected_effective"]["rule_count"] for row in report_rows),
            "semantic_added_count": sum(row["semantic_added_count"] for row in report_rows),
            "semantic_removed_count": sum(row["semantic_removed_count"] for row in report_rows),
            "v2fly_unsupported_count": sum(row["v2fly_raw"]["unsupported_count"] for row in report_rows),
        },
        "cross_category_shared_semantics": cross,
        "warnings": [
            "Counts compare one pinned source snapshot to another; they do not establish user traffic coverage.",
            "v2fly/google recursively includes YouTube and Google DeepMind; preserve source provenance and do not interpret this as a foreign-domain aggregate.",
            "v2fly row attributes such as @cn, @ads, and @telemetry are recorded as source metadata, not automatically mapped to routing policy.",
            "Shared hosts and unreviewed source semantics remain separate review gates.",
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / ".cache/root-expansion-sources.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/source-transition-audit")
    args = parser.parse_args()
    summary = audit(args.snapshot, args.output)
    print(json.dumps({"output": str((args.output / "summary.json").resolve()), "totals": summary["totals"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
