"""Exercise the real publisher HTTP client against an isolated local GitHub API fake."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

spec = importlib.util.spec_from_file_location("publisher", Path(__file__).parents[1] / "scripts/publish.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
WORK = "chen-chen-step-m-eulerian-parity-positivity"


class Fake:
    def __init__(self):
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.repos = {}
        self.writes = []
        self.pages = {}
        self.issues = {}
        self.creations = 0
        self.fail_issue = False
        for name, files in {"catalog": {"scripts/publish.py": b"owned", "publish.json": b"owned"},
                            ".github": {"other.txt": b"owned"},
                            "formalization": {"notes/human.md": b"owned", "targets/stale/Target.lean": b"stale", ".github/workflows/human.yml": b"owned"},
                            "mathesis-papers.github.io": {"CNAME": b"papers.trureturing.org\n", ".github/workflows/pages.yml": b"owned", "doi/stale/index.html": b"old"}}.items():
            self.add_repo(name, files)

    def object(self, store, data):
        oid = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
        store[oid] = copy.deepcopy(data)
        return oid

    def add_repo(self, name, files):
        entries = {}
        for path, data in files.items():
            oid = publisher.sha(data)
            self.blobs[oid] = data
            entries[path] = {"path": path, "mode": "100644", "type": "blob", "sha": oid}
        tree = self.object(self.trees, entries)
        commit = self.object(self.commits, {"tree": {"sha": tree}, "parents": []})
        self.repos[name] = {"default_branch": "main", "head": commit, "private": False, "archived": False,
                            "template_repository": {"full_name": "mathesis-papers/paper-template"}}

    def files(self, name):
        tree = self.commits[self.repos[name]["head"]]["tree"]["sha"]
        return {p: self.blobs[e["sha"]] for p, e in self.trees[tree].items()}

    def request(self, method, path, body):
        clean = path.split("?", 1)[0]
        parts = clean.strip("/").split("/")
        if method != "GET":
            self.writes.append((method, path))
        if clean == "/repos/mathesis-papers/paper-template/generate":
            self.creations += 1
            if body["name"] in self.repos:
                return 422, {}
            self.add_repo(body["name"], {"README.md": b"template", "template-extra": b"drift"})
            return 201, self.repos[body["name"]]
        name = parts[2]
        if name not in self.repos:
            return 404, {}
        info = self.repos[name]
        tail = "/".join(parts[3:])
        if tail == "":
            if method == "PATCH":
                info.update(body)
            return 200, info
        if tail == "pages":
            if method == "GET":
                return (200, self.pages[name]) if name in self.pages else (404, {})
            self.pages[name] = body
            return 201, body
        if tail == "issues":
            if method == "GET":
                # Exercise pagination, including closed issues; PRs aren't issues.
                page = int(path.rsplit("page=", 1)[1])
                return 200, self.issues.get(name, [])[100*(page-1):100*page]
            if self.fail_issue:
                return 503, {}
            self.issues.setdefault(name, []).append(dict(body, state="closed"))
            return 201, body
        if tail == "git/ref/heads/main":
            return 200, {"object": {"sha": info["head"]}}
        if tail.startswith("git/commits/"):
            return 200, self.commits[parts[-1]]
        if tail.startswith("git/trees/"):
            return 200, {"truncated": False, "tree": list(self.trees[parts[-1]].values())}
        if tail.startswith("git/blobs/"):
            return 200, {"encoding": "base64", "content": base64.b64encode(self.blobs[parts[-1]]).decode()}
        if tail == "git/blobs":
            data = base64.b64decode(body["content"])
            oid = publisher.sha(data)
            self.blobs[oid] = data
            return 201, {"sha": oid}
        if tail == "git/trees":
            entries = copy.deepcopy(self.trees[body["base_tree"]])
            for entry in body["tree"]:
                if entry["sha"] is None:
                    entries.pop(entry["path"], None)
                else:
                    entries[entry["path"]] = entry
            return 201, {"sha": self.object(self.trees, entries)}
        if tail == "git/commits":
            value = {"tree": {"sha": body["tree"]}, "parents": body["parents"]}
            return 201, {"sha": self.object(self.commits, value)}
        if tail == "git/refs/heads/main":
            if body["force"] or self.commits[body["sha"]]["parents"] != [info["head"]]:
                return 422, {}
            info["head"] = body["sha"]
            return 200, {}
        raise AssertionError((method, path, body))


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="publisher-test-")
        self.source = Path(self.tmp.name) / "source"
        self.source.mkdir()
        self.stage = Path(self.tmp.name) / "stage"
        self.stage.mkdir()
        self.fake = Fake()
        fake = self.fake
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self): self.reply("GET")
            def do_POST(self): self.reply("POST")
            def do_PUT(self): self.reply("PUT")
            def do_PATCH(self): self.reply("PATCH")
            def reply(self, method):
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                code, value = fake.request(method, self.path, json.loads(data) if data else None)
                raw = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = publisher.API("local-fake-token", f"http://127.0.0.1:{self.server.server_port}")
        publisher.write(self.source / "repositories.json", {})
        publisher.write(self.source / "publish.json", [WORK])
        publisher.write(self.source / "publisher.json", {"api_base":"https://mathesis-omega-research.apps.chrono-ai.fun"})
        publisher.write(self.stage / "plan.json", {"original_mapping": {}, "mapping": {WORK: WORK}, "new_works": [WORK], "allow_list": [WORK], "config": {"api_base":"https://mathesis-omega-research.apps.chrono-ai.fun"}})
        for path, data in {"output/site/CNAME": b"papers.trureturing.org\n", "output/site/index.html": b"index", "output/site/.nojekyll": b"", "output/site/doi/10.5281/zenodo.23957560/index.html": b"redirect",
                           "output/formalization/README.md": b"projection", "output/formalization/.mathesis/generated.json": b"{}",
                           "output/formalization/.github/workflows/check.yml": b"check",
                           "output/formalization/curated/problem/Target.lean": b"exact public bytes",
                           "output/profile/README.md": b"profile", "output/catalog/repositories.json": json.dumps({WORK: WORK}).encode(),
                           "output/catalog/archive-index.json": b"{}", "checklist.md": b"- [ ] Prepare reading and media",
                           f"output/papers/{WORK}/paper.json": json.dumps({"work": {"work": WORK, "repository_name": WORK, "title": "Canonical paper title"}}).encode(),
                           f"papers/{WORK}/README.md": b"filled", f"papers/{WORK}/paper.json": json.dumps({"work": {"work": WORK, "repository_name": WORK}}).encode(),
                           f"papers/{WORK}/.mathesis/book.json": b"{}", f"papers/{WORK}/.mathesis/generated.json": b"{}",
                           f"papers/{WORK}/.github/workflows/pages.yml": b"workflow"}.items():
            target = self.stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        tbook = self.stage / "papers" / WORK / "tbook"
        tbook.write_bytes(b"#!/bin/sh\nexit 0\n")
        tbook.chmod(0o755)
        (self.stage / "papers" / WORK / "AGENTS.md").symlink_to("README.md")
        controls = {name: (self.source / name).read_bytes() for name in ("repositories.json", "publish.json", "publisher.json")}
        self.fake.add_repo("catalog", dict(self.fake.files("catalog"), **controls))
        self.seal()

    def seal(self):
        publisher.write(self.stage / "seal.json", publisher.seal_files(self.stage))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def apply(self):
        publisher.apply(self.api, self.source, self.stage)

    def test_create_publish_repeat_and_preserve_unmanaged_files(self):
        self.apply()
        self.assertEqual(self.fake.creations, 1)
        self.assertEqual(self.fake.files("formalization")["curated/problem/Target.lean"], b"exact public bytes")
        self.assertEqual(self.fake.files("formalization")["notes/human.md"], b"owned")
        self.assertIn(".github/workflows/human.yml", self.fake.files("formalization"))
        self.assertNotIn("targets/stale/Target.lean", self.fake.files("formalization"))
        self.assertEqual(len(self.fake.issues[WORK]), 1)
        self.assertEqual(self.fake.pages[WORK]["build_type"], "workflow")
        self.assertEqual(self.fake.files("mathesis-papers.github.io")["CNAME"], b"papers.trureturing.org\n")
        self.assertIn(".github/workflows/pages.yml", self.fake.files("mathesis-papers.github.io"))
        self.assertNotIn("doi/stale/index.html", self.fake.files("mathesis-papers.github.io"))
        self.assertIn("scripts/publish.py", self.fake.files("catalog"))
        self.assertNotIn("template-extra", self.fake.files(WORK))
        entries = self.api.state(WORK)[3]
        self.assertEqual(entries["tbook"]["mode"], "100755")
        self.assertEqual(entries["AGENTS.md"]["mode"], "120000")
        pages = self.fake.writes.index(("POST", f"/repos/mathesis-papers/{WORK}/pages"))
        push = self.fake.writes.index(("PATCH", f"/repos/mathesis-papers/{WORK}/git/refs/heads/main"))
        self.assertLess(pages, push)
        count = len(self.fake.writes)
        self.apply()
        self.assertEqual(len(self.fake.writes), count)
        self.assertEqual(self.api.receipts[WORK]["changed_paths"], 0)
        self.assertEqual(self.api.receipts[WORK]["before"], self.api.receipts[WORK]["after"])

    def test_missing_or_private_formalization_repo_fails_before_writes(self):
        self.fake.repos["formalization"]["private"] = True
        with self.assertRaisesRegex(ValueError, "already exist, public and active"):
            self.apply()
        self.assertEqual(self.fake.writes, [])
        del self.fake.repos["formalization"]
        with self.assertRaisesRegex(RuntimeError, "404"):
            self.apply()
        self.assertEqual(self.fake.writes, [])

    def test_formalization_tamper_is_sealed_and_unmanaged_output_refused(self):
        (self.stage / "output/formalization/curated/problem/Target.lean").write_bytes(b"tamper")
        with self.assertRaisesRegex(ValueError, "changed after validation"):
            self.apply()
        self.assertEqual(self.fake.writes, [])
        (self.stage / "output/formalization/UNRELATED").write_bytes(b"unmanaged")
        self.seal()
        with self.assertRaisesRegex(ValueError, "invalid formalization tree"):
            self.apply()
        self.assertEqual(self.fake.writes, [])

    def test_recover_after_issue_failure_preserves_populated_book(self):
        self.fake.fail_issue = True
        with self.assertRaisesRegex(RuntimeError, "503"):
            self.apply()
        self.assertEqual(json.loads(self.fake.files("catalog")["repositories.json"]), {})
        # Simulate content work after the initialization commit: retry must preserve it.
        files = self.fake.files(WORK)
        files["books/reading.md"] = b"human content"
        self.fake.add_repo(WORK, files)
        self.fake.fail_issue = False
        self.apply()
        self.assertEqual(self.fake.creations, 1)
        self.assertEqual(self.fake.files(WORK)["books/reading.md"], b"human content")
        self.assertEqual(len(self.fake.issues[WORK]), 1)

    def test_existing_authored_book_needs_no_fresh_skeleton_and_retains_its_content(self):
        self.apply()
        files = self.fake.files(WORK)
        files["books/reading.md"] = b"Reviewed story; preserve these exact bytes."
        files["slides/seminar.tex"] = b"Reviewed slides; do not regenerate."
        self.fake.add_repo(WORK, files)
        # Existing authored books are deliberately absent from prepare's new
        # skeletons. The sealed public projection still supplies their metadata.
        shutil.rmtree(self.stage / "papers" / WORK)
        self.seal()
        self.apply()
        self.assertEqual(self.fake.files(WORK), files)
        self.assertEqual(self.fake.repos[WORK]["description"], "Canonical paper title")
        self.assertEqual(self.fake.creations, 1)
        count = len(self.fake.writes)
        self.apply()
        self.assertEqual(len(self.fake.writes), count)
        self.assertEqual(self.api.receipts[WORK]["changed_paths"], 0)

    def test_resume_template_only_repo_and_closed_paginated_issue(self):
        self.fake.add_repo(WORK, {"README.md": b"template"})
        self.fake.issues[WORK] = [{"title":"unrelated", "body":""} for _ in range(100)] + [{"title":publisher.TITLE, "body":publisher.MARKER, "state":"closed"}]
        self.apply()
        self.assertEqual(self.fake.creations, 0)
        self.assertEqual(len(self.fake.issues[WORK]), 101)
        self.assertEqual(self.fake.files(WORK)["README.md"], b"filled")

    def test_seal_and_cname_fail_before_any_write(self):
        (self.stage / "output/site/index.html").write_bytes(b"tamper")
        with self.assertRaisesRegex(ValueError, "changed after validation"):
            self.apply()
        self.assertEqual(self.fake.writes, [])
        self.seal()
        self.fake.add_repo("mathesis-papers.github.io", {"CNAME": b"different.example"})
        with self.assertRaisesRegex(ValueError, "remote CNAME differs"):
            self.apply()
        self.assertEqual(self.fake.writes, [])

    def test_control_change_and_mode_tamper_fail_before_publication(self):
        files = self.fake.files("catalog")
        files["publish.json"] = b"[]"
        self.fake.add_repo("catalog", files)
        with self.assertRaisesRegex(ValueError, "remote publisher control changed"):
            self.apply()
        self.assertEqual(self.fake.writes, [])
        (self.stage / "papers" / WORK / "tbook").chmod(0o644)
        with self.assertRaisesRegex(ValueError, "changed after validation"):
            self.apply()
        self.assertEqual(self.fake.writes, [])

    def test_unrelated_repo_not_adopted_and_prepare_failure_does_not_publish(self):
        self.fake.add_repo(WORK, {"README.md": b"unrelated"})
        self.fake.repos[WORK].pop("template_repository")
        with self.assertRaisesRegex(ValueError, "not from paper-template"):
            self.apply()
        # Provenance is checked before Pages is enabled or any other write.
        self.assertEqual(self.fake.writes, [])
        before = publisher.tree_files(self.source)
        with mock.patch.object(publisher, "run", side_effect=subprocess.CalledProcessError(1, "snapshot")):
            with self.assertRaises(subprocess.CalledProcessError):
                publisher.prepare(Path("unused"), self.source, Path(self.tmp.name) / "failed-stage")
        self.assertEqual(publisher.tree_files(self.source), before)
        self.assertEqual(self.fake.writes, [])


if __name__ == "__main__":
    unittest.main()
