"""Release-bound, short-lived evidence for manual target deployment admission."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from .engine import BuildError


KNOWN_PREPARATION_BLOCKERS = frozenset({
    "target SmartDNS/Mihomo semantics not verified",
    "deployment master switch is disabled",
})
ATTESTATION_TTL_SECONDS = 3600


class AttestationError(BuildError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AttestationError("attestation timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise AttestationError("attestation timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def check_preparation_candidate(manifest: dict[str, Any]) -> None:
    """Allow preflight of only the two explicit target/master blockers."""
    if manifest.get("deployment_allowed") is True:
        return
    blockers = manifest.get("deployment_blockers")
    reasons = [item.get("reason") for item in blockers] if isinstance(blockers, list) and all(isinstance(item, dict) for item in blockers) else None
    if (not isinstance(blockers, list) or len(blockers) != 2 or not isinstance(reasons, list) or len(reasons) != 2
            or set(reasons) != KNOWN_PREPARATION_BLOCKERS
            or manifest.get("review_required") is not False or manifest.get("publish_allowed") is not True
            or manifest.get("source_license_blockers") != [] or manifest.get("publish_blockers") != []):
        raise AttestationError("candidate has blockers outside the exact target-preparation allowlist")


def create_attestation(
    *,
    request_id: str,
    release_id: str,
    manifest_sha256: str,
    envelopes: dict[str, dict[str, Any]],
    previous_releases: dict[str, str | None],
    validation_evidence: dict[str, dict[str, Any]],
    now: datetime | None = None,
    ttl_seconds: int = ATTESTATION_TTL_SECONDS,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", release_id) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise AttestationError("attestation release or manifest digest is invalid")
    if ttl_seconds < 1 or ttl_seconds > 24 * 3600:
        raise AttestationError("attestation TTL is outside the supported range")
    if set(envelopes) != set(previous_releases) or set(envelopes) != set(validation_evidence):
        raise AttestationError("attestation does not contain an exact target set")
    target_rows = {}
    for target_id in sorted(envelopes):
        envelope = envelopes[target_id]
        evidence = validation_evidence[target_id]
        envelope_digest = sha256_json(envelope)
        if (envelope.get("target_id") != target_id or envelope.get("release_id") != release_id
                or evidence.get("target_id") != target_id or evidence.get("release_id") != release_id
                or evidence.get("target_manifest_sha256") != envelope_digest or evidence.get("result") != "ok"):
            raise AttestationError(f"{target_id}: validation evidence is not bound to its target manifest")
        binary_hashes = evidence.get("binary_sha256")
        config_hashes = evidence.get("config_sha256")
        if (not isinstance(binary_hashes, dict) or not binary_hashes or not isinstance(config_hashes, dict) or not config_hashes
                or any(not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) for name, digest in (*binary_hashes.items(), *config_hashes.items()))):
            raise AttestationError(f"{target_id}: validation evidence is missing binary/config hashes")
        target_rows[target_id] = {
            "role": envelope["role"],
            "target_manifest_sha256": envelope_digest,
            "files": dict(sorted(envelope["files"].items())),
            "previous_release_id": previous_releases[target_id],
            "binary_sha256": dict(sorted(binary_hashes.items())),
            "config_sha256": dict(sorted(config_hashes.items())),
            "strategy_validation_sha256": evidence.get("strategy_validation_sha256"),
            "validation": "passed",
        }
        strategy_digest = target_rows[target_id]["strategy_validation_sha256"]
        if not isinstance(strategy_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", strategy_digest):
            raise AttestationError(f"{target_id}: strategy validation digest is missing")
    instant = (now or utc_now()).astimezone(timezone.utc)
    document = {
        "schema_version": 1,
        "request_id": request_id,
        "release_id": release_id,
        "manifest_sha256": manifest_sha256,
        "created_at": instant.isoformat().replace("+00:00", "Z"),
        "expires_at": (instant + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
        "targets": target_rows,
    }
    document["attestation_sha256"] = sha256_json(document)
    return document


def verify_attestation(
    document: dict[str, Any],
    *,
    release_id: str,
    manifest_sha256: str,
    envelopes: dict[str, dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise AttestationError("target validation attestation schema is invalid")
    unsigned = {key: value for key, value in document.items() if key != "attestation_sha256"}
    if document.get("attestation_sha256") != sha256_json(unsigned):
        raise AttestationError("target validation attestation checksum mismatch")
    if document.get("release_id") != release_id or document.get("manifest_sha256") != manifest_sha256:
        raise AttestationError("target validation attestation belongs to another release")
    created, expires = parse_utc(document.get("created_at")), parse_utc(document.get("expires_at"))
    instant = (now or utc_now()).astimezone(timezone.utc)
    if created > instant or expires <= instant or expires <= created or expires - created > timedelta(hours=24):
        raise AttestationError("target validation attestation is stale or has invalid times")
    targets = document.get("targets")
    if not isinstance(targets, dict) or set(targets) != set(envelopes):
        raise AttestationError("target validation attestation does not cover both configured targets")
    for target_id, envelope in envelopes.items():
        row = targets[target_id]
        if (not isinstance(row, dict) or row.get("target_manifest_sha256") != sha256_json(envelope)
                or row.get("files") != dict(sorted(envelope.get("files", {}).items()))
                or row.get("role") != envelope.get("role") or row.get("validation") != "passed"):
            raise AttestationError(f"{target_id}: attestation target evidence does not match current candidate")
        for key in ("binary_sha256", "config_sha256"):
            hashes = row.get(key)
            if not isinstance(hashes, dict) or not hashes or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x) for x in hashes.values()):
                raise AttestationError(f"{target_id}: attestation {key} is invalid")
    return document
