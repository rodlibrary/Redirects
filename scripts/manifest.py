"""Shared helpers for reading and checking the redirect manifest.

The manifest is a CSV with one row per migrated item (or series):

    dc_path,dc_article,ark,preservica_ref,target_url,status,notes

  dc_path         landing-page key: "etd/123", a journal article "pias/vol94/iss4/3",
                  a journal issue or volume "pias/vol94/iss4" / "pias/vol94",
                  or a series/journal key such as "etd"
  dc_article      file key "context/article_number", e.g. "etd/1124"
  ark             "NAAN/name", e.g. "32498/x7k9q" (hyphens are ignored)
  preservica_ref  the Preservica reference: "IO_<uuid>" (Asset) or "SO_<uuid>" (Folder).
                  Also accepted, and normalized on reading: a bare UUID (taken as an
                  Asset), "sdb:IO|<uuid>" (plain or percent-encoded), or a full portal or
                  Universal Access address copied from the browser.
  target_url      full Preservica address. May be left blank when preservica_ref is
                  filled in: it is then built from PRESERVICA_TARGET_TEMPLATE.
  status          active | withdrawn | unmatched. An "unmatched" row that has a
                  preservica_ref is treated as active.
  notes           free text

Any of dc_path / dc_article / ark may be blank, but an active or withdrawn row
needs at least one of them. overrides.csv uses the same columns; its keys win.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

COLUMNS = ["dc_path", "dc_article", "ark", "preservica_ref", "target_url", "status", "notes"]
KEY_COLUMNS = ["dc_path", "dc_article", "ark"]
STATUSES = {"active", "withdrawn", "unmatched"}

# Map file name for each key column.
MAP_NAMES = {"dc_path": "dc_paths", "dc_article": "dc_article", "ark": "ark"}

GONE = "GONE"  # map value Apache turns into 410 Gone

# Series/journal names may contain hyphens (e.g. "journal-stem-arts").
CONTEXT = r"[a-z0-9_][a-z0-9_-]*"
DC_PATH_RE = re.compile(
    rf"^{CONTEXT}(?:/\d+|/vol[0-9a-z_]+(?:/iss[0-9a-z_]+(?:/\d+)?)?)?$"
)
DC_ARTICLE_RE = re.compile(rf"^{CONTEXT}/\d+$")
# NAAN (5+ alphanumerics) / name; no whitespace, '?', '#'. Slashes allowed for qualifiers.
ARK_RE = re.compile(r"^[0-9a-z]{5,}/[^\s/?#][^\s?#]*$")
PRESERVICA_REF_RE = re.compile(
    r"^(IO|SO)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# Where redirect links point. Overridden by the same-named environment variables,
# which .gitlab-ci.yml sets for every job.
DEFAULT_ACCESS_HOST = "uni.access.preservica.com"
DEFAULT_TARGET_TEMPLATE = "https://{access_host}/portal/en-US/{kind}/{sdb}"

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_REF_FORMS = [
    re.compile(rf"\b(io|so)_({UUID})\b"),          # IO_<uuid>, also inside a URL
    re.compile(rf"sdb:(io|so)\|({UUID})"),          # sdb:IO|<uuid> (after unquoting)
    re.compile(rf"/(asset|folder)/({UUID})\b"),     # portal address with a bare UUID
]


def normalize_ref(value: str) -> str:
    """Turn the forms people paste into "IO_<uuid>" / "SO_<uuid>".

    Returns the input unchanged when it can't be recognized, so validation can
    report it.
    """
    v = unquote(value.strip()).lower()
    if not v:
        return ""
    for rx in _REF_FORMS:
        m = rx.search(v)
        if m:
            kind = {"io": "IO", "so": "SO", "asset": "IO", "folder": "SO"}[m.group(1)]
            return f"{kind}_{m.group(2)}"
    if re.fullmatch(UUID, v):
        return f"IO_{v}"
    return value.strip()


def build_target(ref: str, template: str | None = None, access_host: str | None = None) -> str:
    """Fill the redirect-target template for a normalized reference ("IO_<uuid>")."""
    template = template or os.environ.get("PRESERVICA_TARGET_TEMPLATE") or DEFAULT_TARGET_TEMPLATE
    access_host = access_host or os.environ.get("PRESERVICA_ACCESS_HOST") or DEFAULT_ACCESS_HOST
    etype, uuid = ref.split("_", 1)
    return template.format(
        access_host=access_host,
        kind="asset" if etype == "IO" else "folder",
        type=etype,
        ref=uuid,
        sdb=quote(f"sdb:{etype}|{uuid}", safe=""),
    )


def normalize_ark(value: str) -> str:
    """Strip an optional 'ark:/' or 'ark:' prefix and the hyphens ARKs ignore."""
    v = value.strip()
    for prefix in ("ark:/", "ark:"):
        if v.lower().startswith(prefix):
            v = v[len(prefix):]
            break
    return v.replace("-", "").rstrip("/")


@dataclass
class Row:
    source: str
    line: int
    data: dict = field(default_factory=dict)

    def __getitem__(self, key):
        return self.data.get(key, "")

    @property
    def where(self) -> str:
        return f"{self.source}:{self.line}"


def read_manifest(path: str | Path) -> list[Row]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path}: missing column(s): {', '.join(missing)}")
        rows = []
        # Line 1 is the header, so data starts on line 2.
        for i, raw in enumerate(reader, start=2):
            data = {k: (raw.get(k) or "").strip() for k in COLUMNS}
            data["status"] = data["status"].lower() or "active"
            data["dc_path"] = data["dc_path"].strip("/").lower()
            data["dc_article"] = data["dc_article"].strip("/").lower()
            if data["ark"]:
                data["ark"] = normalize_ark(data["ark"])
            if data["preservica_ref"]:
                data["preservica_ref"] = normalize_ref(data["preservica_ref"])
                valid = bool(PRESERVICA_REF_RE.match(data["preservica_ref"]))
                # A reference filled in by hand makes the row live.
                if valid and data["status"] == "unmatched":
                    data["status"] = "active"
                if valid and not data["target_url"] and data["status"] == "active":
                    data["target_url"] = build_target(data["preservica_ref"])
            rows.append(Row(str(path), i, data))
        return rows


def check_rows(rows: list[Row], allowed_hosts: set[str], naan: str = "") -> list[str]:
    """Return a list of error strings for one file's rows."""
    errors: list[str] = []
    seen: dict[str, dict[str, str]] = {k: {} for k in KEY_COLUMNS}

    for r in rows:
        status = r["status"]
        if status not in STATUSES:
            errors.append(f"{r.where}: status '{status}' is not one of {sorted(STATUSES)}")
            continue

        keys = {k: r[k] for k in KEY_COLUMNS if r[k]}
        if status != "unmatched" and not keys:
            errors.append(f"{r.where}: row has no dc_path, dc_article or ark")

        if r["dc_path"] and not DC_PATH_RE.match(r["dc_path"]):
            errors.append(f"{r.where}: dc_path '{r['dc_path']}' should look like 'etd/123', 'pias/vol94/iss4/3' or 'etd'")
        if r["dc_article"] and not DC_ARTICLE_RE.match(r["dc_article"]):
            errors.append(f"{r.where}: dc_article '{r['dc_article']}' should look like 'etd/1124'")
        if r["ark"] and not ARK_RE.match(r["ark"]):
            errors.append(f"{r.where}: ark '{r['ark']}' should look like '32498/x7k9q'")
        if r["ark"] and naan and not r["ark"].startswith(f"{naan}/"):
            errors.append(f"{r.where}: ark '{r['ark']}' is not under our NAAN {naan}")
        if r["preservica_ref"] and not PRESERVICA_REF_RE.match(r["preservica_ref"]):
            errors.append(f"{r.where}: preservica_ref '{r['preservica_ref']}' should look like 'IO_<uuid>'")

        target = r["target_url"]
        if status == "active":
            if not target:
                errors.append(f"{r.where}: active row has no target_url or preservica_ref")
            else:
                errors.extend(check_target(r, target, allowed_hosts))
        elif target:
            errors.extend(check_target(r, target, allowed_hosts))

        if status == "unmatched":
            continue
        for k, v in keys.items():
            if v in seen[k]:
                errors.append(f"{r.where}: duplicate {k} '{v}' (first seen at {seen[k][v]})")
            else:
                seen[k][v] = r.where
    return errors


# Characters allowed in a URL (RFC 3986), with % only as part of %XX. Apache sends
# map targets out exactly as stored, so anything else must already be encoded
# (for example | as %7C).
URL_CHARS_RE = re.compile(r"^(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})+$")


def check_target(r: Row, target: str, allowed_hosts: set[str]) -> list[str]:
    errs = []
    if any(c.isspace() for c in target):
        errs.append(f"{r.where}: target_url contains whitespace")
    elif not URL_CHARS_RE.match(target):
        errs.append(f"{r.where}: target_url has characters that must be percent-encoded "
                    "(e.g. | as %7C) or a stray %")
    u = urlparse(target)
    if u.scheme != "https" or not u.netloc:
        errs.append(f"{r.where}: target_url must be an absolute https URL")
    elif allowed_hosts and u.hostname not in allowed_hosts:
        errs.append(
            f"{r.where}: target host '{u.hostname}' is not in the allowed list "
            f"({', '.join(sorted(allowed_hosts))})"
        )
    return errs


def build_maps(base: list[Row], overrides: list[Row]) -> dict[str, dict[str, str]]:
    """Merge base and override rows into {map_name: {key: value}}.

    Override rows replace base rows key by key. Unmatched rows are skipped.
    """
    maps: dict[str, dict[str, str]] = {name: {} for name in MAP_NAMES.values()}
    for rows in (base, overrides):
        for r in rows:
            if r["status"] == "unmatched":
                continue
            value = GONE if r["status"] == "withdrawn" else r["target_url"]
            for col, name in MAP_NAMES.items():
                if r[col]:
                    maps[name][r[col]] = value
    return maps


def parse_hosts(values: list[str] | None) -> set[str]:
    hosts: set[str] = set()
    for v in values or []:
        hosts.update(h.strip().lower() for h in v.split(",") if h.strip())
    return hosts
