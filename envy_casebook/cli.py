"""Command-line entry point. The Casebook window is the default.

Every write goes to a brand-new file; the Casebook never overwrites a source.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import __version__
from .core import (CasebookError, append, check_passphrase, create, export, inspect,
                   search, stats, template, timeline)


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="envy-casebook",
        description="Offline, encrypted, revision-preserving case notes. Run with no arguments to "
                    "open the Casebook window. Local only: no network, no accounts, no trusted timestamps.")
    root.add_argument("--version", action="version", version=f"envy-casebook {__version__}")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("gui", help="open the Casebook window (default)")

    new = commands.add_parser("new", help="create a new encrypted case")
    new.add_argument("destination", help="a new file for the encrypted case; never overwritten")
    new.add_argument("--title", required=True, help="the case title")

    add = commands.add_parser("add", help="append a JSON record and write a new encrypted revision")
    add.add_argument("source", help="the current encrypted case")
    add.add_argument("destination", help="a new file for the next revision; never overwritten")
    add.add_argument("--record", required=True, help="a JSON file holding one record")

    tmpl = commands.add_parser("template", help="write a blank record template to edit")
    tmpl.add_argument("destination", help="a new JSON file; never overwritten")
    tmpl.add_argument("--kind", default="note", choices=("note", "entity", "event", "link"))

    insp = commands.add_parser("inspect", help="print the decrypted records (read-only)")
    insp.add_argument("source")

    tl = commands.add_parser("timeline", help="print event records in UTC order (read-only)")
    tl.add_argument("source")

    se = commands.add_parser("search", help="full-text search over titles, bodies and provenance (read-only)")
    se.add_argument("source")
    se.add_argument("query", help="the text to search for (case-insensitive)")
    se.add_argument("--kind", action="append", choices=("note", "entity", "event", "link"),
                    help="limit to a record kind; repeat to allow several")

    stx = commands.add_parser("stats", help="print a summary of the case: counts, body totals, event span (read-only)")
    stx.add_argument("source")

    exp = commands.add_parser("export", help="write a plaintext HTML or JSON report")
    exp.add_argument("source")
    exp.add_argument("destination", help="a new report file; never overwritten")
    exp.add_argument("--format", default="html", choices=("html", "json", "md"))
    return root


def _passphrase(confirm: bool = False) -> bytes:
    if not sys.stdin.isatty():
        raise CasebookError("Open this action in a terminal so the passphrase is not echoed")
    value = getpass.getpass("Case passphrase: ").encode()
    if confirm and value != getpass.getpass("Repeat passphrase: ").encode():
        raise CasebookError("Passphrases do not match")
    return check_passphrase(value)


def _run(args) -> int:
    if args.command == "template":
        result = template(args.destination, args.kind)
        print(f"template: wrote {result['output']}")
        print(result["next"])
        return 0
    if args.command == "new":
        result = create(args.destination, args.title, _passphrase(confirm=True))
        print(f"new case: wrote {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "add":
        result = append(args.source, args.destination, args.record, _passphrase())
        print(f"added record {result['added_record_id']} → {result['output']} (revision {result['revision']})")
        return 0
    if args.command == "export":
        result = export(args.source, args.destination, _passphrase(), args.format)
        print(f"export ({result['format']}): wrote {result['output']}  sha256 {result['sha256']}")
        return 0
    if args.command == "inspect":
        print(json.dumps(inspect(args.source, _passphrase()), indent=2, ensure_ascii=False))
        return 0
    if args.command == "timeline":
        print(json.dumps(timeline(args.source, _passphrase()), indent=2, ensure_ascii=False))
        return 0
    if args.command == "search":
        result = search(args.source, _passphrase(), args.query, args.kind)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["matches"] else 1
    if args.command == "stats":
        print(json.dumps(stats(args.source, _passphrase()), indent=2, ensure_ascii=False))
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "gui"):
        from .gui import run_app
        return run_app()
    try:
        return _run(args)
    except CasebookError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
