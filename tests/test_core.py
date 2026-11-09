"""Core casebook tests: encrypted round trips, the journal hash chain,
links, export and the no-overwrite guarantee. Standard library + cryptography.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from envy_casebook import core
from envy_casebook.core import CasebookError


class CaseTest(unittest.TestCase):
    SECRET = b"a long private case passphrase"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="envy-casebook-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir)

    def new_case(self, name="r0", title="Case"):
        path = self.dir / name
        core.create(path, title, self.SECRET)
        return path

    # ----- envelope -----------------------------------------------------
    def test_encrypted_round_trip_and_magic(self):
        blob = core.encrypt_bytes(b"secret notes", self.SECRET)
        self.assertTrue(blob.startswith(core.MAGIC))
        self.assertEqual(core.decrypt_bytes(blob, self.SECRET), b"secret notes")

    def test_wrong_passphrase_is_a_clean_error(self):
        blob = core.encrypt_bytes(b"x" * 40, self.SECRET)
        with self.assertRaises(CasebookError):
            core.decrypt_bytes(blob, b"the wrong passphrase!!")

    def test_short_passphrase_rejected(self):
        with self.assertRaises(CasebookError):
            core.create(self.dir / "short", "Case", b"tooshort")

    def test_not_an_envelope_rejected(self):
        with self.assertRaises(CasebookError):
            core.decrypt_bytes(b"not an ENVY envelope at all....", self.SECRET)

    # ----- create / load ------------------------------------------------
    def test_create_then_load_is_empty_revision_zero(self):
        path = self.new_case(title="Night shift")
        data, envelope = core._load(path, self.SECRET)
        self.assertEqual(data["revision"], 0)
        self.assertEqual(data["title"], "Night shift")
        self.assertEqual(data["records"], [])
        self.assertEqual(len(envelope), 64)

    # ----- append / hash chain -----------------------------------------
    def test_append_chains_and_increments_revision(self):
        path = self.new_case()
        r1 = self.dir / "r1"
        res = core.append_record(path, r1, {"kind": "note", "title": "First", "body": "seen"}, self.SECRET)
        self.assertEqual(res["revision"], 1)
        self.assertTrue(res["input_preserved"])
        self.assertTrue(path.exists(), "the previous revision must be preserved")
        r2 = self.dir / "r2"
        res2 = core.append_record(r1, r2, {"kind": "note", "title": "Second"}, self.SECRET)
        self.assertEqual(res2["revision"], 2)
        data, _ = core._load(r2, self.SECRET)
        # Hash chain: each record's previous_sha256 is the prior record's record_sha256.
        self.assertEqual(data["records"][1]["previous_sha256"], data["records"][0]["record_sha256"])

    def test_tampered_record_breaks_the_chain(self):
        path = self.new_case()
        r1 = self.dir / "r1"
        core.append_record(path, r1, {"kind": "note", "title": "Real"}, self.SECRET)
        data, _ = core._load(r1, self.SECRET)
        data["records"][0]["title"] = "Forged"  # changed without re-hashing
        forged = self.dir / "forged"
        forged.write_bytes(core.encrypt_bytes(core._canonical(data), self.SECRET))
        with self.assertRaises(CasebookError):
            core.inspect(forged, self.SECRET)

    def test_event_requires_utc_and_link_requires_two_refs(self):
        path = self.new_case()
        with self.assertRaises(CasebookError):
            core.append_record(path, self.dir / "e", {"kind": "event", "title": "No time"}, self.SECRET)
        with self.assertRaises(CasebookError):
            core.append_record(path, self.dir / "l", {"kind": "link", "title": "Bad", "references": []}, self.SECRET)

    def test_reference_must_name_an_existing_record(self):
        path = self.new_case()
        with self.assertRaises(CasebookError):
            core.append_record(path, self.dir / "x",
                               {"kind": "note", "title": "Dangling", "references": ["not-an-id"]}, self.SECRET)

    # ----- graph --------------------------------------------------------
    def _case_with_link(self):
        p0 = self.new_case()
        p1 = self.dir / "r1"
        a = core.append_record(p0, p1, {"kind": "entity", "title": "Host A"}, self.SECRET)["added_record_id"]
        p2 = self.dir / "r2"
        b = core.append_record(p1, p2, {"kind": "entity", "title": "Disk B"}, self.SECRET)["added_record_id"]
        p3 = self.dir / "r3"
        core.append_record(p2, p3, {"kind": "link", "title": "Attached to", "references": [a, b]}, self.SECRET)
        data, _ = core._load(p3, self.SECRET)
        return data, a, b

    def test_graph_shows_only_entered_links(self):
        data, a, b = self._case_with_link()
        graph = core.graph_data(data)
        self.assertEqual(len(graph["nodes"]), 3)
        edge = next(x for x in graph["edges"] if x["kind"] == "relationship")
        self.assertEqual((edge["source"], edge["target"]), (a, b))
        self.assertEqual(graph["omitted_nodes"], 0)
        self.assertEqual(core.graph_data(data, limit=1)["omitted_nodes"], 2)

    def test_graph_limit_is_validated(self):
        data, _, _ = self._case_with_link()
        for bad in (True, 0, 161):
            with self.assertRaises(CasebookError):
                core.graph_data(data, bad)

    # ----- export -------------------------------------------------------
    def test_export_html_and_json_are_plaintext_and_safe(self):
        data, a, b = self._case_with_link()
        src = self.dir / "r3"
        html_out = self.dir / "report.html"
        res = core.export(src, html_out, self.SECRET, "html")
        text = html_out.read_text()
        self.assertEqual(res["format"], "html")
        self.assertIn("Content-Security-Policy", text)
        self.assertIn("Host A", text)
        json_out = self.dir / "report.json"
        core.export(src, json_out, self.SECRET, "json")
        report = json.loads(json_out.read_text())
        self.assertEqual(report["casebook"]["revision"], 3)
        self.assertIn("limitations", report)

    def test_export_html_escapes_markup_in_titles(self):
        p0 = self.new_case()
        p1 = self.dir / "r1"
        core.append_record(p0, p1, {"kind": "entity", "title": "Host <script>"}, self.SECRET)
        out = self.dir / "r.html"
        core.export(p1, out, self.SECRET, "html")
        body = out.read_text()
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    # ----- no overwrite -------------------------------------------------
    def test_new_file_never_overwrites(self):
        keep = self.dir / "keep"
        keep.write_bytes(b"precious")
        with self.assertRaises(CasebookError):
            core.new_file(keep, b"clobber")
        self.assertEqual(keep.read_bytes(), b"precious")

    def test_append_refuses_to_overwrite_destination(self):
        path = self.new_case()
        dest = self.dir / "exists"
        dest.write_bytes(b"keep me")
        with self.assertRaises(CasebookError):
            core.append_record(path, dest, {"kind": "note", "title": "n"}, self.SECRET)
        self.assertEqual(dest.read_bytes(), b"keep me")

    # ----- inspect / timeline ------------------------------------------
    def test_timeline_orders_events_by_observed_utc(self):
        p0 = self.new_case()
        p1 = self.dir / "r1"
        core.append_record(p0, p1, {"kind": "event", "title": "Later", "observed_utc": "2026-02-01T00:00:00+00:00"}, self.SECRET)
        p2 = self.dir / "r2"
        core.append_record(p1, p2, {"kind": "event", "title": "Earlier", "observed_utc": "2026-01-01T00:00:00+00:00"}, self.SECRET)
        tl = core.timeline(p2, self.SECRET)
        self.assertEqual([e["title"] for e in tl["events"]], ["Earlier", "Later"])

    def test_template_writes_editable_json(self):
        out = self.dir / "tmpl.json"
        core.template(out, "event")
        value = json.loads(out.read_text())
        self.assertEqual(value["kind"], "event")
        self.assertIn("observed_utc", value)

    # ----- search -------------------------------------------------------
    def _searchable_case(self):
        p0 = self.new_case()
        p1 = self.dir / "s1"
        core.append_record(p0, p1, {"kind": "entity", "title": "Raven laptop",
                                    "body": "A silver machine seized at the scene"}, self.SECRET)
        p2 = self.dir / "s2"
        core.append_record(p1, p2, {"kind": "note", "title": "Interview",
                                    "body": "Subject mentioned a USB drive",
                                    "provenance": [{"source": "/evidence/raven-notes.txt",
                                                    "note": "handwritten"}]}, self.SECRET)
        return p2

    def test_search_matches_title_body_and_provenance(self):
        case = self._searchable_case()
        res = core.search(case, self.SECRET, "raven")
        self.assertEqual(res["matches"], 2)
        by_kind = {r["kind"]: r for r in res["records"]}
        self.assertIn("title", by_kind["entity"]["matched_in"])
        self.assertIn("provenance", by_kind["note"]["matched_in"])
        usb = core.search(case, self.SECRET, "usb drive")
        self.assertEqual(usb["matches"], 1)
        self.assertIn("body", usb["records"][0]["matched_in"])
        self.assertIn("USB drive", usb["records"][0]["snippet"])

    def test_search_is_case_insensitive_and_can_filter_kind(self):
        case = self._searchable_case()
        self.assertEqual(core.search(case, self.SECRET, "INTERVIEW")["matches"], 1)
        only_entity = core.search(case, self.SECRET, "raven", kinds=["entity"])
        self.assertEqual(only_entity["matches"], 1)
        self.assertEqual(only_entity["records"][0]["kind"], "entity")

    def test_search_rejects_empty_query_and_unknown_kind(self):
        case = self._searchable_case()
        with self.assertRaises(core.CasebookError):
            core.search(case, self.SECRET, "   ")
        with self.assertRaises(core.CasebookError):
            core.search(case, self.SECRET, "raven", kinds=["bogus"])
        self.assertEqual(core.search(case, self.SECRET, "nothing-here")["matches"], 0)


    # ----- stats (v0.2) -------------------------------------------------
    def test_stats_summarizes_the_case(self):
        p0 = self.new_case()
        p1 = self.dir / "s1"
        core.append_record(p0, p1, {"kind": "entity", "title": "Host A", "body": "two words"}, self.SECRET)
        p2 = self.dir / "s2"
        core.append_record(p1, p2, {"kind": "event", "title": "Boot", "observed_utc": "2026-01-05T00:00:00+00:00",
                                    "provenance": [{"source": "/var/log/boot"}]}, self.SECRET)
        p3 = self.dir / "s3"
        core.append_record(p2, p3, {"kind": "event", "title": "Earlier",
                                    "observed_utc": "2026-01-01T00:00:00+00:00"}, self.SECRET)
        s = core.stats(p3, self.SECRET)
        self.assertEqual(s["records"], 3)
        self.assertEqual(s["by_kind"]["event"], 2)
        self.assertEqual(s["by_kind"]["entity"], 1)
        self.assertEqual(s["body_words"], 2)
        self.assertEqual(s["provenance_sources"], 1)
        self.assertEqual(s["events"]["count"], 2)
        self.assertTrue(s["events"]["earliest_utc"].startswith("2026-01-01"))
        self.assertTrue(s["events"]["latest_utc"].startswith("2026-01-05"))

    def test_stats_empty_case(self):
        s = core.stats(self.new_case(), self.SECRET)
        self.assertEqual(s["records"], 0)
        self.assertIsNone(s["events"]["earliest_utc"])
        self.assertEqual(sum(s["by_kind"].values()), 0)


    # ----- Markdown export (v0.2) --------------------------------------
    def test_export_markdown(self):
        p0 = self.new_case(title="Night shift")
        p1 = self.dir / "r1"
        core.append_record(p0, p1, {"kind": "note", "title": "First note",
                                    "body": "a body with ``` fenced text inside",
                                    "provenance": [{"source": "/evidence/x", "note": "seized"}]}, self.SECRET)
        out = self.dir / "r.md"
        res = core.export(p1, out, self.SECRET, "md")
        self.assertEqual(res["format"], "md")
        text = out.read_text()
        self.assertTrue(text.startswith("# Night shift"))
        self.assertIn("## First note", text)
        self.assertIn("`note`", text)
        self.assertIn("a body with ``` fenced text inside", text)   # reproduced verbatim
        self.assertIn("````", text)                                 # fence longer than the inner run
        self.assertIn("/evidence/x", text)

    def test_export_markdown_empty_case(self):
        out = self.dir / "e.md"
        core.export(self.new_case(), out, self.SECRET, "md")
        self.assertIn("_No records._", out.read_text())


if __name__ == "__main__":
    unittest.main()
