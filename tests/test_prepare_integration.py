"""Optional full offline preflight using a real paper and local template clone.

Set MATHESIS_CATALOG_BINARY, MATHESIS_TEST_TEMPLATE and MATHESIS_TEST_PAPER.
The public Mathesis listing is a local fixture, with no statement/proof rows;
it is not a replacement research snapshot. All network traffic is loopback.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
import zipfile

from test_publisher import publisher

BINARY = os.environ.get("MATHESIS_CATALOG_BINARY")
TEMPLATE = os.environ.get("MATHESIS_TEST_TEMPLATE")
PAPER = os.environ.get("MATHESIS_TEST_PAPER")


@unittest.skipUnless(BINARY and TEMPLATE and PAPER, "set local SDK binary, template and real-paper fixture paths")
class PrepareIntegration(unittest.TestCase):
    def test_real_snapshot_fetch_render_check_fill_and_seal_without_remote_writes(self):
        source_paper = Path(PAPER)
        work = publisher.read(source_paper / "paper.json")["work"]
        pdf = (source_paper / "paper.pdf").read_bytes()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            for path in sorted((source_paper / "manuscript").rglob("*")):
                if path.is_file():
                    archive.writestr(path.relative_to(source_paper / "manuscript").as_posix(), path.read_bytes())
        manuscript = buf.getvalue()
        row = {"id":work["work"], "revision":str(work["current_version"]), "title":work["title"],
               "authors":work["authors"], "license":work["license"], "published_on":work["published_on"], "doi":work["doi"],
               "pdf":{"url":"/files/pdf", "sha256":hashlib.sha256(pdf).hexdigest()},
               "source":{"url":"/files/source", "sha256":hashlib.sha256(manuscript).hexdigest()}}
        summary = {"record": "WP-2026-0007", "kind": "paper", "title": "Accepted author paper",
                   "authors": [{"name": "Original Author"}], "archive_consent": True, "accepted_at": "2026-11-14T00:00:00Z"}
        detail = {"summary": summary, "publication": {"version": 2, "claims_revision": 1},
                  "archive": {"marker": "public-archive-2026-11-12", "licence": "CC-BY-4.0", "consent_time": "2026-11-14T00:00:00Z",
                              "pdf": {"url": "/files/pdf", "bytes": len(pdf), "sha256": hashlib.sha256(pdf).hexdigest()},
                              "source": {"url": "/files/source", "bytes": len(manuscript), "sha256": hashlib.sha256(manuscript).hexdigest()}},
                  "plain_language": {"en": "A useful explanation.", "zh": "这是已有的解读。"}}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/files/pdf": data = pdf
                elif path == "/files/source": data = manuscript
                elif path == "/api/v1/papers/WP-2026-0007": data = json.dumps(detail).encode()
                elif path.endswith("/proofs") or path.startswith("/api/v1/leaderboard"): data = b"[]"
                else: data = json.dumps({"items":[row] if path == "/api/v1/collected-papers" else [summary] if path == "/api/v1/papers" else [], "next_before":None}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="publisher-prepare-integration-") as name:
                root = Path(name)
                source = root / "publisher"
                source.mkdir()
                config = {"api_base":f"http://127.0.0.1:{server.server_port}", "publication_selection": "all_eligible", "paper_publication": "collection", "book_works": [work["work"]], "repository_naming": "doi-v1", "template_commit":subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=TEMPLATE, text=True).strip()}
                publisher.write(source / "publisher.json", config)
                publisher.write(source / "publish.json", [work["work"]])
                publisher.write(source / "repositories.json", {})
                original_run = publisher.run
                calls = []
                def local_run(*args, cwd=None):
                    args = list(args)
                    if args[0] == "git" and "clone" in args:
                        args[args.index("https://github.com/mathesis-papers/paper-template.git")] = TEMPLATE
                    calls.append([str(a) for a in args])
                    original_run(*args, cwd=cwd)
                stage = root / "stage"
                with mock.patch.object(publisher, "run", side_effect=local_run):
                    publisher.prepare(Path(BINARY).resolve(), source, stage)
                self.assertEqual(publisher.read(stage / "seal.json"), publisher.seal_files(stage))
                repo = publisher.read(stage / "plan.json")["mapping"][work["work"]]
                self.assertEqual((stage / "papers" / repo / "paper.pdf").read_bytes(), pdf)
                self.assertEqual(publisher.read(stage / "papers" / repo / ".mathesis/book.json")["stage"], "skeleton")
                self.assertEqual((stage / "output/site/CNAME").read_text(), "papers.trureturing.org\n")
                self.assertEqual([c[1] for c in calls if c[0] == str(Path(BINARY).resolve())], ["snapshot", "fetch", "render", "check", "render", "check"])
                self.assertEqual(publisher.read(source / "repositories.json"), {})
                accepted = stage / "papers/wp-2026-0007"
                self.assertEqual((accepted / "source.archive").read_bytes(), manuscript)
                self.assertEqual((accepted / "versions/v2/paper.pdf").read_bytes(), pdf)
                self.assertEqual(publisher.read(accepted / "site/mathesis-materials.json")["reading"]["zh"], "https://papers.trureturing.org/wp-2026-0007/zh/")
                self.assertEqual(publisher.read(stage / "factory/factory.json")["papers"], 2)
                self.assertFalse((source / "factory.json").exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
