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


def _read_source_file(path: Path, logical_path: str, category: str, cfg: dict[str, Any], source_revision: str) -> tuple[list[Rule], list[dict[str, Any]]]:
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
        rules.append(Rule(domain, "EXACT" if kind == "DOMAIN" else "SUFFIX", category, cfg["provider_name"], cfg["policy_group"], int(cfg["priority"]), "blackmatrix7", logical_path, source_revision, line_number))
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


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


class Builder:
    def __init__(self, project_root: Path, source_root: Path | None = None, source_revision: str | None = None, data_revision: str | None = None, code_commit: str | None = None, input_snapshot_time_utc: str | None = None, build_purpose: str = "rules"):
        self.root = project_root.resolve()
        self.source_root = (source_root or (self.root / ".cache/ios_rule_script-83f37120")).resolve()
        self.sources_config = load_yaml(self.root / "config/sources.yaml")["sources"]["blackmatrix7"]
        self.source_revision = source_revision or self.sources_config["revision"]
        self.data_revision = data_revision
        self.code_commit = code_commit
        self.input_snapshot_time_utc = input_snapshot_time_utc
        if build_purpose not in {"rules", "smoke"}:
            raise BuildError("build_purpose must be rules or smoke")
        self.build_purpose = build_purpose
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_revision):
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
        marker = self.source_root / ".source-revision"
        if not marker.is_file():
            raise BuildError(f"source tree must include .source-revision containing {self.source_revision}")
        if marker.read_text(encoding="utf-8").strip() != self.source_revision:
            raise BuildError(f"source tree revision marker does not match requested {self.source_revision}")
        self.categories = load_yaml(self.root / "config/categories.yaml").get("categories", {})
        self.policies = load_yaml(self.root / "config/policies.yaml").get("policies", {})
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
        self.approved_exclusions = exclusion_data.get("exclusions", [])
        if not isinstance(self.approved_exclusions, list):
            raise BuildError("data/approved-exclusions.yaml exclusions must be a list")
        self.approved_exclusions_sha256 = sha256_file(exclusions_path) if exclusions_path.exists() else hashlib.sha256(b"exclusions: []\n").hexdigest()
        review_path = self.root / "data/shared-domain-reviews.yaml"
        review_data = load_yaml(review_path) if review_path.exists() else {"reviews": []}
        self.shared_domain_reviews = review_data.get("reviews", [])
        self.shared_domain_reviews_sha256 = sha256_file(review_path) if review_path.exists() else hashlib.sha256(b"reviews: []\n").hexdigest()
        if not isinstance(self.shared_domain_reviews, list):
            raise BuildError("data/shared-domain-reviews.yaml reviews must be a list")
        archive = self.root / self.sources_config["archive"]
        expected_archive_hash = self.sources_config.get("archive_sha256")
        if archive.is_file() and expected_archive_hash and sha256_file(archive) != expected_archive_hash:
            raise BuildError(f"locked source archive checksum mismatch: {archive}")
        revision_archive = self.source_root.parent / f"ios_rule_script-{self.source_revision}.tar.gz"
        self.source_archive_sha256 = sha256_file(revision_archive) if revision_archive.is_file() else (expected_archive_hash if self.source_revision == self.sources_config["revision"] else None)

    def inventory(self) -> dict[str, Any]:
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
        source_revision = self.source_revision
        upstream_base = self.source_root / self.sources_config["rules_path"]
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
            if cfg.get("format", "surge") not in {"surge", "surge-domain-set"}:
                raise BuildError(f"category {category}: unsupported format {cfg.get('format')!r}")
            if cfg["enabled"]:
                if category != "foreign_general" and cfg.get("source_file") is None:
                    raise BuildError(f"category {category}: enabled source_file is required")
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
            path = upstream_base / cfg["source_file"] if cfg.get("source_file") else None
            logical_path = None
            if path is not None:
                resolved = path.resolve()
                if not resolved.is_relative_to(upstream_base.resolve()):
                    raise BuildError(f"category {category}: source_file escapes source tree")
                if not resolved.is_file() or resolved.stat().st_size == 0:
                    raise BuildError(f"category {category}: missing or empty source file {path}")
                try:
                    has_entries = any(line.strip() and not line.lstrip().startswith(("#", ";")) for line in resolved.read_text(encoding="utf-8-sig", errors="replace").splitlines())
                except OSError as exc:
                    raise BuildError(f"category {category}: cannot read source file {path}: {exc}") from exc
                if not has_entries:
                    raise BuildError(f"category {category}: missing or empty source file {path}")
                logical_path = f"{self.sources_config['rules_path']}/{cfg['source_file']}"
                source_hashes[logical_path] = sha256_file(resolved)
                parsed, unsupported_items = _read_source_file(resolved, logical_path, category, cfg, source_revision)
                rules.extend(parsed)
                unsupported.extend(unsupported_items)
            categories_report.append({"category": category, "provider_name": cfg["provider_name"], "policy_group": policy, "priority": cfg["priority"], "source_file": logical_path, "rule_count": len([r for r in rules if r.category == category]), "unsupported_count": len([u for u in unsupported if u["category"] == category]), "status": "partial" if any(u["category"] == category for u in unsupported) else "complete"})

        self.loaded_upstream_rule_count = sum(not rule.custom for rule in rules)
        # Approved exclusions match one exact upstream semantic rule and category.
        # They never remove custom rules or infer parent/child relationships.
        self.exclusion_report = []
        seen_exclusions: set[tuple[str, str, str]] = set()
        for index, item in enumerate(self.approved_exclusions, 1):
            row = {"index": index, "exclusion": item, "status": "invalid", "excluded_sources": []}
            if not isinstance(item, dict) or set(item) != {"category", "domain", "match_type", "reviewer", "reviewed_at", "reason"}:
                row["reason"] = "required fields are missing or unknown"
                self.exclusion_report.append(row)
                continue
            category, raw_domain, match_type = item.get("category"), item.get("domain"), item.get("match_type")
            try:
                domain = canonical_domain(raw_domain) if isinstance(raw_domain, str) else None
            except ValueError:
                domain = None
            review_fields_valid = all(isinstance(item.get(field), str) and item[field].strip() for field in ("reviewer", "reviewed_at", "reason"))
            if not isinstance(category, str) or category not in self.categories or not isinstance(raw_domain, str) or domain != raw_domain or not isinstance(match_type, str) or match_type not in {"EXACT", "SUFFIX"} or not review_fields_valid:
                row["reason"] = "invalid category, canonical domain, match type, or review metadata"
                self.exclusion_report.append(row)
                continue
            key = (category, domain, match_type)
            if key in seen_exclusions:
                row["reason"] = "duplicate exclusion"
                self.exclusion_report.append(row)
                continue
            seen_exclusions.add(key)
            matched = [rule for rule in rules if not rule.custom and rule.category == category and rule.domain == domain and rule.match_type == match_type]
            if not matched:
                row["status"] = "unmatched"
                row["reason"] = "no exact upstream rule matched"
                self.exclusion_report.append(row)
                continue
            row["status"] = "applied"
            row["excluded_sources"] = [asdict(rule) for rule in matched]
            self.exclusion_report.append(row)
            excluded_ids = {id(rule) for rule in matched}
            rules = [rule for rule in rules if id(rule) not in excluded_ids]

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
        conflicts = []
        provider_dependencies: set[tuple[str, str]] = set()
        for i, left in enumerate(rules):
            for right in rules[i + 1:]:
                if left.category == right.category or left.policy_group == right.policy_group or not _overlaps(left, right):
                    continue
                left_custom_exact = left.custom and left.match_type == "EXACT"
                right_custom_exact = right.custom and right.match_type == "EXACT"
                if left_custom_exact != right_custom_exact:
                    winner, loser = (left, right) if left_custom_exact else (right, left)
                    resolved = True
                    reason = "custom EXACT provider is emitted first"
                elif _strictly_more_specific(left, right) or _strictly_more_specific(right, left):
                    winner, loser = (left, right) if _strictly_more_specific(left, right) else (right, left)
                    provider_dependencies.add((winner.category, loser.category))
                    resolved = True
                    reason = "more-specific matching rule must precede the broader provider"
                elif left.priority != right.priority:
                    winner, loser = (left, right) if left.priority < right.priority else (right, left)
                    resolved = True
                    reason = "explicit category priority"
                else:
                    winner, loser, resolved, reason = None, None, False, "overlapping policies have equal priority"
                conflicts.append({"domain_a": left.domain, "match_a": left.match_type, "category_a": left.category, "policy_a": left.policy_group, "domain_b": right.domain, "match_b": right.match_type, "category_b": right.category, "policy_b": right.policy_group, "resolved": resolved, "winner": winner.category if winner else None, "shadowed": loser.category if loser else None, "reason": reason})

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
        code_identity = {str(p.relative_to(self.root)): sha256_file(p) for p in sorted((self.root / "src").rglob("*.py"))}
        config_identity = {str(p.relative_to(self.root)): sha256_file(p) for p in sorted((self.root / "config").glob("*.yaml"))}
        custom_path = self.root / "data/custom-rules.yaml"
        custom_hash = sha256_file(custom_path) if custom_path.exists() else hashlib.sha256(b"rules: []\nforce_direct: []\n").hexdigest()
        release_data_revision = self.data_revision or custom_hash
        by_category: dict[str, list[Rule]] = {category: [] for category in self.categories}
        for rule in rules:
            by_category[rule.category].append(rule)
        custom_exact_groups: dict[str, list[Rule]] = {}
        for rule in rules:
            if rule.custom and rule.match_type == "EXACT":
                custom_exact_groups.setdefault(rule.category, []).append(rule)
        active_cfg = [(name, cfg) for name, cfg in self.categories.items() if cfg.get("enabled") and by_category[name]]
        active_cfg.sort(key=lambda x: (x[1]["priority"], x[1]["provider_name"].casefold()))
        custom_provider_order = [f"CustomExact-{self.categories[category]['provider_name']}" for category in sorted(custom_exact_groups, key=lambda c: (self.categories[c]["priority"], self.categories[c]["provider_name"].casefold()))]
        regular_categories = [category for category, _ in active_cfg if any(not (r.custom and r.match_type == "EXACT") for r in by_category[category])]
        outgoing = {category: set() for category in regular_categories}
        indegree = {category: 0 for category in regular_categories}
        for narrower, broader in provider_dependencies:
            if narrower in outgoing and broader in outgoing and broader not in outgoing[narrower]:
                outgoing[narrower].add(broader)
                indegree[broader] += 1
        category_rank = {category: index for index, (category, _cfg) in enumerate(active_cfg)}
        ready = sorted((category for category in regular_categories if indegree[category] == 0), key=category_rank.get)
        ordered_categories = []
        while ready:
            category = ready.pop(0)
            ordered_categories.append(category)
            for following in sorted(outgoing[category], key=category_rank.get):
                indegree[following] -= 1
                if indegree[following] == 0:
                    ready.append(following)
                    ready.sort(key=category_rank.get)
        cyclic_categories = sorted((category for category in regular_categories if indegree[category] > 0), key=category_rank.get)
        provider_order_cycles = []
        if cyclic_categories:
            provider_order_cycles.append({"categories": cyclic_categories, "reason": "specificity precedence constraints form a cycle across providers"})
            ordered_categories.extend(cyclic_categories)
        regular_provider_order = [self.categories[category]["provider_name"] for category in ordered_categories]
        mihomo_rule_order = custom_provider_order + regular_provider_order
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
        approved_types = set(settings.get("approved_unsupported_types", []))
        unknown_unsupported = [item for item in unsupported if item.get("type") not in approved_types or item.get("reason") != "not an EXACT/SUFFIX domain rule"]
        unresolved_exclusions = [row for row in self.exclusion_report if row["status"] != "applied"]
        review_required = bool(unknown_unsupported or empty_categories or sharp_drops or unresolved_conflicts or provider_order_cycles or direct_conflicts or unresolved_shared_reviews or unresolved_exclusions)
        deployment_allowed = deployment_enabled and target_verified and not review_required and not unresolved_conflicts and not provider_order_cycles and not direct_conflicts and not unresolved_shared_reviews and not unresolved_exclusions
        review_blockers = ([{"reason": "unsupported input rules require review", "count": len(unknown_unsupported)}] if unknown_unsupported else []) + ([{"reason": "enabled category empty", "categories": empty_categories}] if empty_categories else []) + ([{"reason": "category decreased by more than 30 percent", "categories": sharp_drops}] if sharp_drops else [])
        deployment_blockers = review_blockers + unresolved_conflicts + provider_order_cycles + direct_conflicts + ([{"reason": "shared-domain review required", "entries": [item["id"] for item in unresolved_shared_reviews]}] if unresolved_shared_reviews else []) + ([{"reason": "approved exclusion invalid or unmatched", "entries": unresolved_exclusions}] if unresolved_exclusions else []) + ([] if target_verified else [{"reason": "target SmartDNS/Mihomo semantics not verified"}]) + ([] if deployment_enabled else [{"reason": "deployment master switch is disabled"}])
        publish_allowed = license_release_approved or (upstream_rule_count == 0 and custom_rule_count > 0)
        decision = {"review_required": review_required, "deployment_allowed": deployment_allowed, "publish_allowed": publish_allowed, "target_semantics_verified": target_verified, "deployment_enabled": deployment_enabled, "deployment_blockers": deployment_blockers}
        identity_inputs = {"schema_version": 4, "build_purpose": self.build_purpose, "builder_version": __version__, "code_commit": self.code_commit, "code_identity": code_identity, "config_identity": config_identity, "custom_rules_sha256": custom_hash, "approved_exclusions_sha256": self.approved_exclusions_sha256, "approved_exclusions": self.approved_exclusions, "exclusion_report": self.exclusion_report, "shared_domain_reviews_sha256": self.shared_domain_reviews_sha256, "shared_domain_reviews": self.shared_domain_reviews, "data_revision": release_data_revision, "source_revision": self.source_revision, "source_archive_sha256": self.source_archive_sha256, "source_file_sha256": source_hashes, "normalized_rules": normalized_rules, "mihomo_rule_order": mihomo_rule_order, "decision": decision}

        staging_root = output_root / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix="candidate-", dir=staging_root))
        (temp / "smartdns").mkdir()
        (temp / "mihomo/providers").mkdir(parents=True)
        (temp / "reports").mkdir()
        custom_provider_by_category = {}
        for category, rows in sorted(custom_exact_groups.items()):
            name = f"CustomExact-{categories_by_id[category]['provider_name']}"
            custom_provider_by_category[category] = name
            (temp / "mihomo/providers" / f"{name}.yaml").write_text(_provider_yaml(rows), encoding="utf-8")
        for category, cfg in sorted(self.categories.items()):
            if not cfg.get("enabled"):
                continue
            regular = [r for r in by_category[category] if not (r.custom and r.match_type == "EXACT")]
            if regular:
                (temp / "mihomo/providers" / f"{cfg['provider_name']}.yaml").write_text(_provider_yaml(regular), encoding="utf-8")

        smartdns_rules = sorted({r.smartdns() for r in rules})
        (temp / "smartdns/proxy-domains.txt").write_text("".join(row + "\n" for row in smartdns_rules), encoding="utf-8")
        providers = []
        rule_lines = []
        custom_policy = {f"CustomExact-{self.categories[category]['provider_name']}": self.categories[category]["policy_group"] for category in custom_exact_groups}
        for name in custom_provider_order:
            providers.append((name, custom_policy[name]))
            rule_lines.append(f"RULE-SET,{name},{custom_policy[name]}")
        regular_policy = {cfg["provider_name"]: cfg["policy_group"] for category, cfg in active_cfg if any(not (r.custom and r.match_type == "EXACT") for r in by_category[category])}
        for name in regular_provider_order:
            providers.append((name, regular_policy[name]))
            rule_lines.append(f"RULE-SET,{name},{regular_policy[name]}")
        provider_lines = ["rule-providers:"]
        for name, _policy in providers:
            provider_lines += [f"  {name}:", "    type: file", "    behavior: classical", "    format: yaml", f"    path: ./providers/{name}.yaml"]
        (temp / "mihomo/rule-providers.yaml").write_text("\n".join(provider_lines) + "\n", encoding="utf-8")
        rules_lines = ["rules:"] + [f"  - {json.dumps(line, ensure_ascii=False)}" for line in rule_lines]
        (temp / "mihomo/rules.yaml").write_text("\n".join(rules_lines) + "\n", encoding="utf-8")

        for row in categories_report:
            if row["rule_count"] == 0:
                row["status"] = "empty"
        statistics = {"category_count": sum(row["rule_count"] > 0 for row in categories_report), "enabled_category_count": len(categories_report), "rule_count": len(rules), "unique_semantic_rule_count": len({(r.domain, r.match_type) for r in rules}), "smartdns_entry_count": len(smartdns_rules), "unsupported_count": len(unsupported), "conflict_count": len(conflicts), "unresolved_conflict_count": sum(not x["resolved"] for x in conflicts), "force_direct_conflict_count": len(direct_conflicts)}
        _write_json(temp / "reports/statistics.json", statistics)
        _write_json(temp / "reports/missing-categories.json", self._missing_categories())
        _write_json(temp / "reports/conflicts.json", {"policy_conflicts": conflicts, "provider_order_cycles": provider_order_cycles, "provider_dependencies": sorted([list(edge) for edge in provider_dependencies]), "force_direct_conflicts": direct_conflicts})
        _write_json(temp / "reports/shared-domains.json", shared_domain_report)
        _write_json(temp / "reports/excluded-rules.json", self.exclusion_report)
        _write_json(temp / "reports/unsupported.json", unsupported)
        _write_json(temp / "reports/source-inventory.json", self.inventory())
        _write_json(temp / "reports/categories.json", categories_report)

        artifact_hashes = {str(p.relative_to(temp)): sha256_file(p) for p in sorted(temp.rglob("*")) if p.is_file()}
        release_id = hashlib.sha256(canonical_json({"identity_inputs": identity_inputs, "artifact_sha256": artifact_hashes})).hexdigest()
        manifest = {"schema_version": 4, "release_id": release_id, "build_purpose": self.build_purpose, "input_snapshot_time_utc": self.input_snapshot_time_utc, "builder_version": __version__, "code_commit": self.code_commit, "code_revision": hashlib.sha256(canonical_json(code_identity)).hexdigest(), "data_revision": release_data_revision, "source_revision": self.source_revision, "source_file_sha256": source_hashes, "custom_rules_sha256": custom_hash, "approved_exclusions_sha256": self.approved_exclusions_sha256, "approved_exclusions": self.approved_exclusions, "exclusion_report": self.exclusion_report, "shared_domain_reviews_sha256": self.shared_domain_reviews_sha256, "shared_domain_reviews": self.shared_domain_reviews, "identity_inputs": identity_inputs, "artifact_sha256": artifact_hashes, "statistics": statistics, "category_status": categories_report, "mihomo_rule_order": mihomo_rule_order, **decision}
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
    }
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
    found = []
    for rule in items:
        name = rule["domain"]
        matches = host == name if rule["match_type"] == "EXACT" else host == name or host.endswith("." + name)
        if matches:
            found.append(rule)
    provider_order = manifest.get("mihomo_rule_order", [])
    rank = {name: index for index, name in enumerate(provider_order)}
    for rule in found:
        if rule["custom"] and rule["match_type"] == "EXACT":
            rule["_provider"] = f"CustomExact-{rule['provider_name']}"
        else:
            rule["_provider"] = rule["provider_name"]
    found.sort(key=lambda rule: (rank.get(rule["_provider"], len(rank)), rule["domain"], rule["match_type"]))
    return found
