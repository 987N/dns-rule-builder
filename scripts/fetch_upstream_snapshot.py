#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dns_rule_builder.engine import load_yaml
from dns_rule_builder.snapshot import fetch_sources_snapshot


parser = argparse.ArgumentParser()
parser.add_argument("--cache-root", type=Path, default=Path(".cache/upstream"))
parser.add_argument("--config", type=Path, default=Path("config/sources.yaml"))
args = parser.parse_args()
sources = load_yaml(args.config).get("sources", {})
project_root = args.config.resolve().parent.parent
categories = load_yaml(project_root / "config/categories.yaml").get("categories", {})
required_sources = set()
for category in categories.values():
    if not isinstance(category, dict) or not category.get("enabled"):
        continue
    refs = category.get("source_refs")
    if refs is not None:
        required_sources.update(ref.get("source_id") for ref in refs if isinstance(ref, dict) and isinstance(ref.get("source_id"), str))
    elif category.get("source_file"):
        required_sources.add("blackmatrix7")
missing = required_sources - set(sources)
if missing:
    raise SystemExit(f"configured sources are missing: {', '.join(sorted(missing))}")
snapshot = fetch_sources_snapshot(args.cache_root, {key: sources[key] for key in sorted(required_sources)}, github_token=os.environ.get("GITHUB_TOKEN"), project_root=project_root)
print(json.dumps({"schema_version": 1, "sources": snapshot}, ensure_ascii=False, sort_keys=True))
