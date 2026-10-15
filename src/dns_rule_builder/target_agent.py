"""Forced-command entrypoint for dns-rule-target-v4."""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

from .target_runtime import run_agent


def main() -> int:
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    try:
        args = shlex.split(original, posix=True)
    except ValueError:
        return 2
    if len(args) != 4 or args[0] != "dns-rule-target-v4":
        return 2
    operation, request_id, release_id = args[1:]
    if operation not in {"stage", "validate", "activate", "rollback", "status", "recovery-status", "strategies", "strategy-select", "strategy-preview", "strategy-apply"}:
        return 2
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", request_id):
        return 2
    if release_id != "none" and not re.fullmatch(r"[0-9a-f]{64}", release_id):
        return 2
    config_file = Path(os.environ.get("DNS_RULE_TARGET_CONFIG", "/etc/dns-rule-target-v4.yaml"))
    return run_agent(config_file, operation, request_id, release_id, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
