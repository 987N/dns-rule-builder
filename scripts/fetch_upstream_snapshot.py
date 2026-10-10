#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dns_rule_builder.snapshot import fetch_master_snapshot


parser = argparse.ArgumentParser()
parser.add_argument("--cache-root", type=Path, default=Path(".cache/upstream"))
args = parser.parse_args()
revision, archive_sha256, source_root, source_date_utc = fetch_master_snapshot(args.cache_root, github_token=os.environ.get("GITHUB_TOKEN"))
print(json.dumps({"source_revision": revision, "archive_sha256": archive_sha256, "source_root": str(source_root), "source_date_utc": source_date_utc}))
