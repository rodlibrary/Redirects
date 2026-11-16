"""Tests for ark_manifest.py, against a stand-in for Preservica running on this machine.
Run with:  python3 -m unittest discover -s arks"""

import contextlib
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ark_manifest as am  # noqa: E402

U = ["11111111-1111-4111-8111-11111111111%d" % i for i in range(10)]


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def write(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def run(*args, env=None):
    out, err = io.StringIO(), io.StringIO()
    old_argv, old_env = sys.argv, dict(os.environ)
    sys.argv = ["ark_manifest.py", *args]
    os.environ.update(env or {})
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = am.main()
            except SystemExit as exc:
                code = exc.code
                if isinstance(code, str):  # sys.exit("message") prints it to stderr and exits 1
                    err.write(code + "\n")
                    code = 1
    finally:
        sys.argv = old_argv
        os.environ.clear()
        os.environ.update(old_env)
    return code, out.getvalue() + err.getvalue()


class ArkKeys(unittest.TestCase):
    def test_forms(self):
        for text in ("ark:/32498/x7k9q", "ark:32498/x7-k9q", "https://n2t.net/ark:/32498/x7k9q",
                     "32498/x7k9q", "ARK held as ark:/32498/x7k9q."):
            self.assertEqual(am.ark_key(text), "32498/x7k9q", text)
        self.assertEqual(am.ark_key("no ark here"), "")
        self.assertEqual(am.ark_key("ark:/12345/abc", "32498"), "")

    def test_arks_in_values(self):
        found = am.arks_in(["ark:/32498/aaa", "https://n2t.net/ark:/32498/b-bb", "ark:/99999/zzz", "x"], "32498")
        self.assertEqual(found, {"32498/aaa", "32498/bbb"})

    def test_refs(self):
        self.assertEqual(am.normalize_ref(U[0]), f"IO_{U[0]}")
        self.assertEqual(am.normalize_ref(f"sdb:SO|{U[1]}"), f"SO_{U[1]}")
        self.assertEqual(am.normalize_ref(f"https://x/portal/en-US/asset/sdb%3AIO%7C{U[2]}"), f"IO_{U[2]}")


class FakePreservica(BaseHTTPRequestHandler):
    """Login plus /api/content/search over RECORDS, paging 2 at a time."""
    RECORDS = []
    VISIBLE = True
    searches = []

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if self.path == "/api/accesstoken/login":
            if form.get("tenant") != ["UNIOWA"]:
                return self.reply(401, {"success": False, "message": "invalid.user incorrect.tenant"})
            return self.reply(200, {"token": "T"})
        q = json.loads(form["q"][0])
        FakePreservica.searches.append(q)
        assert q["q"] == "*", "the search must use '*', not '%'"
        recs = self.RECORDS if self.VISIBLE else []
        field = next((f["name"] for f in q.get("fields", []) if f["values"] == ["*"]), None)
        if field:
            recs = [r for r in recs if r.get(field)]
        start, size = int(form["start"][0]), min(int(form["max"][0]), 2)
        page = recs[start:start + size]
        return self.reply(200, {"value": {
            "totalHits": len(recs), "objectIds": [f"sdb:{r['type']}|{r['uuid']}" for r in page],
            "metadata": [[{"name": k, "value": v} for k, v in r.items() if "." in k] for r in page]}})


class Match(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.manifest = os.path.join(self.dir, "arks_manifest.csv")
        self.master = os.path.join(self.dir, "ark_master_list.csv")
        self.srv, self.url = serve(FakePreservica)
        FakePreservica.VISIBLE = True
        FakePreservica.searches = []
        FakePreservica.RECORDS = [
            {"type": "IO", "uuid": U[0], "xip.title": "Letter", "mods.identifier": ["ark:/32498/new1", "local-7"]},
            {"type": "SO", "uuid": U[1], "xip.title": "A folder", "mods.identifier": "https://n2t.net/ark:/32498/fold-er"},
            {"type": "IO", "uuid": U[2], "xip.title": "Copy A", "mods.identifier": "ark:/32498/dupe"},
            {"type": "IO", "uuid": U[3], "xip.title": "Copy B", "mods.identifier": "ark:/32498/dupe"},
            {"type": "IO", "uuid": U[4], "xip.title": "Moved", "mods.identifier": "ark:/32498/conf"},
            {"type": "IO", "uuid": U[5], "xip.title": "Stray", "mods.identifier": "ark:/32498/elsewhere"},
            {"type": "IO", "uuid": U[6], "xip.title": "No ARK", "mods.identifier": "local-9"},
            {"type": "IO", "uuid": U[7], "xip.title": "Gone", "mods.identifier": "ark:/32498/wdrn"},
        ]
        rows = [
            {"ark": "ark:/32498/new1", "title": "t1"},
            {"ark": "ark:/32498/folder", "title": "t2", "preservica_ref": U[1]},  # bare UUID, really a Folder
            {"ark": "ark:/32498/dupe", "title": "t3"},
            {"ark": "ark:/32498/conf", "title": "t4", "preservica_ref": f"IO_{U[9]}"},
            {"ark": "ark:/32498/miss", "title": "t5"},
            {"ark": "ark:/32498/hand", "title": "t6", "preservica_ref": f"IO_{U[8]}"},
            {"ark": "ark:/32498/wdrn", "title": "t7", "redirect_status": "withdrawn"},
        ]
        write(self.manifest, am.MANIFEST_FIELDS, [am.finish(am.manifest_row(r)) for r in rows])
        write(self.master, ["ark"], [{"ark": r["ark"]} for r in rows])
        self.env = {"PRESERVICA_HOST": self.url, "PRESERVICA_USER": "svc", "PRESERVICA_PASSWORD": "pw",
                    "PRESERVICA_TENANT": "UNIOWA", "ARK_FIELDS": "mods.identifier", "ARK_NAAN": "32498",
                    "GITHUB_OUTPUT": os.path.join(self.dir, "out.txt")}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def rows(self):
        return {am.ark_key(r["ark"]): r for r in am.read_csv(self.manifest)}

    def test_match_rules(self):
        code, out = run("--manifest", self.manifest, "--master-list", self.master, "match", "--summary", "",
                        env=self.env)
        self.assertEqual(code, 0, out)
        r = self.rows()
        self.assertEqual((r["32498/new1"]["preservica_ref"], r["32498/new1"]["redirect_status"]),
                         (f"IO_{U[0]}", "active"))
        self.assertEqual(r["32498/new1"]["preservica_title"], "Letter")
        self.assertEqual(r["32498/folder"]["preservica_ref"], f"SO_{U[1]}", "type taken from Preservica")
        self.assertEqual(r["32498/dupe"]["match"], "several records")
        self.assertEqual(r["32498/dupe"]["redirect_status"], "unmatched")
        self.assertEqual(r["32498/conf"]["preservica_ref"], f"IO_{U[9]}", "existing ref never replaced")
        self.assertEqual(r["32498/conf"]["match"], "conflict")
        self.assertEqual(r["32498/miss"]["match"], "not found")
        self.assertEqual(r["32498/hand"]["match"], "entered by hand")
        self.assertEqual(r["32498/wdrn"]["redirect_status"], "withdrawn")
        self.assertIn("ark:/32498/elsewhere", out, "ARKs not in the manifest are reported")
        with open(self.env["GITHUB_OUTPUT"]) as fh:
            self.assertIn("changed=", fh.read())
        self.assertTrue(all(q["q"] == "*" for q in FakePreservica.searches))

    def test_second_run_changes_nothing(self):
        run("--manifest", self.manifest, "match", "--summary", "", env=self.env)
        open(self.env["GITHUB_OUTPUT"], "w").close()
        run("--manifest", self.manifest, "match", "--summary", "", env=self.env)
        with open(self.env["GITHUB_OUTPUT"]) as fh:
            self.assertEqual(fh.read().strip(), "changed=0")

    def test_account_that_sees_nothing(self):
        FakePreservica.VISIBLE = False
        code, out = run("--manifest", self.manifest, "match", "--summary", "", env=self.env)
        self.assertNotEqual(code, 0)
        self.assertIn("Read Metadata", out)

    def test_wrong_tenant(self):
        code, out = run("--manifest", self.manifest, "match", "--summary", "",
                        env={**self.env, "PRESERVICA_TENANT": "UNI"})
        self.assertNotEqual(code, 0)
        self.assertIn("incorrect.tenant", out)

    def test_redirects_csv(self):
        run("--manifest", self.manifest, "match", "--summary", "", env=self.env)
        out_csv = os.path.join(self.dir, "ark_redirects.csv")
        self.assertEqual(run("--manifest", self.manifest, "redirects", "--out", out_csv)[0], 0)
        with open(out_csv, newline="") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, am.REDIRECT_FIELDS)
            rows = {r["ark"]: r for r in reader}
        portal = "https://uni.access.preservica.com/portal/en-US"
        self.assertEqual(rows["ark:/32498/new1"]["p_path"], f"{portal}/asset/sdb%3AIO%7C{U[0]}")
        self.assertEqual(rows["ark:/32498/folder"]["p_path"], f"{portal}/folder/sdb%3ASO%7C{U[1]}")
        self.assertEqual(rows["ark:/32498/conf"]["p_path"], f"{portal}/asset/sdb%3AIO%7C{U[9]}",
                         "a conflict keeps pointing at the existing reference")
        self.assertEqual(rows["ark:/32498/wdrn"]["p_path"], "410")
        self.assertNotIn("ark:/32498/miss", rows, "unmatched ARKs have nowhere to go yet")
        self.assertNotIn("ark:/32498/dupe", rows)

    def test_redirects_with_old_addresses(self):
        rows = am.read_csv(self.manifest)
        rows[0].update(dc_path="https://scholarworks.uni.edu/etd/123", dc_article="etd/1124",
                       preservica_ref=f"IO_{U[0]}")
        rows[1].update(url="https://digital.library.uni.edu/special")
        write(self.manifest, am.MANIFEST_FIELDS, [am.finish(am.manifest_row(r)) for r in rows])
        out_csv = os.path.join(self.dir, "ark_redirects.csv")
        self.assertEqual(run("--manifest", self.manifest, "redirects", "--out", out_csv)[0], 0)
        got = am.read_csv(out_csv)
        new1 = next(r for r in got if r["ark"] == "ark:/32498/new1")
        self.assertEqual(new1["dc_path"], "https://scholarworks.uni.edu/etd/123")
        self.assertIn({"dc_path": "https://scholarworks.uni.edu/cgi/viewcontent.cgi?article=1124&context=etd",
                       "p_path": new1["p_path"], "ark": ""}, got)
        folder = next(r for r in got if r["ark"] == "ark:/32498/folder")
        self.assertEqual(folder["p_path"], "https://digital.library.uni.edu/special", "url wins over the reference")
        # And the Apache side accepts the result as it is.
        build_map = os.path.join(HERE, "..", "scripts", "build_map.py")
        p = subprocess.run([sys.executable, build_map, "--check", out_csv], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_check(self):
        self.assertEqual(run("--manifest", self.manifest, "--master-list", self.master, "check")[0], 0)
        rows = am.read_csv(self.manifest)
        rows.append(dict(rows[0]))                                   # duplicate ARK
        rows.append({**rows[1], "ark": "ark:/12345/other"})          # wrong NAAN, not in master list
        rows.append({**rows[2], "ark": "ark:/32498/zz", "redirect_status": "gone"})
        write(self.manifest, am.MANIFEST_FIELDS, rows)
        code, out = run("--manifest", self.manifest, "--master-list", self.master, "check")
        self.assertEqual(code, 1)
        for text in ("is also on line", "isn't under NAAN", "isn't in", "redirect_status 'gone'"):
            self.assertIn(text, out)


class Minting(unittest.TestCase):
    def test_generate_then_add(self):
        d = tempfile.mkdtemp()
        new = os.path.join(d, "new_arks.csv")
        write(new, ["identifier", "title", "shoulder", "url", "dc_path", "dc_article", "preservica_ref", "status"],
              [{"identifier": "A-1", "title": "First"}, {"identifier": "A-2", "title": "Second"}])
        master, manifest = os.path.join(d, "ark_master_list.csv"), os.path.join(d, "arks_manifest.csv")
        for _ in range(2):  # two runs against the same master list
            subprocess.run([sys.executable, os.path.join(HERE, "generate_arks.py"), new, os.path.join(d, "minted"),
                            "--naan", "32498", "--master-list", master, "--no-redirects-manifest"],
                           check=True, capture_output=True)
            self.assertEqual(run("--manifest", manifest, "add-minted", os.path.join(d, "minted.csv"))[0], 0)
        rows = am.read_csv(manifest)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({r["ark"] for r in rows}), 4, "no ARK issued twice")
        self.assertTrue(all(r["ark"].startswith("ark:/32498/") and r["redirect_status"] == "unmatched" for r in rows))
        self.assertEqual(run("--manifest", manifest, "--master-list", master, "check")[0], 0)


if __name__ == "__main__":
    unittest.main()
