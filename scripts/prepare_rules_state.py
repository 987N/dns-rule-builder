#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from dns_rule_builder.rules_state import apply_state


parser = argparse.ArgumentParser()
parser.add_argument("--project-root", type=Path, default=Path.cwd())
parser.add_argument("--state-file", type=Path, required=True)
args = parser.parse_args()
print(apply_state(args.project_root, args.state_file))
