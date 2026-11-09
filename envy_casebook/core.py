"""Encrypted, revision-preserving local case notes with explicit plaintext export.

A self-contained port of the ENVY BLACK VEIL Casebook engine. Everything is
local and offline: no remote collection, identity inference, or trusted
timestamps are provided. Encrypted casebooks use the same AES-256-GCM envelope
as ENVY Vault, so a file written here opens there and the other way around.

Standard library only, apart from ``cryptography`` for the encrypted envelope.
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import os
import re
import stat
import uuid
from pathlib import Path

# --- Shared errors and bounds ------------------------------------------------

MAGIC = b"ENVYVAULT2\x00"
MAX_ENVELOPE = 8 * 1024 * 1024
MAX_RECORD = 128 * 1024
MAX_RECORDS = 1000
MIN_PASSPHRASE = 12
KINDS = ("note", "entity", "event", "link")


class CasebookError(RuntimeError):
    """A clean, expected error: a bad passphrase, an invalid record, a chain mismatch."""


# --- Safe file primitives (never follow symlinks, never overwrite) -----------

def read_regular(path, limit: int = MAX_ENVELOPE) -> bytes:
    path = Path(path).expanduser()
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
            raise CasebookError("Expected a regular file within the size limit")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CasebookError("File exceeded the size limit")
    return data


def new_file(path, data: bytes, mode: int = 0o600) -> Path:
    """Write a brand-new file. O_EXCL means an existing file is never overwritten."""
    path = Path(path).expanduser()
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        raise CasebookError(f"Refusing to overwrite an existing file: {path}") from None
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return path


# --- Encrypted envelope (byte-compatible with ENVY Vault) --------------------

def _key(secret: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(secret)


def encrypt_bytes(data: bytes, secret: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(secret, salt)).encrypt(nonce, data, MAGIC)


def decrypt_bytes(data: bytes, secret: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 44:
        raise CasebookError("Not an ENVY encrypted envelope")
    blob = data[len(MAGIC):]
    try:
        return AESGCM(_key(secret, blob[:16])).decrypt(blob[16:28], blob[28:], MAGIC)
    except InvalidTag:
        raise CasebookError("Incorrect passphrase or modified encrypted data") from None


def check_passphrase(secret: bytes) -> bytes:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < MIN_PASSPHRASE:
        raise CasebookError(f"Use a passphrase of at least {MIN_PASSPHRASE} bytes")
    return bytes(secret)


# --- Canonical form and field validation -------------------------------------

def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _text(value, field, limit, required=True):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
        raise CasebookError("Invalid case record " + field)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise CasebookError("Case record " + field + " must contain valid Unicode text") from None
    return value


def _timestamp(value):
    _text(value, "observed_utc", 64)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CasebookError("Event observation time must be ISO 8601 with a timezone") from None
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        raise CasebookError("Event observation time must include UTC (+00:00 or Z)")
    return parsed.isoformat()


def _validate_record(value, previous_ids=()):
    if not isinstance(value, dict) or value.get("kind") not in KINDS:
        raise CasebookError("Case record kind must be note, entity, event, or link")
    allowed = {"kind", "title", "body", "observed_utc", "references", "provenance"}
    if set(value) - allowed:
        raise CasebookError("Unknown case record field")
    record = {"kind": value["kind"], "title": _text(value.get("title"), "title", 200),
              "body": _text(value.get("body", ""), "body", 32000, required=False)}
    if "observed_utc" in value:
        record["observed_utc"] = _timestamp(value["observed_utc"])
    elif record["kind"] == "event":
        raise CasebookError("An event needs its observation time in UTC")
    refs = value.get("references", [])
    if not isinstance(refs, list) or len(refs) > 40 or any(not isinstance(x, str) or x not in previous_ids for x in refs):
        raise CasebookError("References must name existing record IDs in this case")
    if len(set(refs)) != len(refs):
        raise CasebookError("Duplicate case record reference")
    if record["kind"] == "link" and len(refs) != 2:
        raise CasebookError("A link must reference exactly two existing records")
    record["references"] = refs
    provenance = value.get("provenance", [])
    if not isinstance(provenance, list) or len(provenance) > 20:
        raise CasebookError("At most 20 provenance sources are allowed per record")
    record["provenance"] = []
    for source in provenance:
        if not isinstance(source, dict) or set(source) - {"source", "sha256", "note"}:
            raise CasebookError("Invalid provenance source")
        item = {"source": _text(source.get("source"), "source", 2048),
                "note": _text(source.get("note", ""), "provenance note", 4000, required=False)}
        if "sha256" in source:
            if not isinstance(source["sha256"], str) or not re.fullmatch("[0-9a-fA-F]{64}", source["sha256"]):
                raise CasebookError("Provenance SHA-256 must contain 64 hexadecimal characters")
            item["sha256"] = source["sha256"].lower()
        record["provenance"].append(item)
    return record


def _validate_case(data):
    fields = {"schema", "case_id", "title", "created_utc", "revision", "previous_envelope_sha256", "records"}
    if not isinstance(data, dict) or set(data) != fields or type(data["schema"]) is not int or data["schema"] != 1:
        raise CasebookError("Unsupported encrypted casebook schema")
    _text(data["title"], "case title", 200)
    try:
        uuid.UUID(data["case_id"])
    except (ValueError, TypeError, AttributeError):
        raise CasebookError("Invalid case identifier") from None
    _timestamp(data["created_utc"])
    records = data["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS or type(data["revision"]) is not int or data["revision"] != len(records):
        raise CasebookError("Invalid case revision or record count")
    if not isinstance(data["previous_envelope_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", data["previous_envelope_sha256"]):
        raise CasebookError("Invalid previous encrypted revision digest")
    previous, ids = "0" * 64, set()
    for record in records:
        meta = {"id", "recorded_utc", "previous_sha256", "record_sha256"}
        if not isinstance(record, dict) or not meta <= set(record):
            raise CasebookError("Invalid journal metadata")
        try:
            uuid.UUID(record["id"])
        except (ValueError, TypeError, AttributeError):
            raise CasebookError("Invalid record identifier") from None
        if record["id"] in ids:
            raise CasebookError("Duplicate record identifier")
        _timestamp(record["recorded_utc"])
        content = {k: v for k, v in record.items() if k not in meta}
        normalized = _validate_record(content, ids)
        if normalized != content:
            raise CasebookError("Encrypted case record is incomplete or not normalized")
        core = {k: v for k, v in record.items() if k != "record_sha256"}
        if record["previous_sha256"] != previous or hashlib.sha256(_canonical(core)).hexdigest() != record["record_sha256"]:
            raise CasebookError("Case journal hash chain mismatch")
        previous = record["record_sha256"]
        ids.add(record["id"])
    return data


# --- Load / write encrypted revisions ---------------------------------------

def _load(source, secret):
    encrypted = read_regular(source, MAX_ENVELOPE)
    try:
        data = json.loads(decrypt_bytes(encrypted, secret))
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        raise CasebookError("Encrypted casebook does not contain valid JSON") from None
    return _validate_case(data), hashlib.sha256(encrypted).hexdigest()


def _write(destination, data, secret):
    check_passphrase(secret)
    encoded = _canonical(_validate_case(data))
    if len(encoded) + 128 > MAX_ENVELOPE:
        raise CasebookError("Casebook exceeds the 8 MiB encrypted revision limit")
    new_file(destination, encrypt_bytes(encoded, secret))
    return {"output": str(destination), "case_id": data["case_id"], "revision": data["revision"],
            "records": len(data["records"]), "encrypted": True, "previous_revision_preserved": True}


def create(destination, title, secret):
    return _write(destination, {"schema": 1, "case_id": str(uuid.uuid4()),
                  "title": _text(title, "case title", 200), "created_utc": _utc(),
                  "revision": 0, "previous_envelope_sha256": "0" * 64, "records": []}, secret)


def template(destination, kind="note"):
    if kind not in KINDS:
        raise CasebookError("Unknown case record kind")
    value = {"kind": kind, "title": "Replace with a title", "body": "Replace with your observation.",
             "references": [], "provenance": [{"source": "Replace with source path, case object ID, or URL",
                                               "note": "Describe how you obtained it"}]}
    if kind == "event":
        value["observed_utc"] = _utc()
    if kind == "link":
        value["references"] = ["REPLACE-WITH-FIRST-RECORD-ID", "REPLACE-WITH-SECOND-RECORD-ID"]
    new_file(destination, (json.dumps(value, indent=2) + "\n").encode("utf-8"))
    return {"output": str(destination), "plaintext": True,
            "next": "Edit the template locally, import it into a new encrypted revision, then handle "
                    "the plaintext working file according to your storage policy."}


def append(source, destination, record_source, secret):
    try:
        value = json.loads(read_regular(record_source, MAX_RECORD))
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        raise CasebookError("Case record must contain valid UTF-8 JSON") from None
    return append_record(source, destination, value, secret)


def append_record(source, destination, value, secret):
    """Append a record and write a new encrypted revision, without a plaintext working file."""
    data, previous_envelope = _load(source, secret)
    if len(data["records"]) >= MAX_RECORDS:
        raise CasebookError("Casebook has reached its 1000 record limit")
    record = _validate_record(value, {r["id"] for r in data["records"]})
    record.update(id=str(uuid.uuid4()), recorded_utc=_utc(),
                  previous_sha256=data["records"][-1]["record_sha256"] if data["records"] else "0" * 64)
    record["record_sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
    data["records"].append(record)
    data["revision"] += 1
    data["previous_envelope_sha256"] = previous_envelope
    result = _write(destination, data, secret)
    return dict(result, added_record_id=record["id"], input_preserved=True)


def inspect(source, secret):
    data, envelope_hash = _load(source, secret)
    return {"case_id": data["case_id"], "title": data["title"], "revision": data["revision"],
            "created_utc": data["created_utc"],
            "envelope_sha256": envelope_hash, "previous_envelope_sha256": data["previous_envelope_sha256"],
            "records": [{"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r.get("body", ""),
                         "observed_utc": r.get("observed_utc"), "references": r["references"],
                         "provenance": r["provenance"], "record_sha256": r["record_sha256"]}
                        for r in data["records"]],
            "warning": "Record contents are decrypted for this display. Local timestamps and "
                       "provenance are operator assertions, not trusted facts."}


def timeline(source, secret):
    data, _ = _load(source, secret)
    records = [r for r in data["records"] if r["kind"] == "event"]
    records.sort(key=lambda r: (r["observed_utc"], r["id"]))
    return {"case_id": data["case_id"], "title": data["title"],
            "events": [{"id": r["id"], "title": r["title"], "observed_utc": r["observed_utc"],
                        "references": r["references"], "provenance": r["provenance"]}
                       for r in records],
            "time_basis": "Operator-provided UTC observation times, not trusted timestamps."}


def _snippet(record, needle, width=80):
    """A short window of context around the first title/body match, or empty."""
    for text in (record["title"], record.get("body", "")):
        low = text.lower()
        i = low.find(needle)
        if i >= 0:
            start = max(0, i - width // 2)
            end = min(len(text), i + len(needle) + width // 2)
            piece = " ".join(text[start:end].split())
            return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")
    return ""


def search(source, secret, query, kinds=None):
    """Case-insensitive substring search over record titles, bodies and provenance. Read-only.

    It ranks nothing and infers no relationships; it only reports which records
    contain the query text and where.
    """
    if not isinstance(query, str) or not query.strip():
        raise CasebookError("Search needs a non-empty query")
    needle = query.strip().lower()
    if kinds is not None:
        kinds = tuple(kinds)
        if any(k not in KINDS for k in kinds):
            raise CasebookError("Search kind must be note, entity, event, or link")
    data, _ = _load(source, secret)
    results = []
    for r in data["records"]:
        if kinds and r["kind"] not in kinds:
            continue
        matched = []
        if needle in r["title"].lower():
            matched.append("title")
        if needle in r.get("body", "").lower():
            matched.append("body")
        if any(needle in s["source"].lower() or needle in s.get("note", "").lower()
               for s in r["provenance"]):
            matched.append("provenance")
        if matched:
            results.append({"id": r["id"], "kind": r["kind"], "title": r["title"],
                            "observed_utc": r.get("observed_utc"), "matched_in": matched,
                            "snippet": _snippet(r, needle)})
    return {"case_id": data["case_id"], "title": data["title"], "query": query,
            "kinds": list(kinds) if kinds else None, "matches": len(results), "records": results,
            "note": "Substring match over decrypted record text. It ranks nothing, infers no "
                    "relationships, and record contents are operator assertions, not trusted facts."}


def stats(source, secret):
    """A read-only summary of the case: record counts by kind, body text totals,
    provenance sources, references, and the span of event observation times."""
    data, _ = _load(source, secret)
    records = data["records"]
    by_kind = {k: 0 for k in KINDS}
    body_chars = body_words = provenance = references = 0
    event_times = []
    for r in records:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        body = r.get("body", "")
        body_chars += len(body)
        body_words += len(body.split())
        provenance += len(r["provenance"])
        references += len(r["references"])
        if r["kind"] == "event" and r.get("observed_utc"):
            event_times.append(r["observed_utc"])
    event_times.sort()
    return {"case_id": data["case_id"], "title": data["title"], "revision": data["revision"],
            "records": len(records), "by_kind": by_kind,
            "body_chars": body_chars, "body_words": body_words,
            "provenance_sources": provenance, "references": references,
            "events": {"count": len(event_times),
                       "earliest_utc": event_times[0] if event_times else None,
                       "latest_utc": event_times[-1] if event_times else None},
            "note": "Counts only. Observation times and provenance are operator assertions, not trusted facts."}


def graph_data(data, limit=160):
    """A bounded display model for asserted record links; it infers no relationships."""
    _validate_case(data)
    if type(limit) is not int or not 1 <= limit <= 160:
        raise CasebookError("Display at most 160 case records at a time")
    shown = data["records"][:limit]
    ids = {r["id"] for r in shown}
    nodes = [{"id": r["id"], "kind": r["kind"], "title": r["title"]} for r in shown]
    edges, omitted_edges = [], 0

    def add_edge(edge):
        nonlocal omitted_edges
        if len(edges) < 600:
            edges.append(edge)
        else:
            omitted_edges += 1

    for record in data["records"]:
        for reference in record["references"]:
            if record["id"] in ids and reference in ids:
                add_edge({"source": record["id"], "target": reference, "kind": "reference", "label": "references"})
            else:
                omitted_edges += 1
        if record["kind"] == "link":
            first, second = record["references"]
            if first in ids and second in ids and record["id"] in ids:
                add_edge({"source": first, "target": second, "kind": "relationship",
                          "label": record["title"], "record_id": record["id"]})
            else:
                omitted_edges += 1
    return {"nodes": nodes, "edges": edges, "omitted_nodes": max(0, len(data["records"]) - limit),
            "omitted_edges": omitted_edges,
            "basis": "Only links and references explicitly entered in this case are displayed."}


def graph_from(source, secret, limit=160):
    data, _ = _load(source, secret)
    return graph_data(data, limit)


def _md_fence(text):
    """A backtick fence longer than any run of backticks in the text, so a body is
    reproduced verbatim and cannot break out into Markdown."""
    run = longest = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def export(source, destination, secret, format="html"):
    if format not in ("html", "json", "md"):
        raise CasebookError("Export format must be html, json or md")
    data, envelope_hash = _load(source, secret)
    report = {"casebook": data, "envelope_sha256": envelope_hash,
              "limitations": "Plaintext export. Local timestamps, operator assertions, and hash links do "
                             "not independently establish provenance, truth, or a trusted time. Sign and "
                             "retain a separate report checkpoint."}
    if format == "json":
        output = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    elif format == "md":
        lines = [f"# {data['title']}", "", report["limitations"], "",
                 f"- **Case:** `{data['case_id']}`",
                 f"- **Revision:** {data['revision']}",
                 f"- **Encrypted source SHA-256:** `{envelope_hash}`", ""]
        if not data["records"]:
            lines.append("_No records._")
        for record in data["records"]:
            lines.append(f"## {record['title']}")
            meta = f"`{record['kind']}` · `{record['id']}`"
            if record.get("observed_utc"):
                meta += f" · observed {record['observed_utc']}"
            lines += [meta, ""]
            body = record.get("body", "")
            if body:
                fence = _md_fence(body)
                lines += [fence, body, fence, ""]
            if record["references"]:
                lines.append("**References:** " + ", ".join(f"`{ref}`" for ref in record["references"]))
            if record["provenance"]:
                lines.append("**Provenance:**")
                for item in record["provenance"]:
                    entry = f"- {item['source']}"
                    if item.get("note"):
                        entry += f" — {item['note']}"
                    if item.get("sha256"):
                        entry += f" (SHA-256: `{item['sha256']}`)"
                    lines.append(entry)
            lines += ["", f"_Record SHA-256:_ `{record['record_sha256']}`", ""]
        output = ("\n".join(lines) + "\n").encode("utf-8")
    else:
        def esc(value):
            return html.escape(str(value), quote=True)
        records = []
        for record in data["records"]:
            refs = ", ".join(record["references"])
            sources = "".join("<li>" + esc(item["source"]) + " — " + esc(item["note"]) +
                              ("<br>SHA-256: " + esc(item["sha256"]) if "sha256" in item else "") + "</li>"
                              for item in record["provenance"])
            records.append("<article><h2>" + esc(record["title"]) + "</h2><p>" + esc(record["kind"]) +
                           " · " + esc(record["id"]) + "</p><p>Observed UTC: " + esc(record.get("observed_utc", "Not recorded")) +
                           "</p><pre>" + esc(record["body"]) + "</pre><p>References: " + esc(refs) +
                           "</p><ul>" + sources + "</ul><p>Record SHA-256: " + esc(record["record_sha256"]) + "</p></article>")
        # No external content, executable script, clickable operator-supplied URLs, or active HTML.
        output = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
                  "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                  "<title>" + esc(data["title"]) + "</title><style>body{max-width:64rem;margin:2rem auto;padding:0 1rem;font:16px system-ui;line-height:1.5}pre{white-space:pre-wrap;overflow-wrap:anywhere}article{border-top:1px solid #777;padding:1rem 0}</style>"
                  "</head><body><h1>" + esc(data["title"]) + "</h1><p>" + esc(report["limitations"]) +
                  "</p><p>Case: " + esc(data["case_id"]) + " · Revision: " + str(data["revision"]) +
                  "</p><p>Encrypted source SHA-256: " + envelope_hash + "</p>" + "".join(records) + "</body></html>").encode("utf-8")
    new_file(destination, output)
    return {"output": str(destination), "format": format, "plaintext": True,
            "sha256": hashlib.sha256(output).hexdigest(), "records": len(data["records"])}
