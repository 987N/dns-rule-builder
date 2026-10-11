"""Adapters for the supported upstream source formats."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .engine import BuildError, canonical_domain


MAX_SOURCE_FILE_BYTES = 32 * 1024 * 1024
MAX_INCLUDE_DEPTH = 32
MAX_EXPANDED_ENTRIES = 2_000_000
_LEGAL_DECLARATION = re.compile(r"(?:\blicen[cs]e\b|\bcopyright\b|\bredistribut\w*\b|\ball rights\b|\bproprietary\b|\bpermission\b|版权所有|禁止转载|保留所有权利|未经授权)", re.IGNORECASE)
_V2FLY_CACHE: dict[tuple[str, str, tuple[tuple[str, int, int, int, int], ...]], tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]] = {}


def safe_source_file(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise BuildError(f"unsafe source file path: {relative!r}")
    path = root.joinpath(*pure.parts)
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BuildError(f"symlink in source file path: {relative}")
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise BuildError(f"missing or empty source file (or unsafe path): {relative}")
    if resolved.stat().st_size == 0 or resolved.stat().st_size > MAX_SOURCE_FILE_BYTES:
        raise BuildError(f"empty or oversized source file: {relative}")
    return resolved


def _contents(path: Path) -> list[str]:
    if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
        raise BuildError(f"source file exceeds size limit: {path.name}")
    return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()


def _surge(root: Path, source_file: str, fmt: str = "surge") -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    path = safe_source_file(root, source_file)
    records, unsupported = [], []
    hashes = {source_file: hashlib.sha256(path.read_bytes()).hexdigest()}
    lines = _contents(path)
    if not any(line.strip() and not line.lstrip().startswith(("#", ";")) for line in lines):
        raise BuildError(f"missing or empty source file: {source_file}")
    for line_no, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if fmt == "surge-domain-set":
            fields = ["DOMAIN-SUFFIX", line[1:]] if line.startswith(".") else ["DOMAIN", line]
        else:
            fields = [x.strip() for x in line.split(",")]
            if not fields[0].upper() in {"DOMAIN", "DOMAIN-SUFFIX"}:
                unsupported.append({"source_file": source_file, "line": line_no, "type": fields[0].upper(), "raw": line, "reason": "not an EXACT/SUFFIX domain rule"})
                continue
            if len(fields) != 2:
                unsupported.append({"source_file": source_file, "line": line_no, "type": fields[0].upper(), "raw": line, "reason": "unexpected extra fields"})
                continue
        try:
            domain = canonical_domain(fields[1])
        except ValueError as exc:
            unsupported.append({"source_file": source_file, "line": line_no, "type": fields[0].upper(), "raw": line, "reason": str(exc)})
            continue
        records.append({"domain": domain, "match_type": "EXACT" if fields[0].upper() == "DOMAIN" else "SUFFIX", "source_file": source_file, "line": line_no, "raw": line, "attributes": [], "include_chain": []})
    return records, hashes, unsupported


def _v2fly(root: Path, key: str) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    base = root.resolve()
    data_dir = base / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise BuildError("v2fly source is missing a real data directory")
    files = sorted(path for path in data_dir.rglob("*") if path.is_file())
    if len(files) > 10000:
        raise BuildError("v2fly source tree has too many data files")
    signature_rows = []
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(data_dir.resolve()):
            raise BuildError("symlink or escaping path in v2fly data tree")
        st = path.lstat()
        signature_rows.append((path.relative_to(base).as_posix(), st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino))
    cache_key = (str(base), key.upper(), tuple(signature_rows))
    if cache_key in _V2FLY_CACHE:
        cached_records, cached_hashes, cached_unsupported = _V2FLY_CACHE[cache_key]
        return [dict(row) for row in cached_records], dict(cached_hashes), [dict(row) for row in cached_unsupported]
    hashes: dict[str, str] = {}
    nodes: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(data_dir.resolve()):
            raise BuildError("symlink or escaping path in v2fly data tree")
        relative = path.relative_to(base).as_posix()
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            raise BuildError(f"v2fly file exceeds size limit: {relative}")
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        list_key = path.relative_to(data_dir).as_posix().upper()
        node = nodes.setdefault(list_key, {"entries": [], "includes": []})
        for line_no, raw in enumerate(_contents(path), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            head, fields = tokens[0], tokens[1:]
            if head.lower().startswith("include:"):
                include_key = head.split(":", 1)[1].upper()
                if not re.fullmatch(r"[A-Z0-9!-]+", include_key):
                    raise BuildError(f"invalid v2fly include in {relative}:{line_no}")
                must, banned = set(), set()
                for field in fields:
                    if not field.startswith("@") or field.startswith("&"):
                        raise BuildError(f"invalid v2fly include attribute in {relative}:{line_no}")
                    attr = field[1:].lower()
                    if not re.fullmatch(r"-?[a-z0-9!]+", attr):
                        raise BuildError(f"invalid v2fly include attribute in {relative}:{line_no}")
                    (banned if attr.startswith("-") else must).add(attr[1:] if attr.startswith("-") else attr)
                node["includes"].append({"key": include_key, "must": sorted(must), "ban": sorted(banned), "file": relative, "line": line_no})
                continue
            attrs, affiliations = [], []
            for field in fields:
                if field.startswith("@") and re.fullmatch(r"@[a-z0-9!]+", field.lower()):
                    attrs.append(field[1:].lower())
                elif field.startswith("&") and re.fullmatch(r"&[A-Za-z0-9!-]+", field):
                    affiliations.append(field[1:].upper())
                else:
                    raise BuildError(f"invalid v2fly rule attribute in {relative}:{line_no}")
            raw_domain = head
            rule_type = "SUFFIX"
            if head.lower().startswith("full:"):
                rule_type, raw_domain = "EXACT", head[5:]
            elif head.lower().startswith("domain:"):
                raw_domain = head[7:]
            elif ":" in head and head.split(":", 1)[0].lower() not in {"keyword", "regexp"}:
                raise BuildError(f"unknown v2fly rule syntax in {relative}:{line_no}")
            entry = {"raw": line, "value": head, "match_type": rule_type, "attributes": attrs, "affiliations": affiliations, "source_file": relative, "line": line_no, "type": head.split(":", 1)[0].upper() if ":" in head else "DOMAIN"}
            node["entries"].append(entry)
            for affiliation in affiliations:
                nodes.setdefault(affiliation, {"entries": [], "includes": []})["entries"].append(entry)

    root_key = key.upper()
    if root_key not in nodes:
        raise BuildError(f"v2fly source key or affiliation does not exist: {key}")
    unsupported: list[dict[str, Any]] = []

    def expand(current: str, chain: tuple[str, ...]) -> list[dict[str, Any]]:
        if len(chain) >= MAX_INCLUDE_DEPTH:
            raise BuildError("v2fly include depth exceeds limit")
        if current in chain:
            raise BuildError(f"v2fly include cycle: {' -> '.join((*chain, current))}")
        node = nodes.get(current)
        if node is None:
            raise BuildError(f"v2fly include target does not exist: {current}")
        output = []
        for entry in node["entries"]:
            row = {**entry, "include_chain": [*chain, current]}
            output.append(row)
        for include in node["includes"]:
            included = expand(include["key"], (*chain, current))
            for row in included:
                attrs = set(row["attributes"])
                if not set(include["must"]).issubset(attrs) or set(include["ban"]).intersection(attrs):
                    continue
                output.append(row)
            if len(output) > MAX_EXPANDED_ENTRIES:
                raise BuildError("v2fly include expansion exceeds entry limit")
        return output

    expanded = expand(root_key, ())
    records: list[dict[str, Any]] = []
    for row in expanded:
        if row["type"] in {"KEYWORD", "REGEXP"}:
            unsupported.append({"source_file": row["source_file"], "line": row["line"], "type": row["type"], "raw": row["raw"], "reason": "v2fly rule type is not a domain EXACT/SUFFIX"})
            continue
        try:
            value = row["value"]
            domain = canonical_domain(value[5:] if row["match_type"] == "EXACT" else value[7:] if value.lower().startswith("domain:") else value)
        except ValueError as exc:
            unsupported.append({"source_file": row["source_file"], "line": row["line"], "type": "DOMAIN", "raw": row["raw"], "reason": str(exc)})
            continue
        records.append({"domain": domain, "match_type": row["match_type"], "source_file": row["source_file"], "line": row["line"], "raw": row["raw"], "attributes": row["attributes"], "include_chain": row["include_chain"], "affiliations": row["affiliations"]})
    if not records and not unsupported:
        raise BuildError(f"v2fly source key is empty: {key}")
    result = (records, hashes, unsupported)
    _V2FLY_CACHE[cache_key] = result
    return [dict(row) for row in records], dict(hashes), [dict(row) for row in unsupported]


def parse_source(source_id: str, root: Path, source_file: str, source_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    fmt = source_cfg.get("format", "surge")
    if fmt in {"surge", "surge-domain-set"}:
        return _surge(root, source_file, fmt)
    if fmt == "v2fly-domain-list":
        return _v2fly(root, source_file)
    raise BuildError(f"source {source_id}: unsupported format {fmt!r}")


def v2fly_include_scope(root: Path, key: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Return the physical include closure and selector edges for one v2fly key.

    Domain-line changes do not alter the selector fingerprint. Include targets and
    their positive/negative attribute filters do, even when the resulting set of
    files happens to stay the same.
    """
    base = root.resolve()
    data_dir = base / "data"
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise BuildError("v2fly source is missing a real data directory")
    nodes: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(data_dir.resolve()):
            raise BuildError("symlink or escaping path in v2fly data tree")
        relative = path.relative_to(base).as_posix()
        list_key = path.relative_to(data_dir).as_posix().upper()
        includes: list[dict[str, Any]] = []
        for line_no, raw in enumerate(_contents(path), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            head, fields = tokens[0], tokens[1:]
            if not head.lower().startswith("include:"):
                continue
            target = head.split(":", 1)[1].upper()
            if not re.fullmatch(r"[A-Z0-9!-]+", target):
                raise BuildError(f"invalid v2fly include in {relative}:{line_no}")
            must, ban = set(), set()
            for field in fields:
                if not field.startswith("@") or field.startswith("&"):
                    raise BuildError(f"invalid v2fly include attribute in {relative}:{line_no}")
                attr = field[1:].lower()
                if not re.fullmatch(r"-?[a-z0-9!]+", attr):
                    raise BuildError(f"invalid v2fly include attribute in {relative}:{line_no}")
                (ban if attr.startswith("-") else must).add(attr[1:] if attr.startswith("-") else attr)
            includes.append({"target": target, "must": sorted(must), "ban": sorted(ban), "source_file": relative})
        nodes[list_key] = (relative, includes)

    root_key = key.upper()
    if root_key not in nodes:
        raise BuildError(f"v2fly source key or affiliation does not exist: {key}")
    visited: set[str] = set()
    active: set[str] = set()
    selectors: list[dict[str, Any]] = []

    def visit(current: str, depth: int) -> None:
        if depth >= MAX_INCLUDE_DEPTH:
            raise BuildError("v2fly include depth exceeds limit")
        if current in active:
            raise BuildError(f"v2fly include cycle while auditing scope: {current}")
        node = nodes.get(current)
        if node is None:
            raise BuildError(f"v2fly include target does not exist: {current}")
        if current in visited:
            return
        active.add(current)
        visited.add(current)
        for include in node[1]:
            selectors.append({"parent": current, **include})
            visit(include["target"], depth + 1)
        active.remove(current)

    visit(root_key, 0)
    files = sorted(nodes[name][0] for name in visited)
    return files, sorted(selectors, key=lambda item: (item["parent"], item["target"], item["must"], item["ban"], item["source_file"]))


def file_legal_declarations(root: Path, relative_files: list[str]) -> list[dict[str, str]]:
    """Return only explicit comment lines that assert licensing/copyright scope."""
    declarations = []
    for relative in sorted(set(relative_files)):
        path = safe_source_file(root, relative)
        for raw in _contents(path):
            stripped = raw.strip()
            if not stripped.startswith(("#", ";", "//", "/*", "*")):
                continue
            if _LEGAL_DECLARATION.search(stripped):
                text = " ".join(stripped.split())
                declarations.append({"file": relative, "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    return declarations
