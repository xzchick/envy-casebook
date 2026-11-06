"""CLI tests that need no display. The passphrase prompt is patched."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from envy_casebook import cli


class CLITest(unittest.TestCase):
    SECRET = b"a long private case passphrase"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="envy-casebook-cli-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(cli, "_passphrase", return_value=self.SECRET):
            code = cli.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("envy-casebook", out.getvalue())

    def test_new_add_inspect_flow(self):
        case = self.dir / "case.ecase"
        code, out, _ = self.invoke("new", case, "--title", "Night shift")
        self.assertEqual(code, 0)
        self.assertTrue(case.exists())

        record = self.dir / "rec.json"
        record.write_text(json.dumps({"kind": "note", "title": "First sighting", "body": "a light in the dark"}))
        r1 = self.dir / "case.r1.ecase"
        code, out, _ = self.invoke("add", case, r1, "--record", record)
        self.assertEqual(code, 0)
        self.assertTrue(r1.exists())
        self.assertTrue(case.exists(), "previous revision preserved")

        code, out, _ = self.invoke("inspect", r1)
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["revision"], 1)
        self.assertEqual(report["records"][0]["title"], "First sighting")

    def test_export_writes_report(self):
        case = self.dir / "c.ecase"
        self.invoke("new", case, "--title", "Case")
        out_html = self.dir / "r.html"
        code, out, _ = self.invoke("export", case, out_html, "--format", "html")
        self.assertEqual(code, 0)
        self.assertIn("sha256", out)
        self.assertTrue(out_html.exists())

    def test_template_command(self):
        dest = self.dir / "t.json"
        code, out, _ = self.invoke("template", dest, "--kind", "link")
        self.assertEqual(code, 0)
        value = json.loads(dest.read_text())
        self.assertEqual(value["kind"], "link")

    def test_bad_passphrase_prefix_is_error(self):
        case = self.dir / "c2.ecase"
        self.invoke("new", case, "--title", "Case")
        with mock.patch.object(cli, "_passphrase", return_value=b"the wrong passphrase!"):
            code, _, err = self.invoke_raw("inspect", case)
        self.assertEqual(code, 2)
        self.assertIn("passphrase", err.lower())

    def invoke_raw(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def test_unknown_command(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["seance"])


if __name__ == "__main__":
    unittest.main()
