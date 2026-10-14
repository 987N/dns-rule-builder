from __future__ import annotations

import csv
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .catalog import CatalogError, load_service_catalog
from .source_approvals import ApprovalError, approval_scope_sha256, exclusion_signature, load_source_approvals, raw_sha256, shared_scope_for_category, unsupported_signature, validate_rules_data_dispositions, validate_unsupported_dispositions


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class Rule:
    domain: str
    match_type: str
    category: str
    provider_name: str
    policy_group: str
    priority: int
    source: str
    source_file: str
    source_revision: str
    line: int
    custom: bool = False
    source_attributes: tuple[str, ...] = ()
    include_chain: tuple[str, ...] = ()
    affiliations: tuple[str, ...] = ()
    source_key: str | None = None
    raw_sha256: str | None = None

    def mihomo(self) -> str:
        return f"{self.match_type.replace('EXACT', 'DOMAIN').replace('SUFFIX', 'DOMAIN-SUFFIX')},{self.domain}"

    def smartdns(self) -> str:
        return f"-.{self.domain}" if self.match_type == "EXACT" else self.domain


@dataclass
class BuildResult:
    release_id: str
    output_dir: Path
    deployment_allowed: bool
    summary: dict[str, Any]


INTERNAL_SUFFIXES = (".local", ".lan", ".home.arpa")
DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def canonical_domain(value: str) -> str:
    raw = value.strip()
    if not raw or any(ch.isspace() for ch in raw) or "://" in raw or any(c in raw for c in "/?#@"):
        raise ValueError("expected a bare DNS domain without URL, port, path, query, or whitespace")
    if raw.endswith("."):
        raw = raw[:-1]
    try:
        ipaddress.ip_address(raw.strip("[]"))
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not domain rules")
    try:
        domain = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("invalid IDNA domain") from exc
    if len(domain) > 253 or not DOMAIN_RE.fullmatch(domain):
        raise ValueError("invalid DNS name or top-level suffix")
    if any(domain == suffix[1:] or domain.endswith(suffix) for suffix in INTERNAL_SUFFIXES):
        raise ValueError("local and home domains are excluded from proxy rules")
    return domain


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BuildError(f"cannot read YAML {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildError(f"expected a YAML mapping: {path}")
    return data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _read_source_file(path: Path, logical_path: str, category: str, cfg: dict[str, Any], source_revision: str, source_id: str = "blackmatrix7") -> tuple[list[Rule], list[dict[str, Any]]]:
    rules: list[Rule] = []
    unsupported: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if cfg.get("format", "surge") == "surge-domain-set":
            kind, raw_domain = ("DOMAIN-SUFFIX", line[1:]) if line.startswith(".") else ("DOMAIN", line)
            fields = [kind, raw_domain]
        elif cfg.get("format", "surge") == "surge":
            try:
                fields = next(csv.reader([line], skipinitialspace=True))
            except csv.Error as exc:
                unsupported.append({"category": category, "source_file": logical_path, "line": line_number, "raw": raw, "reason": f"CSV parse error: {exc}"})
                continue
            kind = fields[0].strip().upper()
            if kind not in {"DOMAIN", "DOMAIN-SUFFIX"}:
                unsupported.append({"category": category, "source_file": logical_path, "line": line_number, "raw": raw, "type": kind, "reason": "not an EXACT/SUFFIX domain rule"})
                continue
            if len(fields) != 2:
                unsupported.append({"category": category, "source_file": logical_path, "line": line_number, "raw": raw, "type": kind, "reason": "unexpected extra fields"})
                continue
        else:
            raise BuildError(f"category {category}: unsupported source format {cfg.get('format')!r}")
        try:
            domain = canonical_domain(fields[1])
        except ValueError as exc:
            unsupported.append({"category": category, "source_file": logical_path, "line": line_number, "raw": raw, "type": kind, "reason": str(exc)})
            continue
        rules.append(Rule(domain, "EXACT" if kind == "DOMAIN" else "SUFFIX", category, cfg["provider_name"], cfg["policy_group"], int(cfg["priority"]), source_id, logical_path, source_revision, line_number, raw_sha256=raw_sha256(line.strip())))
    return rules, unsupported


def _overlaps(a: Rule, b: Rule) -> bool:
    if a.match_type == b.match_type == "EXACT":
        return a.domain == b.domain
    if a.match_type == "SUFFIX" and b.match_type == "SUFFIX":
        return a.domain == b.domain or a.domain.endswith("." + b.domain) or b.domain.endswith("." + a.domain)
    exact, suffix = (a, b) if a.match_type == "EXACT" else (b, a)
    return exact.domain == suffix.domain or exact.domain.endswith("." + suffix.domain)


def _contained(rule: Rule, domain: str) -> bool:
    return domain == rule.domain or (rule.match_type == "SUFFIX" and domain.endswith("." + rule.domain))


def _strictly_more_specific(left: Rule, right: Rule) -> bool:
    """Whether left matches a strict subset of the domains matched by right."""
    if left.match_type == right.match_type == "SUFFIX":
        return left.domain != right.domain and left.domain.endswith("." + right.domain)
    if left.match_type == "EXACT" and right.match_type == "SUFFIX":
        return _contained(right, left.domain)
    return False


def _provider_yaml(rules: list[Rule]) -> str:
    items = sorted({r.mihomo() for r in rules})
    return "payload:\n" + "".join(f"  - {json.dumps(item, ensure_ascii=False)}\n" for item in items)


def _provider_yaml_from_rows(rows: list[dict[str, Any]]) -> str:
    items = sorted({f"{row['match_type'].replace('EXACT', 'DOMAIN').replace('SUFFIX', 'DOMAIN-SUFFIX')},{row['domain']}" for row in rows})
    return "payload:\n" + "".join(f"  - {json.dumps(item, ensure_ascii=False)}\n" for item in items)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


def _safe_license_copy(root: Path, relative: str, license_root: Path) -> Path | None:
    """Return a regular in-scope license file, rejecting symlinks in any path component."""
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    candidate = root / rel
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return None
    resolved = candidate.resolve()
    if not resolved.is_relative_to(license_root) or not resolved.is_file():
        return None
    return resolved


class Builder:
    def __init__(self, project_root: Path, source_root: Path | None = None, source_revision: str | None = None, data_revision: str | None = None, code_commit: str | None = None, input_snapshot_time_utc: str | None = None, build_purpose: str = "rules", source_trees: dict[str, dict[str, Any]] | None = None):
        self.root = project_root.resolve()
        self.source_root = (source_root or (self.root / ".cache/ios_rule_script-83f37120")).resolve()
        self.sources_config_all = load_yaml(self.root / "config/sources.yaml")["sources"]
        self.sources_config = self.sources_config_all.get("blackmatrix7", {})
        self.categories = load_yaml(self.root / "config/categories.yaml").get("categories", {})
        self.legacy_source_mode = source_trees is None
        self.source_revision = source_revision
        self.data_revision = data_revision
        self.code_commit = code_commit
        self.input_snapshot_time_utc = input_snapshot_time_utc
        if build_purpose not in {"rules", "smoke"}:
            raise BuildError("build_purpose must be rules or smoke")
        self.build_purpose = build_purpose
        self.source_trees = {key: {**value, "root": Path(value["root"]).resolve()} for key, value in (source_trees or {}).items()}
        if self.legacy_source_mode:
            if not self.sources_config:
                raise BuildError("legacy source mode requires the blackmatrix7 source configuration")
            self.source_revision = self.source_revision or self.sources_config["revision"]
            archive = self.root / self.sources_config["archive"]
            archive_hash = sha256_file(archive) if archive.is_file() else self.sources_config.get("archive_sha256")
            self.source_trees = {"blackmatrix7": {"root": self.source_root, "revision": self.source_revision, "archive_sha256": archive_hash, "source_date_utc": None}}
        if "blackmatrix7" in self.source_trees:
            self.source_root = self.source_trees["blackmatrix7"]["root"]
        selected_source_ids = []
        for category, cfg in self.categories.items():
            if not isinstance(cfg, dict) or not cfg.get("enabled"):
                continue
            refs = cfg.get("source_refs")
            if refs is not None:
                selected_source_ids.extend(ref.get("source_id") for ref in refs if isinstance(ref, dict))
            elif cfg.get("source_file"):
                selected_source_ids.append("blackmatrix7")
        selected_source_ids = list(dict.fromkeys(selected_source_ids))
        self.primary_source_id = next((source_id for source_id in selected_source_ids if source_id in self.source_trees), None)
        if self.primary_source_id is None and "blackmatrix7" in self.source_trees:
            self.primary_source_id = "blackmatrix7"
        if self.primary_source_id is not None:
            locked_primary_revision = self.source_trees[self.primary_source_id]["revision"]
            if self.source_revision is not None and self.source_revision != locked_primary_revision:
                raise BuildError("source revision override does not match the selected primary source lock")
            self.source_revision = locked_primary_revision
        elif self.source_trees and self.source_revision is not None:
            raise BuildError("source revision override has no selected source tree")
        else:
            self.source_revision = None
        for source_id, tree in self.source_trees.items():
            if source_id not in self.sources_config_all or not re.fullmatch(r"[0-9a-f]{40}", str(tree.get("revision", ""))):
                raise BuildError(f"invalid locked source metadata for {source_id!r}")
            marker = Path(tree["root"]) / ".source-revision"
            if tree.get("kind") != "local" and (not marker.is_file() or marker.read_text(encoding="utf-8").strip() != tree["revision"]):
                raise BuildError(f"source tree revision marker does not match locked {source_id} revision")
            if tree.get("archive_sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(tree["archive_sha256"])):
                raise BuildError(f"invalid source archive checksum for {source_id}")
            if tree.get("kind") == "local" and self.code_commit and tree["revision"] != self.code_commit:
                raise BuildError(f"local source {source_id} must be locked to the builder code commit")
        if self.source_revision is not None and not re.fullmatch(r"[0-9a-f]{40}", self.source_revision):
            raise BuildError("source revision must be a full 40-character commit SHA")
        if self.data_revision is not None and not re.fullmatch(r"[0-9a-f]{40}", self.data_revision):
            raise BuildError("data revision must be a full 40-character commit SHA")
        if self.code_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", self.code_commit):
            raise BuildError("code revision must be a full 40-character commit SHA")
        if self.input_snapshot_time_utc is not None:
            try:
                parsed_build_time = datetime.fromisoformat(self.input_snapshot_time_utc.replace("Z", "+00:00"))
            except ValueError as exc:
                raise BuildError("input_snapshot_time_utc must be an ISO-8601 timestamp") from exc
            if parsed_build_time.tzinfo is None:
                raise BuildError("input_snapshot_time_utc must include a timezone")
            self.input_snapshot_time_utc = parsed_build_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if self.legacy_source_mode:
            marker = self.source_root / ".source-revision"
            if not marker.is_file():
                raise BuildError(f"source tree must include .source-revision containing {self.source_revision}")
            if marker.read_text(encoding="utf-8").strip() != self.source_revision:
                raise BuildError(f"source tree revision marker does not match requested {self.source_revision}")
        self.policies = load_yaml(self.root / "config/policies.yaml").get("policies", {})
        try:
            self.service_catalog = load_service_catalog(self.root).get("services", {})
        except CatalogError as exc:
            raise BuildError(str(exc)) from exc
        shared_config = load_yaml(self.root / "config/shared-domains.yaml")
        self.shared_domains = shared_config.get("shared_domains", [])
        if not isinstance(self.shared_domains, list):
            raise BuildError("config/shared-domains.yaml shared_domains must be a list")
        custom_path = self.root / "data/custom-rules.yaml"
        self.custom_data = load_yaml(custom_path) if custom_path.exists() else {"rules": [], "force_direct": []}
        if not isinstance(self.custom_data.get("rules", []), list) or not isinstance(self.custom_data.get("force_direct", []), list):
            raise BuildError("custom rules and force_direct must be YAML lists")
        self.custom_revision = sha256_file(custom_path) if custom_path.exists() else hashlib.sha256(b"rules: []\nforce_direct: []\n").hexdigest()
        exclusions_path = self.root / "data/approved-exclusions.yaml"
        exclusion_data = load_yaml(exclusions_path) if exclusions_path.exists() else {"exclusions": []}
        user_exclusions = exclusion_data.get("exclusions", [])
        if not isinstance(user_exclusions, list):
            raise BuildError("data/approved-exclusions.yaml exclusions must be a list")
        self.approved_exclusions_sha256 = sha256_file(exclusions_path) if exclusions_path.exists() else hashlib.sha256(b"exclusions: []\n").hexdigest()
        try:
            self.source_approval_ledger, self.source_approvals_sha256 = load_source_approvals(self.root / "config/source-approvals.yaml")
        except ApprovalError as exc:
            raise BuildError(str(exc)) from exc
        self.source_approval_enforced = (self.root / "config/source-approvals.yaml").is_file()
        self.code_approved_exclusions = self.source_approval_ledger.get("approved_exclusions", [])
        self.code_unsupported_dispositions = self.source_approval_ledger.get("unsupported_dispositions", [])
        dispositions_path = self.root / "data/unsupported-dispositions.yaml"
        dispositions_doc = load_yaml(dispositions_path) if dispositions_path.exists() else {"dispositions": []}
        user_dispositions = dispositions_doc.get("dispositions", []) if isinstance(dispositions_doc, dict) and set(dispositions_doc) == {"dispositions"} else None
        try:
            self.user_unsupported_dispositions = validate_rules_data_dispositions(self.root, user_dispositions)
        except ApprovalError as exc:
            raise BuildError(str(exc)) from exc
        self.unsupported_dispositions_sha256 = approval_scope_sha256(user_dispositions)
        self.unsupported_dispositions = [*self.code_unsupported_dispositions, *self.user_unsupported_dispositions]
        self.user_approved_exclusions = user_exclusions
        self.approved_exclusions = [*self.code_approved_exclusions, *user_exclusions]
        self.source_profile_observations: dict[str, Any] = {}
        self.source_approval_blockers = self._validate_source_profiles()
        self.source_license_materials, self.source_license_blockers = self._source_license_materials()
        review_path = self.root / "data/shared-domain-reviews.yaml"
        review_data = load_yaml(review_path) if review_path.exists() else {"reviews": []}
        self.shared_domain_reviews = review_data.get("reviews", [])
        self.shared_domain_reviews_sha256 = sha256_file(review_path) if review_path.exists() else hashlib.sha256(b"reviews: []\n").hexdigest()
        if not isinstance(self.shared_domain_reviews, list):
            raise BuildError("data/shared-domain-reviews.yaml reviews must be a list")
        if "blackmatrix7" in self.source_trees:
            archive = self.root / self.sources_config.get("archive", ".cache/missing-source.tar.gz")
            expected_archive_hash = self.sources_config.get("archive_sha256")
            if archive.is_file() and expected_archive_hash and sha256_file(archive) != expected_archive_hash:
                raise BuildError(f"locked source archive checksum mismatch: {archive}")
        primary_tree = self.source_trees.get(self.primary_source_id, {})
        self.source_archive_sha256 = primary_tree.get("archive_sha256")
        if self.primary_source_id == "blackmatrix7" and self.source_archive_sha256 is None:
            revision_archive = self.source_root.parent / f"ios_rule_script-{self.source_revision}.tar.gz" if self.source_revision else Path("/nonexistent")
            self.source_archive_sha256 = sha256_file(revision_archive) if revision_archive.is_file() else (expected_archive_hash if self.source_revision == self.sources_config["revision"] else None)

    def _validate_source_profiles(self) -> list[dict[str, Any]]:
        if not self.source_approval_enforced:
            return []
        from .sources import file_legal_declarations, v2fly_include_scope

        profiles = {item["category"]: item for item in self.source_approval_ledger.get("source_profiles", [])}
        for category, profile in profiles.items():
            if category not in self.categories:
                raise BuildError(f"source approval profile references unknown category {category!r}")
            for ref in profile["selected_sources"]:
                source_id = ref["source_id"]
                if source_id not in self.sources_config_all:
                    raise BuildError(f"source approval profile references unknown source {source_id!r}")
                reference = ref.get("key", ref.get("file"))
                if not isinstance(reference, str) or Path(reference).is_absolute() or ".." in Path(reference).parts or any(char in reference for char in ",\r\n"):
                    raise BuildError(f"source approval profile contains an unsafe key/file for {category}")

        def require_profile_scope(record: dict[str, Any], label: str) -> None:
            category = record["category"]
            if category not in self.categories:
                raise BuildError(f"{label} references unknown category {category!r}")
            if record["source_id"] not in self.sources_config_all:
                raise BuildError(f"{label} references unknown source {record['source_id']!r}")
            profile = profiles.get(category)
            if profile is None or not any(
                ref["source_id"] == record["source_id"]
                and ref.get("key", ref.get("file")) == record["source_key"]
                for ref in profile["selected_sources"]
            ):
                raise BuildError(f"{label} references an unselected source key for {category}")
            source_file = record["source_file"]
            if not isinstance(source_file, str) or Path(source_file).is_absolute() or ".." in Path(source_file).parts or any(char in source_file for char in ",\r\n"):
                raise BuildError(f"{label} contains an unsafe source file")

        for exclusion in self.code_approved_exclusions:
            require_profile_scope(exclusion, "source-approved exclusion")
        for disposition in self.unsupported_dispositions:
            require_profile_scope(disposition, "unsupported disposition")
        blockers = []
        for category, cfg in self.categories.items():
            if not cfg.get("enabled"):
                continue
            refs = cfg.get("source_refs")
            if refs is None and cfg.get("source_file"):
                blackmatrix = self.sources_config_all.get("blackmatrix7", {})
                refs = [{"source_id": "blackmatrix7", "file": f"{blackmatrix.get('rules_path', '')}/{cfg['source_file']}".strip("/"), "format": cfg.get("format", "surge")}]
            if refs is None:
                continue
            profile = profiles.get(category)
            if profile is None:
                blockers.append({"reason": "enabled source has no reviewed source profile", "category": category})
                self.source_profile_observations[category] = {"category": category, "status": "missing_profile", "selected_sources": refs}
                continue
            expected = sorted((ref["source_id"], ref["file"], ref["format"]) for ref in refs)
            actual = sorted((ref["source_id"], ref.get("key", ref.get("file")), ref["format"]) for ref in profile["selected_sources"])
            if expected != actual:
                raise BuildError(f"source approval profile does not match selected refs for category {category}")
            if profile.get("review_status") != "approved":
                blockers.append({"reason": "enabled source profile requires review", "category": category, "profile_id": profile["profile_id"], "review_status": profile.get("review_status")})
            approved_shared_scope = profile.get("shared_scope_sha256")
            shared_scope = shared_scope_for_category(self.source_approval_ledger, category, self.shared_domains)
            current_shared_scope = approval_scope_sha256(shared_scope)
            if approved_shared_scope != current_shared_scope:
                blockers.append({"reason": "shared infrastructure approval scope changed", "category": category, "profile_id": profile["profile_id"]})
            ref_observations = []
            for ref in profile["selected_sources"]:
                source_id = ref["source_id"]
                source_config = self.sources_config_all[source_id]
                configured_version = int(source_config.get("adapter_version", 1))
                if int(ref.get("adapter_version", configured_version)) != configured_version:
                    blockers.append({"reason": "source adapter version requires review", "category": category, "source_id": source_id, "reference": ref.get("key", ref.get("file"))})
                if source_id in self.source_trees:
                    source_root = Path(self.source_trees[source_id]["root"])
                    selected_key = ref.get("key", ref.get("file"))
                    if source_config.get("format") == "v2fly-domain-list":
                        closure, selectors = v2fly_include_scope(source_root, selected_key)
                    else:
                        if source_config.get("kind") == "local":
                            prefix = source_config.get("path", "")
                            closure = [f"{prefix}/{selected_key}".strip("/")]
                        elif source_id == "blackmatrix7" and not selected_key.startswith(f"{source_config.get('rules_path', '')}/"):
                            closure = [f"{source_config.get('rules_path', '')}/{selected_key}".strip("/")]
                        else:
                            closure = [selected_key]
                        selectors = []
                    observed_selector_hash = approval_scope_sha256(selectors)
                    if ref.get("include_selector_sha256") != observed_selector_hash:
                        blockers.append({"reason": "source include selectors require review", "category": category, "source_id": source_id, "key": selected_key, "approved_selector_sha256": ref.get("include_selector_sha256"), "observed_selector_sha256": observed_selector_hash})
                    observed_declarations = file_legal_declarations(source_root, closure)
                    if ref.get("file_declarations") != observed_declarations:
                        blockers.append({"reason": "selected source legal declarations require review", "category": category, "source_id": source_id, "key": selected_key, "approved_declarations": ref.get("file_declarations"), "observed_declarations": observed_declarations})
                    ref_observations.append({"source_id": source_id, "reference": selected_key, "format": ref.get("format"), "adapter_version": configured_version, "closure_files": closure, "include_selectors": selectors, "include_selector_sha256": observed_selector_hash, "file_declarations": observed_declarations, "approved_include_selector_sha256": ref.get("include_selector_sha256"), "approved_file_declarations": ref.get("file_declarations")})
                    # The file hashes in the ledger are provenance only. Ordinary
                    # valid rule additions/removals inherit review; selector edges
                    # above are the boundary that requires renewed approval.
            self.source_profile_observations[category] = {"category": category, "profile_id": profile["profile_id"], "status": profile.get("review_status"), "reviewer": profile.get("reviewer"), "reviewed_at": profile.get("reviewed_at"), "coverage_status": profile.get("coverage_status"), "coverage_notes": profile.get("coverage_notes", []), "evidence": profile.get("evidence", []), "selected_sources": ref_observations, "shared_scope": shared_scope, "shared_scope_sha256": current_shared_scope, "approved_shared_scope_sha256": approved_shared_scope, "merge_policy": profile.get("merge_policy")}
        return blockers

    def _source_license_materials(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        selected_source_ids_set: set[str] = set()
        for cfg in self.categories.values():
            if not isinstance(cfg, dict) or not cfg.get("enabled"):
                continue
            refs = cfg.get("source_refs")
            if refs is not None:
                selected_source_ids_set.update(ref["source_id"] for ref in refs if isinstance(ref, dict) and ref.get("source_id") in self.source_trees)
            elif cfg.get("source_file") and "blackmatrix7" in self.source_trees:
                selected_source_ids_set.add("blackmatrix7")
        selected_source_ids = sorted(selected_source_ids_set)
        records = self.source_approval_ledger.get("sources", {})
        if not isinstance(records, dict):
            raise BuildError("source approval ledger sources must be a mapping")
        materials: dict[str, dict[str, Any]] = {}
        blockers: list[dict[str, Any]] = []
        project_licenses = (self.root / "licenses").resolve()
        for source_id in selected_source_ids:
            config = self.sources_config_all[source_id]
            approval = records.get(source_id)
            row: dict[str, Any] = {"source_id": source_id, "status": "approved"}
            if not isinstance(approval, dict):
                blockers.append({"reason": "source license profile is not approved", "source_id": source_id, "detail": "missing ledger entry"})
                materials[source_id] = {**row, "status": "missing_approval"}
                continue
            license_relative = config.get("license_file")
            approved_license_relative = approval.get("license_file")
            if not isinstance(license_relative, str) or approved_license_relative != license_relative:
                blockers.append({"reason": "source license copy path differs from approval", "source_id": source_id})
                materials[source_id] = {**row, "status": "license_path_mismatch"}
                continue
            license_copy = _safe_license_copy(self.root, license_relative, project_licenses)
            if license_copy is None:
                blockers.append({"reason": "source license copy is missing or unsafe", "source_id": source_id})
                materials[source_id] = {**row, "status": "missing_license_copy"}
                continue
            copy_hash = sha256_file(license_copy)
            approved_hash = approval.get("license_sha256")
            if not isinstance(approved_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", approved_hash):
                raise BuildError(f"source license approval hash is malformed: {source_id}")
            source_root = Path(self.source_trees[source_id]["root"]).resolve()
            local_source = config.get("kind") == "local"
            actual_license = license_copy if local_source else source_root / "LICENSE"
            actual_hash = sha256_file(actual_license) if actual_license.is_file() and not actual_license.is_symlink() else None
            if actual_hash is None:
                blockers.append({"reason": "actual source LICENSE is missing or unsafe", "source_id": source_id})
            if copy_hash != approved_hash or actual_hash != approved_hash:
                blockers.append({"reason": "source license text changed and requires review", "source_id": source_id, "approved_sha256": approved_hash, "copy_sha256": copy_hash, "source_sha256": actual_hash})
                row["status"] = "license_changed"
            notice_relative = approval.get("notice_file")
            notice_hash = approval.get("notice_sha256")
            notice_copy = None
            actual_notice = None
            actual_notice_hash = None
            if local_source and notice_relative:
                notice_copy = _safe_license_copy(self.root, notice_relative, project_licenses)
                if notice_copy is None:
                    blockers.append({"reason": "source notice copy is missing or unsafe", "source_id": source_id})
                    notice_copy = None
                else:
                    actual_notice_hash = sha256_file(notice_copy)
            elif not local_source:
                notice_source = next((source_root / name for name in ("NOTICE", "NOTICE.txt", "NOTICE.md") if (source_root / name).is_file() and not (source_root / name).is_symlink()), None)
                if notice_source is not None:
                    actual_notice = notice_source
                    if not isinstance(notice_relative, str):
                        blockers.append({"reason": "upstream NOTICE has no approved copy", "source_id": source_id})
                    else:
                        notice_copy = _safe_license_copy(self.root, notice_relative, project_licenses)
                        if notice_copy is None:
                            blockers.append({"reason": "source notice copy is missing or unsafe", "source_id": source_id})
                            notice_copy = None
                        else:
                            actual_notice_hash = sha256_file(notice_source)
                            if actual_notice_hash != sha256_file(notice_copy):
                                blockers.append({"reason": "upstream NOTICE text changed and requires review", "source_id": source_id})
                elif notice_relative or notice_hash:
                    blockers.append({"reason": "approved upstream NOTICE disappeared and requires review", "source_id": source_id, "approved_notice_sha256": notice_hash})
                    if isinstance(notice_relative, str):
                        candidate_copy = _safe_license_copy(self.root, notice_relative, project_licenses)
                        if candidate_copy is None:
                            blockers.append({"reason": "source NOTICE approval copy is missing or unsafe", "source_id": source_id})
                        else:
                            notice_copy = candidate_copy
                            if sha256_file(notice_copy) != notice_hash:
                                blockers.append({"reason": "source NOTICE copy changed and requires review", "source_id": source_id})
            if notice_copy is not None and (not isinstance(notice_hash, str) or actual_notice_hash != notice_hash or sha256_file(notice_copy) != notice_hash):
                blockers.append({"reason": "source notice text changed and requires review", "source_id": source_id})
            bundled_license = actual_license if actual_hash else license_copy
            bundled_notice = actual_notice or notice_copy
            row.update({"license": approval.get("license"), "license_file": license_relative, "license_sha256": actual_hash, "license_copy_sha256": copy_hash, "license_bundled_sha256": sha256_file(bundled_license), "approved_license_sha256": approved_hash, "notice_file": notice_relative, "notice_sha256": actual_notice_hash, "notice_copy_sha256": sha256_file(notice_copy) if notice_copy else None, "notice_bundled_sha256": sha256_file(bundled_notice) if bundled_notice else None, "approved_notice_sha256": notice_hash, "license_scope": approval.get("license_scope"), "evidence": approval.get("evidence", []), "status": row.get("status", "approved")})
            materials[source_id] = {**row, "license_path": bundled_license, "notice_path": bundled_notice}
        return materials, blockers

    def inventory(self) -> dict[str, Any]:
        if "blackmatrix7" not in self.source_trees:
            sources = []
            refs_by_source: dict[str, list[dict[str, str]]] = {}
            for category, cfg in self.categories.items():
                if not cfg.get("enabled"):
                    continue
                refs = cfg.get("source_refs", [])
                for ref in refs:
                    refs_by_source.setdefault(ref["source_id"], []).append({"category": category, "file": ref["file"], "format": ref["format"]})
            for source_id, refs in sorted(refs_by_source.items()):
                tree = self.source_trees.get(source_id)
                if tree is None:
                    sources.append({"source_id": source_id, "status": "snapshot_missing", "selected_refs": refs})
                    continue
                config = self.sources_config_all[source_id]
                root = Path(tree["root"])
                if config.get("format") == "v2fly-domain-list":
                    files = sorted(path.relative_to(root).as_posix() for path in (root / "data").rglob("*") if path.is_file())
                elif config.get("kind") == "local":
                    files = sorted(path.relative_to(root).as_posix() for path in root.joinpath(config["path"]).rglob("*") if path.is_file())
                else:
                    files = [ref["file"] for ref in refs]
                sources.append({"source_id": source_id, "repository": tree.get("repository", config.get("repository")), "revision": tree["revision"], "archive_sha256": tree.get("archive_sha256"), "file_count": len(files), "selected_refs": sorted(refs, key=lambda ref: (ref["category"], ref["file"])), "files": files})
            return {"source_revision": self.source_revision, "primary_source_id": next((source["source_id"] for source in sources if source.get("status") != "snapshot_missing"), None), "sources": sources}
        base = self.source_root / self.sources_config["rules_path"]
        if not base.is_dir():
            raise BuildError(f"source tree missing: {base} (extract the locked archive first)")
        entries = []
        for directory in sorted((x for x in base.iterdir() if x.is_dir()), key=lambda p: p.name.casefold()):
            readme = directory / "README.md"
            readme_date = next((line.split("：", 1)[1].strip() for line in readme.read_text(errors="replace").splitlines() if line.startswith("最后更新时间：")), None) if readme.exists() else None
            files = []
            for path in sorted(directory.glob("*.list")):
                header = next((line.split(":", 1)[1].strip() for line in path.read_text(errors="replace").splitlines()[:10] if line.startswith("# UPDATED:")), None)
                lines = [x.strip() for x in path.read_text(errors="replace").splitlines() if x.strip() and not x.lstrip().startswith("#")]
                files.append({"name": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size, "rule_count": len(lines), "updated_header": header})
            entries.append({"category_dir": directory.name, "readme_updated": readme_date, "files": files})
        return {"repository": self.sources_config["repository"], "revision": self.source_revision, "category_directory_count": len(entries), "categories": entries}

    def _load_rules(self) -> tuple[list[Rule], list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
        rules: list[Rule] = []
        unsupported: list[dict[str, Any]] = []
        source_hashes: dict[str, str] = {}
        categories_report: list[dict[str, Any]] = []
        active: list[tuple[str, dict[str, Any]]] = []
        for category, cfg in self.categories.items():
            if not isinstance(category, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category):
                raise BuildError(f"invalid category ID: {category!r}")
            if not isinstance(cfg, dict) or not isinstance(cfg.get("enabled"), bool):
                raise BuildError(f"category {category}: enabled must be a boolean")
            for field in ("provider_name", "policy_group"):
                if not isinstance(cfg.get(field), str) or not IDENTIFIER_RE.fullmatch(cfg[field]):
                    raise BuildError(f"category {category}: unsafe or missing {field}")
            source_file = cfg.get("source_file")
            if source_file is not None and (not isinstance(source_file, str) or Path(source_file).is_absolute() or ".." in Path(source_file).parts or any(ch in source_file for ch in ",\r\n")):
                raise BuildError(f"category {category}: unsafe source_file")
            refs = cfg.get("source_refs")
            if refs is not None and (not isinstance(refs, list) or not refs):
                raise BuildError(f"category {category}: source_refs must be a non-empty list")
            if refs is not None and len(refs) > 1 and cfg.get("source_merge_review") != "approved":
                raise BuildError(f"category {category}: multiple source_refs require explicit source_merge_review: approved")
            if cfg["enabled"]:
                if category != "foreign_general" and source_file is None and refs is None:
                    raise BuildError(f"category {category}: enabled source_file or source_refs is required")
                active.append((category, cfg))
        provider_names = [cfg["provider_name"].casefold() for _, cfg in active]
        generated_names = provider_names + [f"customexact-{cfg['provider_name']}".casefold() for _, cfg in active]
        if len(set(generated_names)) != len(generated_names):
            raise BuildError("enabled categories or generated CustomExact providers have duplicate names")
        for category, cfg in active:
            policy = cfg.get("policy_group")
            if policy not in self.policies:
                raise BuildError(f"category {category}: unknown logical policy_group {policy!r}")
            if not isinstance(cfg.get("priority"), int):
                raise BuildError(f"category {category}: priority must be an integer")
            if cfg.get("policy_group") not in self.policies:
                raise BuildError(f"category {category}: unknown logical policy_group {cfg.get('policy_group')!r}")
            logical_paths = []
            source_refs = cfg.get("source_refs")
            if source_refs is None and cfg.get("source_file"):
                source_refs = [{"source_id": "blackmatrix7", "file": f"{self.sources_config['rules_path']}/{cfg['source_file']}", "format": cfg.get("format", "surge")}]
            for ref in source_refs or []:
                if not isinstance(ref, dict) or set(ref) - {"source_id", "file", "format"} or not all(isinstance(ref.get(key), str) and ref[key] for key in ("source_id", "file", "format")):
                    raise BuildError(f"category {category}: invalid source reference")
                source_id, source_file_ref, source_format = ref["source_id"], ref["file"], ref["format"]
                if source_id not in self.sources_config_all or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source_id):
                    raise BuildError(f"category {category}: unknown source {source_id!r}")
                if self.source_trees:
                    tree = self.source_trees.get(source_id)
                    if tree is None:
                        raise BuildError(f"category {category}: snapshot missing source {source_id}")
                    source_root = Path(tree["root"])
                    revision = tree["revision"]
                else:
                    if source_id != "blackmatrix7":
                        raise BuildError(f"source snapshot missing {source_id}")
                    source_root = self.source_root
                    revision = self.source_revision
                parse_file = source_file_ref
                if self.sources_config_all[source_id].get("kind") == "local":
                    parse_file = f"{self.sources_config_all[source_id].get('path', '')}/{source_file_ref}"
                from .sources import parse_source
                parsed_rows, hashes, unsupported_rows = parse_source(source_id, source_root, parse_file, {**self.sources_config_all[source_id], "format": source_format})
                explicit_refs = cfg.get("source_refs") is not None
                logical_paths.extend((f"{source_id}:{path}" if explicit_refs or source_id != "blackmatrix7" else path) for path in hashes)
                source_hashes.update({(f"{source_id}:{path}" if explicit_refs or source_id != "blackmatrix7" else path): digest for path, digest in hashes.items()})
                source_key = source_file_ref if self.sources_config_all[source_id].get("format") == "v2fly-domain-list" else None
                for row in parsed_rows:
                    rules.append(Rule(row["domain"], row["match_type"], category, cfg["provider_name"], policy, int(cfg["priority"]), source_id, row["source_file"], revision, row["line"], False, tuple(row.get("attributes", [])), tuple(row.get("include_chain", [])), tuple(row.get("affiliations", [])), source_key, raw_sha256(row.get("raw", ""))))
                for row in unsupported_rows:
                    unsupported.append({"category": category, "source": source_id, "source_id": source_id, "source_key": source_key, "source_revision": revision, "raw_sha256": raw_sha256(row.get("raw", "")), "approval_status": "requires-review", **row})
            category_sources: Any = logical_paths or None
            if cfg.get("source_refs") is None and logical_paths:
                category_sources = logical_paths[0]
            catalog_service_id = cfg.get("catalog_service_id")
            catalog_spec = self.service_catalog.get(catalog_service_id, {}) if catalog_service_id else {}
            if catalog_service_id and not catalog_spec:
                raise BuildError(f"category {category}: unknown service catalog entry {catalog_service_id!r}")
            categories_report.append({
                "category": category,
                "provider_name": cfg["provider_name"],
                "policy_group": policy,
                "priority": cfg["priority"],
                "source_file": category_sources,
                "rule_count": len([r for r in rules if r.category == category]),
                "unsupported_count": len([u for u in unsupported if u["category"] == category]),
                "catalog_service_id": catalog_service_id,
                "catalog_review_state": catalog_spec.get("review_state"),
                "catalog_route_intent": catalog_spec.get("route_intent"),
                "legacy_review_required": bool(cfg.get("legacy_review_required")),
                "status": "partial" if any(u["category"] == category for u in unsupported) else "complete",
            })

        self.loaded_upstream_rule_count = sum(not rule.custom for rule in rules)
        # Main-branch source approvals are always merged with data-branch
        # additions. The older data-only empty list therefore cannot clear the
        # reviewed baseline. Source signatures never use line or revision.
        self.exclusion_report = []
        entries = [(item, "code-baseline") for item in self.code_approved_exclusions] + [(item, "rules-data") for item in self.user_approved_exclusions]
        scope_owners: dict[tuple[str, str, str], list[int]] = {}
        for index, (item, _origin) in enumerate(entries):
            if isinstance(item, dict):
                scope_owners.setdefault((str(item.get("category", "")), str(item.get("domain", "")), str(item.get("match_type", ""))), []).append(index)
        conflicts = {index for indices in scope_owners.values() if len(indices) > 1 for index in indices}
        removed_ids: set[int] = set()
        for index, (item, origin) in enumerate(entries, 1):
            row = {"index": index, "origin": origin, "exclusion": item, "status": "invalid", "excluded_sources": []}
            if index - 1 in conflicts:
                row["status"] = "conflict"
                row["reason"] = "duplicate or conflicting code/data exclusion for the same category/domain/type"
                self.exclusion_report.append(row)
                continue
            if origin == "code-baseline":
                required = ("category", "domain", "match_type", "source_id", "source_key", "source_file", "raw_sha256", "reason", "reviewer", "reviewed_at")
                valid_shape = isinstance(item, dict) and all(isinstance(item.get(field), str) and item[field].strip() for field in required)
                valid_shape = valid_shape and item.get("match_type") in {"EXACT", "SUFFIX"} and bool(re.fullmatch(r"[0-9a-f]{64}", item.get("raw_sha256", ""))) and item.get("review_status") == "approved"
                if not valid_shape:
                    row["reason"] = "source approval signature or review metadata is invalid"
                    self.exclusion_report.append(row)
                    continue
                try:
                    valid_shape = item["category"] in self.categories and canonical_domain(item["domain"]) == item["domain"]
                except (TypeError, ValueError):
                    valid_shape = False
                if not valid_shape:
                    row["reason"] = "source approval category or canonical domain is invalid"
                    self.exclusion_report.append(row)
                    continue
                exact = [rule for rule in rules if not rule.custom and rule.category == item["category"] and rule.source == item["source_id"] and rule.source_key == item["source_key"] and rule.source_file == item["source_file"] and rule.domain == item["domain"] and rule.match_type == item["match_type"] and rule.raw_sha256 == item["raw_sha256"]]
                stale = [rule for rule in rules if not rule.custom and rule.category == item["category"] and rule.source == item["source_id"] and rule.source_key == item["source_key"] and rule.source_file == item["source_file"] and rule.domain == item["domain"] and rule.match_type == item["match_type"] and rule.raw_sha256 != item["raw_sha256"]]
                if len(exact) == 1:
                    matched = exact
                elif len(exact) > 1:
                    row["reason"] = "source approval signature ambiguously matches multiple source rows"
                    self.exclusion_report.append(row)
                    continue
                elif stale:
                    row["status"] = "stale"
                    row["reason"] = "source row changed under an approved exclusion signature; review again"
                    self.exclusion_report.append(row)
                    continue
                else:
                    row["status"] = "inactive"
                    row["reason"] = "approved source row is absent from the selected snapshot"
                    self.exclusion_report.append(row)
                    continue
            else:
                if not isinstance(item, dict) or set(item) != {"category", "domain", "match_type", "reviewer", "reviewed_at", "reason"}:
                    row["reason"] = "user exclusion requires exactly category/domain/match_type/reviewer/reviewed_at/reason"
                    self.exclusion_report.append(row)
                    continue
                category, raw_domain, match_type = item.get("category"), item.get("domain"), item.get("match_type")
                try:
                    domain = canonical_domain(raw_domain) if isinstance(raw_domain, str) else None
                except ValueError:
                    domain = None
                review_fields_valid = all(isinstance(item.get(field), str) and item[field].strip() for field in ("reviewer", "reviewed_at", "reason"))
                if not isinstance(category, str) or category not in self.categories or domain != raw_domain or match_type not in {"EXACT", "SUFFIX"} or not review_fields_valid:
                    row["reason"] = "invalid category, canonical domain, match type, or review metadata"
                    self.exclusion_report.append(row)
                    continue
                matched = [rule for rule in rules if not rule.custom and rule.category == category and rule.domain == domain and rule.match_type == match_type]
                if not matched:
                    row["status"] = "unmatched"
                    row["reason"] = "no exact upstream rule matched"
                    self.exclusion_report.append(row)
                    continue
            row["status"] = "applied"
            row["excluded_sources"] = [asdict(rule) for rule in matched]
            self.exclusion_report.append(row)
            removed_ids.update(id(rule) for rule in matched)
        rules = [rule for rule in rules if id(rule) not in removed_ids]

        disposition_index: dict[tuple[str, ...], dict[str, Any]] = {}
        for disposition in self.unsupported_dispositions:
            signature = unsupported_signature(disposition)
            if signature in disposition_index:
                raise BuildError("duplicate unsupported approval signature")
            disposition_index[signature] = disposition
        used_dispositions: set[tuple[str, ...]] = set()
        for item in unsupported:
            signature = unsupported_signature(item)
            disposition = disposition_index.get(signature)
            if disposition is not None:
                item["approval_status"] = disposition["disposition"]
                item["approval_reviewed_at"] = disposition["reviewed_at"]
                used_dispositions.add(signature)
            else:
                item["approval_status"] = "requires-review"
        self.inactive_unsupported_dispositions = [item for signature, item in disposition_index.items() if signature not in used_dispositions]
        code_signatures = {unsupported_signature(item) for item in self.code_unsupported_dispositions}
        observed_signatures = {unsupported_signature(item) for item in unsupported}
        self.unsupported_disposition_report = [
            {"origin": "code-baseline" if unsupported_signature(item) in code_signatures else "rules-data",
             "status": "applied" if unsupported_signature(item) in observed_signatures else "inactive",
             "signature": {key: item[key] for key in ("category", "source_id", "source_key", "source_file", "type", "raw_sha256", "reason")},
             "disposition": item["disposition"], "reviewer": item["reviewer"], "reviewed_at": item["reviewed_at"]}
            for item in self.unsupported_dispositions
        ]

        for index, item in enumerate(self.custom_data.get("rules", []), 1):
            if not isinstance(item, dict):
                raise BuildError(f"custom rule {index}: expected mapping")
            if "enabled" in item and not isinstance(item["enabled"], bool):
                raise BuildError(f"custom rule {index}: enabled must be a boolean")
            if not item.get("enabled", True):
                continue
            category = item.get("category", "foreign_general")
            cfg = self.categories.get(category)
            if cfg is None or not cfg.get("enabled"):
                raise BuildError(f"custom rule {index}: category {category!r} is unknown or disabled")
            kind = str(item.get("match_type", "SUFFIX")).upper()
            if kind not in {"EXACT", "SUFFIX"}:
                raise BuildError(f"custom rule {index}: match_type must be EXACT or SUFFIX")
            try:
                domain = canonical_domain(str(item.get("domain", "")))
            except ValueError as exc:
                raise BuildError(f"custom rule {index}: {exc}") from exc
            rules.append(Rule(domain, kind, category, cfg["provider_name"], cfg["policy_group"], int(cfg["priority"]), "custom", "data/custom-rules.yaml", self.custom_revision, index, True))

        # Keep duplicate provenance and custom status in the model; renderers
        # deduplicate output syntax without losing source evidence.
        # Refresh counts after parsing and custom additions.
        for row in categories_report:
            row["rule_count"] = sum(r.category == row["category"] for r in rules)
            row["unsupported_count"] = sum(u["category"] == row["category"] for u in unsupported)
        return rules, unsupported, source_hashes, categories_report

    def build(self, output_root: Path | None = None) -> BuildResult:
        output_root = (output_root or (self.root / "data")).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        with (output_root / ".build.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._build_locked(output_root)

    def _build_locked(self, output_root: Path) -> BuildResult:
        rules, unsupported, source_hashes, categories_report = self._load_rules()
        categories_by_id = self.categories
        upstream_rule_count = self.loaded_upstream_rule_count
        custom_rule_count = sum(rule.custom for rule in rules)
        shared_review_by_id = {}
        for review in self.shared_domain_reviews:
            if not isinstance(review, dict) or not isinstance(review.get("id"), str) or review["id"] in shared_review_by_id or review.get("status") != "approved" or not all(isinstance(review.get(key), str) and review[key].strip() for key in ("reviewed_by", "reviewed_at", "rationale")):
                raise BuildError("shared-domain review entries must be unique, approved, and include reviewer, date, and rationale")
            shared_review_by_id[review["id"]] = review
        shared_domain_report = []
        for item in self.shared_domains:
            if not isinstance(item, dict) or set(item) != {"id", "domain", "match_type", "expected_categories", "risk"} or not isinstance(item.get("id"), str):
                raise BuildError("invalid config/shared-domains.yaml entry")
            if item["match_type"] not in {"EXACT", "SUFFIX"}:
                raise BuildError(f"shared-domain entry {item['id']}: invalid match_type")
            try:
                domain = canonical_domain(item["domain"])
            except (TypeError, ValueError) as exc:
                raise BuildError(f"shared-domain entry {item['id']}: invalid domain") from exc
            evidence_rule = Rule(domain, item["match_type"], "_shared_review", "_shared_review", "_shared_review", 0, "review", "config/shared-domains.yaml", "config", 0)
            matched = [rule for rule in rules if _overlaps(evidence_rule, rule)]
            review = shared_review_by_id.get(item["id"])
            if not matched and upstream_rule_count == 0:
                continue
            shared_domain_report.append({"id": item["id"], "domain": domain, "match_type": item["match_type"], "expected_categories": item["expected_categories"], "matched_categories": sorted({rule.category for rule in matched}), "evidence": [asdict(rule) for rule in matched], "risk": item["risk"], "review_status": "approved" if review else "pending", "review": review, "active": bool(matched)})
        configured_review_ids = {item["id"] for item in self.shared_domains if isinstance(item, dict) and isinstance(item.get("id"), str)}
        if set(shared_review_by_id) - configured_review_ids:
            raise BuildError("shared-domain review references an unknown configured review ID")
        unresolved_shared_reviews = [item for item in shared_domain_report if item["active"] and item["review_status"] != "approved"]
        shared_baseline = self.source_approval_ledger.get("shared_scope_baseline", {})
        shared_roots = shared_baseline.get("recognized_shared_roots", []) if isinstance(shared_baseline, dict) else []
        reviewed_additions = shared_baseline.get("service_exact_additions", {}) if isinstance(shared_baseline, dict) else {}
        shared_rule_scope: list[dict[str, Any]] = []
        for rule in rules:
            if rule.custom:
                continue
            matching_roots = [root for root in shared_roots if rule.domain == root or rule.domain.endswith("." + root)]
            if not matching_roots:
                continue
            reviewed_exact = rule.match_type == "EXACT" and rule.domain in reviewed_additions.get(rule.category, [])
            if reviewed_exact:
                continue
            shared_rule_scope.append({"category": rule.category, "source_id": rule.source, "source_key": rule.source_key, "source_file": rule.source_file, "domain": rule.domain, "match_type": rule.match_type, "shared_roots": sorted(matching_roots), "raw_sha256": rule.raw_sha256})
        shared_rule_scope_blockers = []
        if shared_rule_scope:
            shared_rule_scope_blockers.append({"reason": "new or expanded shared infrastructure rule scope requires review", "rules": shared_rule_scope})
        for category, observation in self.source_profile_observations.items():
            category_rules = [row for row in shared_rule_scope if row["category"] == category]
            observation["observed_shared_domain_rules"] = category_rules
            observation["shared_domain_scope_status"] = "review_required" if category_rules else "within_reviewed_scope"
        direct_conflicts = []
        for raw_domain in self.custom_data.get("force_direct", []):
            try:
                domain = canonical_domain(str(raw_domain))
            except ValueError as exc:
                raise BuildError(f"force_direct entry invalid: {raw_domain!r}: {exc}") from exc
            direct_scope = Rule(domain, "SUFFIX", "force_direct", "force_direct", "DIRECT", -1, "custom", "data/custom-rules.yaml", self.custom_revision, 0)
            covering = [r for r in rules if _overlaps(r, direct_scope)]
            if covering:
                direct_conflicts.append({"domain": domain, "covered_by": [asdict(r) for r in covering], "reason": "SmartDNS set has no verified direct-exclusion behavior"})

        normalized_rules = sorted((asdict(r) for r in rules), key=lambda x: (not x["custom"] and x["match_type"] == "EXACT", x["priority"], x["category"], x["domain"], x["match_type"], x["source_file"], x["line"]))
        from .provider_plan import compile_provider_plan
        provider_plan = compile_provider_plan(normalized_rules, self.categories)
        conflicts = provider_plan["conflicts"]
        provider_order_cycles = provider_plan["cycles"]
        provider_map = provider_plan["providers"]
        mihomo_rule_order = provider_plan["order"]
        for provider in provider_map:
            provider["path"] = f"mihomo/providers/{provider['name']}.yaml"
        code_identity = {str(p.relative_to(self.root)): sha256_file(p) for p in sorted((self.root / "src").rglob("*.py"))}
        config_identity = {str(p.relative_to(self.root)): sha256_file(p) for p in sorted((self.root / "config").glob("*.yaml"))}
        custom_path = self.root / "data/custom-rules.yaml"
        custom_hash = sha256_file(custom_path) if custom_path.exists() else hashlib.sha256(b"rules: []\nforce_direct: []\n").hexdigest()
        release_data_revision = self.data_revision or custom_hash
        settings = load_yaml(self.root / "config/settings.yaml")
        for flag in ("target_semantics_verified", "deployment_enabled", "license_release_approved"):
            if not isinstance(settings.get(flag, False), bool):
                raise BuildError(f"settings.{flag} must be a boolean")
        target_verified = settings.get("target_semantics_verified", False)
        deployment_enabled = settings.get("deployment_enabled", False)
        license_release_approved = settings.get("license_release_approved", False)
        empty_categories = [row["category"] for row in categories_report if row["rule_count"] == 0 and row["category"] != "foreign_general"]
        unresolved_conflicts = [c for c in conflicts if not c["resolved"]]
        sharp_drops = []
        current = output_root / "current"
        if current.exists():
            try:
                previous = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
                previous_counts = {row["category"]: row["rule_count"] for row in previous.get("category_status", [])}
            except (OSError, json.JSONDecodeError, TypeError, KeyError):
                previous_counts = {}
            else:
                for row in categories_report:
                    old, new = previous_counts.get(row["category"], 0), row["rule_count"]
                    if old > 0 and new < old * 0.7:
                        sharp_drops.append({"category": row["category"], "previous": old, "current": new, "drop_fraction": round((old - new) / old, 4)})
        legacy_unsupported_types = set(settings.get("approved_unsupported_types", [])) if not self.source_approval_enforced else set()
        unknown_unsupported = [item for item in unsupported if item.get("approval_status") not in {"report_only_nonrepresentable", "keep_rejected_in_report"} and item.get("type") not in legacy_unsupported_types]
        unresolved_exclusions = [row for row in self.exclusion_report if row["status"] not in {"applied", "inactive"}]
        catalog_review_blockers = []
        for category, cfg in self.categories.items():
            if not cfg.get("enabled") or not cfg.get("catalog_service_id"):
                continue
            catalog_spec = self.service_catalog.get(cfg["catalog_service_id"], {})
            route_intent = catalog_spec.get("route_intent")
            implementation = catalog_spec.get("implementation_status")
            if route_intent == "direct" or implementation in {"reference_only", "prohibited_aggregate", "manual_missing"}:
                catalog_review_blockers.append({"reason": "enabled catalog entry is direct-only or not runnable", "category": category, "service": cfg["catalog_service_id"], "route_intent": route_intent, "implementation_status": implementation})
            elif catalog_spec.get("review_state") != "approved":
                catalog_review_blockers.append({"reason": "enabled service catalog entry requires review", "category": category, "service": cfg["catalog_service_id"], "review_state": catalog_spec.get("review_state")})
        review_required = bool(unknown_unsupported or empty_categories or sharp_drops or unresolved_conflicts or provider_order_cycles or direct_conflicts or unresolved_shared_reviews or shared_rule_scope_blockers or unresolved_exclusions or catalog_review_blockers or self.source_approval_blockers or self.source_license_blockers)
        deployment_allowed = deployment_enabled and target_verified and not review_required and not unresolved_conflicts and not provider_order_cycles and not direct_conflicts and not unresolved_shared_reviews and not unresolved_exclusions
        review_blockers = ([{"reason": "unsupported input rules require review", "count": len(unknown_unsupported)}] if unknown_unsupported else []) + ([{"reason": "enabled category empty", "categories": empty_categories}] if empty_categories else []) + ([{"reason": "category decreased by more than 30 percent", "categories": sharp_drops}] if sharp_drops else [])
        deployment_blockers = review_blockers + catalog_review_blockers + self.source_approval_blockers + unresolved_conflicts + provider_order_cycles + direct_conflicts + shared_rule_scope_blockers + ([{"reason": "shared-domain review required", "entries": [item["id"] for item in unresolved_shared_reviews]}] if unresolved_shared_reviews else []) + ([{"reason": "approved exclusion invalid or unmatched", "entries": unresolved_exclusions}] if unresolved_exclusions else []) + ([] if target_verified else [{"reason": "target SmartDNS/Mihomo semantics not verified"}]) + ([] if deployment_enabled else [{"reason": "deployment master switch is disabled"}])
        publish_blockers = self.source_approval_blockers + self.source_license_blockers + shared_rule_scope_blockers
        if review_required:
            publish_blockers.append({"reason": "rule or source review is required before publication"})
        custom_only_smoke = self.build_purpose == "smoke" and upstream_rule_count == 0 and custom_rule_count > 0
        publish_allowed = not publish_blockers and (bool(self.source_trees) or custom_only_smoke)
        deployment_blockers.extend(self.source_license_blockers)
        decision = {"review_required": review_required, "deployment_allowed": deployment_allowed, "publish_allowed": publish_allowed, "target_semantics_verified": target_verified, "deployment_enabled": deployment_enabled, "deployment_blockers": deployment_blockers, "publish_blockers": publish_blockers, "source_shared_scope_blockers": shared_rule_scope_blockers}
        source_manifest = {}
        for source_id, tree in sorted(self.source_trees.items()):
            configured = self.sources_config_all[source_id]
            selected_refs = []
            for category, cfg in self.categories.items():
                if not cfg.get("enabled"):
                    continue
                if cfg.get("source_refs") is not None:
                    for ref in cfg["source_refs"]:
                        if isinstance(ref, dict) and ref.get("source_id") == source_id:
                            file_name = ref.get("file")
                            if configured.get("kind") == "local":
                                file_name = f"{configured.get('path', '')}/{file_name}"
                            selected_refs.append({"category": category, "file": file_name, "format": ref.get("format"), "reference_kind": "list-key" if configured.get("format") == "v2fly-domain-list" else "file"})
                elif source_id == "blackmatrix7" and cfg.get("source_file"):
                    selected_refs.append({"category": category, "file": f"{configured.get('rules_path', self.sources_config['rules_path'])}/{cfg['source_file']}", "format": cfg.get("format", "surge"), "reference_kind": "file"})
            file_hashes = {}
            for logical_path, digest in source_hashes.items():
                if logical_path.startswith(f"{source_id}:"):
                    file_hashes[logical_path.split(":", 1)[1]] = digest
                elif source_id == "blackmatrix7" and ":" not in logical_path:
                    file_hashes[logical_path] = digest
            source_manifest[source_id] = {
                "repository": tree.get("repository", configured.get("repository")),
                "revision": tree["revision"],
                "archive_sha256": tree.get("archive_sha256"),
                "source_date_utc": tree.get("source_date_utc"),
                "kind": tree.get("kind", "remote"),
                "adapter_version": int(configured.get("adapter_version", 1)),
                "selected_refs": sorted(selected_refs, key=lambda ref: (str(ref["category"]), str(ref["file"]))),
                "file_sha256": dict(sorted(file_hashes.items())),
            }
        identity_inputs = {"schema_version": 4, "source_capability_version": 1, "compiler_capability_version": 2, "license_capability_version": 1, "source_license_provenance": {key: {name: value for name, value in item.items() if name not in {"license_path", "notice_path"}} for key, item in self.source_license_materials.items()}, "source_license_blockers": self.source_license_blockers, "source_profile_observations": self.source_profile_observations, "provider_map": provider_map, "sources": source_manifest, "build_purpose": self.build_purpose, "builder_version": __version__, "code_commit": self.code_commit, "code_identity": code_identity, "config_identity": config_identity, "custom_rules_sha256": custom_hash, "source_approvals_sha256": self.source_approvals_sha256, "approved_exclusions_sha256": self.approved_exclusions_sha256, "approved_exclusions": self.approved_exclusions, "exclusion_report": self.exclusion_report, "unsupported_dispositions_sha256": self.unsupported_dispositions_sha256, "user_unsupported_dispositions": self.user_unsupported_dispositions, "unsupported_dispositions": self.unsupported_dispositions, "unsupported_disposition_report": self.unsupported_disposition_report, "shared_domain_reviews_sha256": self.shared_domain_reviews_sha256, "shared_domain_reviews": self.shared_domain_reviews, "data_revision": release_data_revision, "source_revision": self.source_revision, "source_archive_sha256": self.source_archive_sha256, "source_file_sha256": source_hashes, "normalized_rules": normalized_rules, "mihomo_rule_order": mihomo_rule_order, "decision": decision}

        # Bound into the release identity so a direct exception change cannot be
        # mistaken for an ordinary custom-domain update by the manager.
        identity_inputs["automatic_policy_inputs"] = {
            "force_direct": sorted(self.custom_data.get("force_direct", [])),
            "source_configuration": {key: {name: value for name, value in cfg.items()
                                          if name not in {"revision", "archive", "archive_sha256"}}
                                     for key, cfg in self.sources_config_all.items()},
        }

        staging_root = output_root / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix="candidate-", dir=staging_root))
        (temp / "smartdns").mkdir()
        (temp / "mihomo/providers").mkdir(parents=True)
        (temp / "reports").mkdir()
        (temp / "provenance/sources").mkdir(parents=True)
        for provider in provider_map:
            member_rows = [normalized_rules[index] for index in provider["member_indices"]]
            (temp / provider["path"]).write_text(_provider_yaml_from_rows(member_rows), encoding="utf-8")

        smartdns_rules = sorted({r.smartdns() for r in rules})
        (temp / "smartdns/proxy-domains.txt").write_text("".join(row + "\n" for row in smartdns_rules), encoding="utf-8")
        provider_lines = ["rule-providers:"]
        for provider in provider_map:
            provider_lines += [f"  {provider['name']}:", "    type: file", "    behavior: classical", "    format: yaml", f"    path: ./providers/{provider['name']}.yaml"]
        provider_text = "\n".join(provider_lines) + "\n" if provider_map else "rule-providers: {}\n"
        (temp / "mihomo/rule-providers.yaml").write_text(provider_text, encoding="utf-8")
        rule_lines = [f"RULE-SET,{provider['name']},{provider['policy_group']}" for provider in provider_map]
        rules_lines = ["rules:"] + [f"  - {json.dumps(line, ensure_ascii=False)}" for line in rule_lines]
        rules_text = "\n".join(rules_lines) + "\n" if rule_lines else "rules: []\n"
        (temp / "mihomo/rules.yaml").write_text(rules_text, encoding="utf-8")

        for row in categories_report:
            if row["rule_count"] == 0:
                row["status"] = "empty"
        statistics = {"category_count": sum(row["rule_count"] > 0 for row in categories_report), "enabled_category_count": len(categories_report), "rule_count": len(rules), "unique_semantic_rule_count": len({(r.domain, r.match_type) for r in rules}), "smartdns_entry_count": len(smartdns_rules), "unsupported_count": len(unsupported), "conflict_count": len(conflicts), "unresolved_conflict_count": sum(not x["resolved"] for x in conflicts), "force_direct_conflict_count": len(direct_conflicts)}
        _write_json(temp / "reports/statistics.json", statistics)
        _write_json(temp / "reports/missing-categories.json", self._missing_categories())
        _write_json(temp / "reports/conflicts.json", {"policy_conflicts": conflicts, "provider_order_cycles": provider_order_cycles, "provider_precedence": provider_plan.get("precedence_edges", []), "force_direct_conflicts": direct_conflicts})
        _write_json(temp / "reports/shared-domains.json", shared_domain_report)
        _write_json(temp / "reports/source-shared-scope.json", {"recognized_shared_roots": shared_roots, "reviewed_exact_additions": reviewed_additions, "observed_rules": shared_rule_scope, "blockers": shared_rule_scope_blockers})
        _write_json(temp / "reports/excluded-rules.json", self.exclusion_report)
        _write_json(temp / "reports/unsupported.json", unsupported)
        _write_json(temp / "reports/unsupported-dispositions.json", self.unsupported_disposition_report)
        _write_json(temp / "reports/source-inventory.json", self.inventory())
        _write_json(temp / "reports/categories.json", categories_report)
        notice_lines = ["Third-party and curated source material included with this rule bundle.", "", "The Builder program and generated outputs are not relicensed by these source notices.", ""]
        for source_id, item in sorted(self.source_license_materials.items()):
            source_dir = temp / "provenance/sources" / source_id
            source_dir.mkdir(parents=True, exist_ok=True)
            license_path = item.get("license_path")
            if isinstance(license_path, Path) and license_path.is_file():
                (source_dir / "LICENSE.txt").write_bytes(license_path.read_bytes())
                notice_lines.append(f"## {source_id}\n\nLicense: {item.get('license') or 'Unspecified'}; approved hash: {item.get('approved_license_sha256')}; bundled hash: {item.get('license_sha256')}.")
            notice_path = item.get("notice_path")
            if isinstance(notice_path, Path) and notice_path.is_file():
                (source_dir / "NOTICE.txt").write_bytes(notice_path.read_bytes())
            _write_json(source_dir / "source-approval.json", {name: value for name, value in item.items() if name not in {"license_path", "notice_path"}})
        (temp / "provenance/THIRD_PARTY_NOTICES.md").write_text("\n".join(notice_lines) + "\n", encoding="utf-8")
        _write_json(temp / "provenance/source-review-summary.json", {"source_approval_ledger_sha256": self.source_approvals_sha256, "source_profiles": self.source_profile_observations, "source_license_provenance": identity_inputs["source_license_provenance"], "source_profile_blockers": self.source_approval_blockers, "license_blockers": self.source_license_blockers, "shared_scope_blockers": shared_rule_scope_blockers, "publish_blockers": publish_blockers})

        artifact_hashes = {str(p.relative_to(temp)): sha256_file(p) for p in sorted(temp.rglob("*")) if p.is_file()}
        release_id = hashlib.sha256(canonical_json({"identity_inputs": identity_inputs, "artifact_sha256": artifact_hashes})).hexdigest()
        manifest = {"schema_version": 4, "source_capability_version": 1, "compiler_capability_version": 2, "license_capability_version": 1, "source_license_provenance": identity_inputs["source_license_provenance"], "source_license_blockers": self.source_license_blockers, "source_profile_observations": self.source_profile_observations, "provider_map": provider_map, "sources": source_manifest, "release_id": release_id, "build_purpose": self.build_purpose, "input_snapshot_time_utc": self.input_snapshot_time_utc, "builder_version": __version__, "code_commit": self.code_commit, "code_revision": hashlib.sha256(canonical_json(code_identity)).hexdigest(), "data_revision": release_data_revision, "source_revision": self.source_revision, "source_file_sha256": source_hashes, "custom_rules_sha256": custom_hash, "source_approvals_sha256": self.source_approvals_sha256, "approved_exclusions_sha256": self.approved_exclusions_sha256, "approved_exclusions": self.approved_exclusions, "exclusion_report": self.exclusion_report, "unsupported_dispositions_sha256": self.unsupported_dispositions_sha256, "user_unsupported_dispositions": self.user_unsupported_dispositions, "unsupported_dispositions": self.unsupported_dispositions, "unsupported_disposition_report": self.unsupported_disposition_report, "shared_domain_reviews_sha256": self.shared_domain_reviews_sha256, "shared_domain_reviews": self.shared_domain_reviews, "identity_inputs": identity_inputs, "artifact_sha256": artifact_hashes, "statistics": statistics, "category_status": categories_report, "mihomo_rule_order": mihomo_rule_order, **decision}
        _write_json(temp / "manifest.json", manifest)
        releases = output_root / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        final = releases / release_id
        if final.exists():
            shutil.rmtree(temp)
        else:
            os.replace(temp, final)
        verify_manifest(final)
        pointer = output_root / f".current-{os.getpid()}"
        pointer.unlink(missing_ok=True)
        pointer.symlink_to(final.relative_to(output_root), target_is_directory=True)
        os.replace(pointer, output_root / "current")
        return BuildResult(release_id, final, deployment_allowed, {**statistics, "review_required": review_required})

    def _missing_categories(self) -> dict[str, Any]:
        if "blackmatrix7" not in self.source_trees:
            primary = next((source_id for category, cfg in self.categories.items() if cfg.get("enabled") for source_id in [ref.get("source_id") for ref in cfg.get("source_refs", [])] if source_id in self.source_trees), None)
            return {"source_revision": self.source_revision, "source_id": primary, "items": [], "note": "Blackmatrix-specific requested directory candidates are not applicable to the selected source set."}
        base = self.source_root / self.sources_config["rules_path"]
        existing = {x.name.casefold(): x.name for x in base.iterdir() if x.is_dir()}
        aliases = {"stackoverflow": ["stackexchange"], "max": ["hbo", "hboasia", "hbohk", "hbousa"]}
        expected = ["perplexity", "xai", "cursor", "huggingface", "stackoverflow", "netlify", "pypi", "max"]
        result = []
        for name in expected:
            candidates = [existing[a] for a in aliases.get(name, []) if a in existing]
            result.append({"requested_name": name, "exact_directory": existing.get(name), "candidate_directories": candidates, "status": "available" if name in existing else ("candidate_requires_review" if candidates else "not_found")})
        for name in ("abema", "abematv", "bahamut", "npmjs"):
            result.append({"requested_name": name, "exact_directory": existing.get(name), "candidate_directories": [], "status": "available" if name in existing else "not_found"})
        return {"source_revision": self.source_revision, "items": result}


def verify_manifest(release_dir: Path) -> dict[str, Any]:
    try:
        if release_dir.is_symlink() or not release_dir.is_dir():
            raise BuildError("release directory must be a real directory")
        manifest_path = release_dir / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise BuildError("manifest must be a regular file")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"invalid/missing manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BuildError("manifest must be a JSON object")
    if manifest.get("schema_version") != 4:
        raise BuildError("unsupported manifest schema")
    actual = {}
    for parent, dirs, files in os.walk(release_dir, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            if path.is_symlink():
                raise BuildError(f"release contains symlink: {path.relative_to(release_dir)}")
        for name in files:
            path = Path(parent) / name
            if path == manifest_path:
                continue
            relative = path.relative_to(release_dir).as_posix()
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise BuildError(f"unsafe release path: {relative}")
            actual[relative] = sha256_file(path)
    expected_files = manifest.get("artifact_sha256")
    if not isinstance(expected_files, dict) or set(actual) != set(expected_files) or actual != expected_files:
        raise BuildError("artifact hash mismatch")
    identity_inputs = manifest.get("identity_inputs")
    if not isinstance(identity_inputs, dict) or identity_inputs.get("schema_version") != manifest.get("schema_version"):
        raise BuildError("manifest identity schema mismatch")
    if identity_inputs.get("build_purpose") not in {"rules", "smoke"}:
        raise BuildError("manifest build purpose is invalid")
    # Schema 4 was first released with a single implicit blackmatrix source.
    # Keep those manifests readable; capability v1 releases carry explicit
    # source identities and must match both duplicated top-level fields.
    if "sources" in identity_inputs:
        if identity_inputs.get("source_capability_version") != 1 or not isinstance(identity_inputs.get("sources"), dict):
            raise BuildError("manifest source capability metadata is invalid")
        if manifest.get("sources") != identity_inputs.get("sources") or manifest.get("source_capability_version") != 1:
            raise BuildError("manifest source metadata mismatch")
        source_files: dict[str, str] = {}
        for source_id, source in identity_inputs["sources"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source_id) or not isinstance(source, dict):
                raise BuildError("manifest source entry is invalid")
            if set(source) != {"repository", "revision", "archive_sha256", "source_date_utc", "kind", "adapter_version", "selected_refs", "file_sha256"}:
                raise BuildError(f"manifest source fields are invalid: {source_id}")
            if not isinstance(source["repository"], str) or not source["repository"] or not re.fullmatch(r"[0-9a-f]{40}", str(source["revision"])) or source["kind"] not in {"remote", "local"}:
                raise BuildError(f"manifest source identity is invalid: {source_id}")
            if (source["archive_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(source["archive_sha256"]))) or not isinstance(source["adapter_version"], int) or source["adapter_version"] < 1:
                raise BuildError(f"manifest source checksum or adapter version is invalid: {source_id}")
            refs = source["selected_refs"]
            if not isinstance(refs, list) or any(not isinstance(ref, dict) or set(ref) != {"category", "file", "format", "reference_kind"} or not all(isinstance(ref.get(k), str) and ref[k] for k in ("category", "file", "format", "reference_kind")) or ref.get("reference_kind") not in {"file", "list-key"} for ref in refs):
                raise BuildError(f"manifest selected source references are invalid: {source_id}")
            ref_keys = [(ref["category"], ref["file"], ref["format"], ref["reference_kind"]) for ref in refs]
            if len(set(ref_keys)) != len(ref_keys):
                raise BuildError(f"manifest selected source references are duplicated: {source_id}")
            hashes = source["file_sha256"]
            if not isinstance(hashes, dict) or any(not isinstance(path, str) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)) for path, digest in hashes.items()):
                raise BuildError(f"manifest source file hashes are invalid: {source_id}")
            if any(ref["reference_kind"] == "file" and ref["file"] not in hashes for ref in refs):
                raise BuildError(f"manifest selected source reference has no file hash: {source_id}")
            if any(ref["reference_kind"] == "list-key" and (ref["format"] != "v2fly-domain-list" or not re.fullmatch(r"[A-Za-z0-9_.!-]+", ref["file"])) for ref in refs):
                raise BuildError(f"manifest v2fly list key is invalid: {source_id}")
            for path, digest in hashes.items():
                namespaced = f"{source_id}:{path}"
                global_path = namespaced if namespaced in identity_inputs.get("source_file_sha256", {}) else path
                if global_path in source_files:
                    raise BuildError(f"duplicate source file identity: {global_path}")
                source_files[global_path] = digest
        if source_files != identity_inputs.get("source_file_sha256"):
            raise BuildError("manifest per-source file hashes do not match source_file_sha256")
    if "compiler_capability_version" in identity_inputs:
        if identity_inputs.get("compiler_capability_version") != 2 or manifest.get("compiler_capability_version") != 2:
            raise BuildError("unsupported compiler capability version")
        provider_map = identity_inputs.get("provider_map")
        if manifest.get("provider_map") != provider_map or not isinstance(provider_map, list):
            raise BuildError("manifest provider map mismatch")
        normalized = identity_inputs.get("normalized_rules")
        order = manifest.get("mihomo_rule_order")
        if not isinstance(normalized, list) or not isinstance(order, list) or [item.get("name") for item in provider_map if isinstance(item, dict)] != order:
            raise BuildError("provider map order is invalid")
        if len(provider_map) != len(order):
            raise BuildError("provider map has malformed entries")
        covered: list[int] = []
        mapped_payloads: dict[str, list[str]] = {}
        provider_policies: dict[str, str] = {}
        custom_seen_regular = False
        expected_provider_paths: set[str] = set()
        for provider in provider_map:
            if not isinstance(provider, dict) or set(provider) != {"name", "kind", "policy_group", "categories", "member_indices", "path"}:
                raise BuildError("provider map entry shape is invalid")
            name, kind, policy = provider["name"], provider["kind"], provider["policy_group"]
            if not isinstance(name, str) or not IDENTIFIER_RE.fullmatch(name) or kind not in {"custom-exact", "regular"} or not isinstance(policy, str) or not IDENTIFIER_RE.fullmatch(policy):
                raise BuildError("provider map contains unsafe name, kind, or policy")
            if provider["path"] != f"mihomo/providers/{name}.yaml":
                raise BuildError(f"provider {name} has an unsafe artifact path")
            expected_provider_paths.add(provider["path"])
            if not isinstance(provider["categories"], list) or not provider["categories"] or not all(isinstance(category, str) for category in provider["categories"]):
                raise BuildError(f"provider {name} has invalid category mapping")
            indices = provider["member_indices"]
            if not isinstance(indices, list) or not indices or any(not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(normalized) for index in indices):
                raise BuildError(f"provider {name} has invalid member indices")
            members = [normalized[index] for index in indices]
            categories = sorted({row.get("category") for row in members})
            if categories != sorted(provider["categories"]) or any(row.get("policy_group") != policy for row in members):
                raise BuildError(f"provider {name} maps a category or policy incorrectly")
            if kind == "custom-exact":
                if custom_seen_regular or any(not row.get("custom") or row.get("match_type") != "EXACT" for row in members) or not name.startswith("CustomExact-"):
                    raise BuildError("CustomExact providers must map only custom EXACT rules and precede regular providers")
            else:
                custom_seen_regular = True
                if any(row.get("custom") and row.get("match_type") == "EXACT" for row in members):
                    raise BuildError(f"regular provider {name} contains a custom EXACT rule")
            expected_lines = sorted({f"{row['match_type'].replace('EXACT', 'DOMAIN').replace('SUFFIX', 'DOMAIN-SUFFIX')},{row['domain']}" for row in members})
            mapped_payloads[name] = expected_lines
            provider_policies[name] = policy
            covered.extend(indices)
        if sorted(covered) != list(range(len(normalized))):
            raise BuildError("provider map must cover each normalized rule exactly once")
        if len({item["name"].casefold() for item in provider_map}) != len(provider_map):
            raise BuildError("provider map names are not unique")
        provider_rank = {provider["name"]: index for index, provider in enumerate(provider_map)}
        member_rank = {member_index: provider_rank[provider["name"]] for provider in provider_map for member_index in provider["member_indices"]}
        equal_scope_cross_policy = False
        from .provider_plan import _strictly_narrower as _plan_narrower, _overlap as _plan_overlap
        for left_index, left in enumerate(normalized):
            for right_index in range(left_index + 1, len(normalized)):
                right = normalized[right_index]
                if left.get("policy_group") == right.get("policy_group") or not _plan_overlap(left, right):
                    continue
                left_custom = bool(left.get("custom")) and left.get("match_type") == "EXACT"
                right_custom = bool(right.get("custom")) and right.get("match_type") == "EXACT"
                if left_custom != right_custom:
                    before, after = (left_index, right_index) if left_custom else (right_index, left_index)
                elif _plan_narrower(left, right) or _plan_narrower(right, left):
                    before, after = (left_index, right_index) if _plan_narrower(left, right) else (right_index, left_index)
                elif left.get("domain") == right.get("domain") and left.get("match_type") == right.get("match_type"):
                    if left.get("priority") == right.get("priority"):
                        equal_scope_cross_policy = True
                        continue
                    before, after = (left_index, right_index) if left.get("priority", 0) < right.get("priority", 0) else (right_index, left_index)
                else:
                    continue
                if member_rank[before] >= member_rank[after]:
                    raise BuildError("provider map violates custom or specificity precedence")
        if equal_scope_cross_policy:
            decision = identity_inputs.get("decision", {})
            blockers = decision.get("deployment_blockers", [])
            if not decision.get("review_required") or decision.get("deployment_allowed") or not any(
                isinstance(item, dict) and item.get("resolved") is False
                and item.get("reason") == "overlapping policies have equal priority or unresolved same-scope order"
                for item in blockers
            ):
                raise BuildError("equal-priority cross-policy overlap must block deployment")
        provider_root = release_dir / "mihomo/providers"
        actual_provider_paths = {path.relative_to(release_dir).as_posix() for path in provider_root.glob("*.yaml")} if provider_root.is_dir() else set()
        if actual_provider_paths != expected_provider_paths:
            raise BuildError("provider files do not exactly match the provider map")
        try:
            provider_doc = yaml.safe_load((release_dir / "mihomo/rule-providers.yaml").read_text(encoding="utf-8"))
            rules_doc = yaml.safe_load((release_dir / "mihomo/rules.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise BuildError("generated Mihomo mapping files are invalid") from exc
        declarations = provider_doc.get("rule-providers") if isinstance(provider_doc, dict) else None
        rules_list = rules_doc.get("rules") if isinstance(rules_doc, dict) else None
        if not isinstance(declarations, dict) or list(declarations) != order or not isinstance(rules_list, list):
            raise BuildError("generated provider declarations do not match the manifest map")
        expected_rules = [f"RULE-SET,{provider['name']},{provider['policy_group']}" for provider in provider_map]
        if rules_list != expected_rules:
            raise BuildError("generated rule sequence does not match provider category/policy mapping")
        for provider in provider_map:
            name = provider["name"]
            declaration = declarations.get(name)
            if not isinstance(declaration, dict) or declaration != {"type": "file", "behavior": "classical", "format": "yaml", "path": f"./providers/{name}.yaml"}:
                raise BuildError(f"provider {name} declaration differs from managed path")
            try:
                payload_doc = yaml.safe_load((release_dir / provider["path"]).read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise BuildError(f"provider {name} payload is invalid") from exc
            payload = payload_doc.get("payload") if isinstance(payload_doc, dict) else None
            if payload != mapped_payloads[name]:
                raise BuildError(f"provider {name} payload does not match mapped rule members")
    if "license_capability_version" in identity_inputs:
        if identity_inputs.get("license_capability_version") != 1 or manifest.get("license_capability_version") != 1:
            raise BuildError("unsupported license capability version")
        provenance = identity_inputs.get("source_license_provenance")
        license_blockers = identity_inputs.get("source_license_blockers")
        decision = identity_inputs.get("decision", {})
        if not isinstance(provenance, dict) or manifest.get("source_license_provenance") != provenance or not isinstance(license_blockers, list) or manifest.get("source_license_blockers") != license_blockers:
            raise BuildError("manifest source license provenance mismatch")
        sources = identity_inputs.get("sources")
        observations = identity_inputs.get("source_profile_observations")
        normalized_rules = identity_inputs.get("normalized_rules")
        if not isinstance(sources, dict) or not isinstance(observations, dict) or not isinstance(normalized_rules, list):
            raise BuildError("manifest source selection metadata is missing")
        selected_source_ids: set[str] = set()
        expected_refs_by_category: dict[str, list[tuple[str, str, str]]] = {}
        for source_id, source in sources.items():
            if not isinstance(source, dict) or not isinstance(source.get("selected_refs"), list):
                raise BuildError("manifest selected source references are invalid")
            for ref in source["selected_refs"]:
                if not isinstance(ref, dict):
                    raise BuildError("manifest selected source reference is invalid")
                selected_source_ids.add(source_id)
                expected_refs_by_category.setdefault(ref["category"], []).append((source_id, ref["file"], ref["format"]))
        observed_categories = {category for category, refs in expected_refs_by_category.items() if refs}
        required_provenance_ids = selected_source_ids
        if set(provenance) != required_provenance_ids:
            raise BuildError("source license provenance does not cover selected source references")
        if decision.get("publish_allowed"):
            if set(observations) != observed_categories:
                raise BuildError("publishable source profile observations contain missing or extra categories")
            for category in observed_categories:
                observation = observations[category]
                if not isinstance(observation, dict) or observation.get("status") != "approved" or not isinstance(observation.get("selected_sources"), list):
                    raise BuildError("publishable candidate has an unapproved or malformed source profile")
                observed_refs = []
                for ref in observation["selected_sources"]:
                    if not isinstance(ref, dict):
                        raise BuildError("source profile selected reference is malformed")
                    reference = ref.get("reference", ref.get("key", ref.get("file")))
                    if isinstance(reference, str):
                        observed_refs.append((ref.get("source_id"), reference, ref.get("format")))
                unmatched_observed = list(observed_refs)
                for source_id, selected_file, selected_format in expected_refs_by_category[category]:
                    match_index = next((index for index, (observed_source, observed_file, observed_format) in enumerate(unmatched_observed) if observed_source == source_id and observed_format == selected_format and (selected_file == observed_file or selected_file.endswith("/" + observed_file))), None)
                    if match_index is None:
                        raise BuildError("source profile observation does not cover selected source references")
                    unmatched_observed.pop(match_index)
                if unmatched_observed:
                    raise BuildError("source profile observation contains unselected source references")
            for source_id in sorted(required_provenance_ids):
                record = provenance.get(source_id)
                if not isinstance(record, dict) or record.get("status") != "approved":
                    raise BuildError(f"publishable candidate has unapproved source license material: {source_id}")
                approved = record.get("approved_license_sha256")
                license_hashes = (record.get("license_sha256"), record.get("license_copy_sha256"), record.get("license_bundled_sha256"))
                if not isinstance(approved, str) or not re.fullmatch(r"[0-9a-f]{64}", approved) or any(value != approved for value in license_hashes):
                    raise BuildError(f"publishable candidate source license hashes are not approved: {source_id}")
                approved_notice = record.get("approved_notice_sha256")
                notice_hashes = (record.get("notice_sha256"), record.get("notice_copy_sha256"), record.get("notice_bundled_sha256"))
                if approved_notice is not None:
                    if not isinstance(approved_notice, str) or not re.fullmatch(r"[0-9a-f]{64}", approved_notice) or any(value != approved_notice for value in notice_hashes):
                        raise BuildError(f"publishable candidate source NOTICE hashes are not approved: {source_id}")
                elif any(value is not None for value in notice_hashes):
                    raise BuildError(f"publishable candidate includes unapproved source NOTICE material: {source_id}")
        selected_rule_sources = {row.get("source") for row in normalized_rules if isinstance(row, dict) and not row.get("custom")}
        if not selected_rule_sources.issubset(required_provenance_ids):
            raise BuildError("normalized rules reference a source without selected licensed material")
        for row in normalized_rules:
            if not isinstance(row, dict) or row.get("custom"):
                continue
            source_id = row.get("source")
            category = row.get("category")
            source_file = row.get("source_file")
            source_key = row.get("source_key")
            candidates = expected_refs_by_category.get(category, [])
            if not any(
                ref_source == source_id
                and ((source_key is not None and source_key == selected_file)
                     or (source_key is None and isinstance(source_file, str) and (source_file == selected_file or source_file.endswith("/" + selected_file))))
                for ref_source, selected_file, _selected_format in candidates
            ):
                raise BuildError("normalized rule is outside its selected source reference")
        expected_ids = {source_id for source_id, record in provenance.items() if isinstance(record, dict) and record.get("license_bundled_sha256") is not None}
        found_license_ids = set()
        found_notice_ids = set()
        for source_id, record in provenance.items():
            if not IDENTIFIER_RE.fullmatch(source_id) or not isinstance(record, dict):
                raise BuildError("manifest source license record is malformed")
            license_name = f"provenance/sources/{source_id}/LICENSE.txt"
            notice_name = f"provenance/sources/{source_id}/NOTICE.txt"
            if license_name in actual:
                found_license_ids.add(source_id)
                if actual[license_name] != record.get("license_bundled_sha256"):
                    raise BuildError(f"bundled source license hash mismatch: {source_id}")
            if notice_name in actual:
                found_notice_ids.add(source_id)
                if actual[notice_name] != record.get("notice_bundled_sha256"):
                    raise BuildError(f"bundled source notice hash mismatch: {source_id}")
            bundled_hash = record.get("license_bundled_sha256")
            if bundled_hash is None:
                if source_id in found_license_ids or record.get("status") == "approved":
                    raise BuildError(f"source license bundle hash is invalid: {source_id}")
                continue
            if not isinstance(bundled_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", bundled_hash):
                raise BuildError(f"source license bundle hash is invalid: {source_id}")
            if record.get("notice_bundled_sha256") is not None and (source_id not in found_notice_ids or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("notice_bundled_sha256")))):
                raise BuildError(f"source notice bundle hash is invalid: {source_id}")
        if found_license_ids != expected_ids:
            raise BuildError("source license material is missing from candidate bundle")
        summary_path = "provenance/source-review-summary.json"
        try:
            summary = json.loads((release_dir / summary_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError("source review summary is missing or invalid") from exc
        if summary.get("license_blockers") != license_blockers or summary.get("publish_blockers") != decision.get("publish_blockers") or summary.get("source_profiles") != identity_inputs.get("source_profile_observations") or summary.get("source_license_provenance") != provenance or summary.get("shared_scope_blockers") != decision.get("source_shared_scope_blockers"):
            raise BuildError("source review summary differs from identity")
        if (license_blockers or decision.get("publish_blockers")) and decision.get("publish_allowed"):
            raise BuildError("manifest allows publication despite unresolved source approval blockers")
        if decision.get("publish_allowed") and decision.get("review_required"):
            raise BuildError("manifest allows publication while review is required")
    if not isinstance(identity_inputs.get("decision"), dict):
        raise BuildError("manifest decision identity is missing")
    expected = hashlib.sha256(canonical_json({"identity_inputs": identity_inputs, "artifact_sha256": actual})).hexdigest()
    if expected != manifest.get("release_id") or release_dir.name != manifest.get("release_id"):
        raise BuildError("release identity mismatch")
    duplicate_fields = {
        "build_purpose": identity_inputs.get("build_purpose"),
        "builder_version": identity_inputs.get("builder_version"),
        "code_commit": identity_inputs.get("code_commit"),
        "source_revision": identity_inputs.get("source_revision"),
        "source_revision": identity_inputs.get("source_revision"),
        "source_file_sha256": identity_inputs.get("source_file_sha256"),
        "custom_rules_sha256": identity_inputs.get("custom_rules_sha256"),
        "approved_exclusions_sha256": identity_inputs.get("approved_exclusions_sha256"),
        "approved_exclusions": identity_inputs.get("approved_exclusions"),
        "exclusion_report": identity_inputs.get("exclusion_report"),
        "data_revision": identity_inputs.get("data_revision"),
        "shared_domain_reviews_sha256": identity_inputs.get("shared_domain_reviews_sha256"),
        "shared_domain_reviews": identity_inputs.get("shared_domain_reviews"),
        "mihomo_rule_order": identity_inputs.get("mihomo_rule_order"),
        "category_status": json.loads((release_dir / "reports/categories.json").read_text(encoding="utf-8")),
        "statistics": json.loads((release_dir / "reports/statistics.json").read_text(encoding="utf-8")),
        "review_required": identity_inputs.get("decision", {}).get("review_required"),
        "deployment_allowed": identity_inputs.get("decision", {}).get("deployment_allowed"),
        "publish_allowed": identity_inputs.get("decision", {}).get("publish_allowed"),
        "target_semantics_verified": identity_inputs.get("decision", {}).get("target_semantics_verified"),
        "deployment_enabled": identity_inputs.get("decision", {}).get("deployment_enabled"),
        "deployment_blockers": identity_inputs.get("decision", {}).get("deployment_blockers"),
        "publish_blockers": identity_inputs.get("decision", {}).get("publish_blockers"),
    }
    if "sources" in identity_inputs:
        duplicate_fields["sources"] = identity_inputs.get("sources")
        duplicate_fields["source_capability_version"] = 1
    if "compiler_capability_version" in identity_inputs:
        duplicate_fields["compiler_capability_version"] = 2
        duplicate_fields["provider_map"] = identity_inputs.get("provider_map")
    if "license_capability_version" in identity_inputs:
        duplicate_fields["license_capability_version"] = 1
        duplicate_fields["source_license_provenance"] = identity_inputs.get("source_license_provenance")
        duplicate_fields["source_license_blockers"] = identity_inputs.get("source_license_blockers")
        duplicate_fields["source_profile_observations"] = identity_inputs.get("source_profile_observations")
        duplicate_fields["source_shared_scope_blockers"] = identity_inputs.get("decision", {}).get("source_shared_scope_blockers")
    if "source_approvals_sha256" in identity_inputs:
        duplicate_fields["source_approvals_sha256"] = identity_inputs.get("source_approvals_sha256")
    if "unsupported_dispositions_sha256" in identity_inputs:
        dispositions = identity_inputs.get("unsupported_dispositions")
        report = identity_inputs.get("unsupported_disposition_report")
        user_dispositions = identity_inputs.get("user_unsupported_dispositions")
        if not isinstance(dispositions, list) or not isinstance(user_dispositions, list) or identity_inputs.get("unsupported_dispositions_sha256") != approval_scope_sha256(user_dispositions) or not isinstance(report, list):
            raise BuildError("manifest rules-data unsupported disposition metadata is invalid")
        try:
            validate_unsupported_dispositions(dispositions)
        except ApprovalError as exc:
            raise BuildError(f"manifest unsupported disposition is invalid: {exc}") from exc
        profile_observations = identity_inputs.get("source_profile_observations", {})
        for item in dispositions:
            profile = profile_observations.get(item["category"], {}) if isinstance(profile_observations, dict) else {}
            refs = profile.get("selected_sources", []) if isinstance(profile, dict) else []
            if not any(ref.get("source_id") == item["source_id"] and ref.get("reference") == item["source_key"] for ref in refs if isinstance(ref, dict)):
                raise BuildError("manifest unsupported disposition exceeds the selected source scope")
        duplicate_fields["unsupported_dispositions_sha256"] = identity_inputs.get("unsupported_dispositions_sha256")
        duplicate_fields["user_unsupported_dispositions"] = user_dispositions
        duplicate_fields["unsupported_dispositions"] = dispositions
        duplicate_fields["unsupported_disposition_report"] = report
        try:
            bundled_report = json.loads((release_dir / "reports/unsupported-dispositions.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError("unsupported disposition report is missing or invalid") from exc
        if bundled_report != report:
            raise BuildError("unsupported disposition report differs from manifest identity")
    for field, value in duplicate_fields.items():
        if manifest.get(field) != value:
            raise BuildError(f"manifest field mismatch: {field}")
    if manifest.get("code_revision") != hashlib.sha256(canonical_json(identity_inputs.get("code_identity"))).hexdigest():
        raise BuildError("manifest code revision mismatch")
    if manifest.get("data_revision") != identity_inputs.get("data_revision"):
        raise BuildError("manifest data revision mismatch")
    if manifest.get("target_semantics_verified") != identity_inputs.get("decision", {}).get("target_semantics_verified"):
        raise BuildError("manifest target validation status mismatch")
    if manifest.get("deployment_blockers") != identity_inputs.get("decision", {}).get("deployment_blockers"):
        raise BuildError("manifest deployment blocker mismatch")
    return manifest


def explain(release_dir: Path, domain: str) -> list[dict[str, Any]]:
    host = canonical_domain(domain)
    manifest = verify_manifest(release_dir)
    items = manifest["identity_inputs"].get("normalized_rules", [])
    provider_for_index: dict[int, str] = {}
    for provider in manifest.get("provider_map", []):
        for index in provider.get("member_indices", []):
            provider_for_index[index] = provider["name"]
    found = []
    for index, rule in enumerate(items):
        name = rule["domain"]
        matches = host == name if rule["match_type"] == "EXACT" else host == name or host.endswith("." + name)
        if matches:
            found.append({**rule, "_provider": provider_for_index.get(index, f"CustomExact-{rule['provider_name']}" if rule["custom"] and rule["match_type"] == "EXACT" else rule["provider_name"])})
    provider_order = manifest.get("mihomo_rule_order", [])
    rank = {name: index for index, name in enumerate(provider_order)}
    found.sort(key=lambda rule: (rank.get(rule["_provider"], len(rank)), 0 if rule["match_type"] == "EXACT" else 1, -len(rule["domain"]), rule["domain"], rule["source_file"], rule["line"]))
    return found
