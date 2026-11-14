"""Factory policy, version isolation and public readback; no authenticated writes."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_publisher import publisher

factory = publisher.factory
spec = importlib.util.spec_from_file_location("verify_factory", Path(__file__).parents[1] / "scripts/verify_factory.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class FactoryPolicy(unittest.TestCase):
    def test_new_eligible_work_enters_without_expanding_baseline_or_selecting_private(self):
        works = [{"work": name, "publication": scope} for name, scope in
                 (("old", "public"), ("new", "public"), ("legacy", "requires_author_consent"))]
        selected, names = publisher.select_works({"publication_selection": "all_eligible"}, ["old"], works)
        self.assertEqual(names, ["new", "old"])
        self.assertNotIn("legacy", selected)
        self.assertEqual(publisher.select_works({}, ["old"], works)[1], ["old"])
        mapping = publisher.plan_repositories({}, ["WP-2026-0007"], {"WP-2026-0007": {}}, "doi-v1")
        self.assertEqual(mapping, {"WP-2026-0007": "wp-2026-0007"})

    def test_old_reading_neither_blocks_nor_enters_new_paper_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version, work, error in ((1, "paper", None), (3, "paper", "ahead"), (1, "another", "work differs")):
                value = {"work": work, "version": version}
                opener = mock.Mock()
                opener.open.return_value = io.BytesIO(json.dumps(value).encode())
                with mock.patch.object(publisher.urllib.request, "build_opener", return_value=opener):
                    if error:
                        with self.assertRaisesRegex(ValueError, error):
                            publisher.material_downloads({"paper": "repo"}, root, versions={"paper": 2})
                    else:
                        publisher.material_downloads({"paper": "repo"}, root, versions={"paper": 2})
                self.assertFalse((root / "repo.json").exists())

    def test_inventory_keeps_exact_targets_and_pending_work_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            work = {"work": "paper", "publication": "public", "current_version": 2, "title": "A | result", "doi": None}
            publisher.write(output / "catalog/works.json", {"works": [work, dict(work, work="private", publication="requires_author_consent")]})
            publisher.write(output / "catalog/snapshot.json", {"time": "2026-11-14T00:00:00Z"})
            publisher.write(output / "catalog/materials/paper/materials.json", {"reading": {}})
            publisher.write(output / "papers/repo/paper.json", {"files": {"pdf": {"sha256": "a" * 64}}})
            publisher.write(output / "formalization/index/papers/paper.json", {"work": "paper", "split": {
                "covered_by_existing_base": ["one"], "open_target": ["two"], "no_target_yet": ["three"]}})
            inventory = factory.render(output, {"paper": "repo"}, {"paper": {"plain_language": {"en": "Reading", "zh": "解读"}}}, [])
            row = inventory["entries"][0]
            self.assertEqual(inventory["papers"], 1)
            self.assertEqual(row["formalization"]["open_target"], 1)
            self.assertEqual(row["next_steps"], ["attach_verified_doi", "prepare_exact_targets", "submit_proofs_for_open_targets"])
            self.assertIsNone(row["trureturing_write_back"])
            self.assertFalse((output / "catalog/factory.json").exists())
            self.assertIn("A &#124; result", (output.parent / "factory/FACTORY.md").read_text())


class FactoryReadback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stage = Path(self.tmp.name)
        self.site = "https://papers.trureturing.org/repo/"
        self.row = {"work": "paper", "version": 2, "site_url": self.site, "repository_url": "https://github.com/mathesis-papers/repo",
                    "mode": "paper", "files": {key: {"sha256": hashlib.sha256(data).hexdigest()} for key, data in (("pdf", b"PDF"), ("source", b"SOURCE"))}}
        self.manifest = {"work": "paper", "version": 2, "site_url": self.site, "repository_url": self.row["repository_url"], "reading": {"en": self.site + "en/"}}
        publisher.write(self.stage / "plan.json", {"mapping": {"paper": "repo"}, "config": {}})
        publisher.write(self.stage / "factory/factory.json", {"snapshot_time": "now", "entries": [self.row]})
        publisher.write(self.stage / "papers/repo/site/mathesis-materials.json", self.manifest)
        for path, data in (("output/papers/repo/paper.pdf", b"PDF"), ("output/papers/repo/source.archive", b"SOURCE"), ("papers/repo/site/en/index.html", b"English"), ("papers/repo/site/index.html", b"<!doctype html><html>Paper")):
            p = self.stage / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.urls = {self.site: b"<!doctype html><html>Paper", self.site + "mathesis-materials.json": json.dumps(self.manifest).encode(),
                     self.site + "en/": b"English", self.site + "paper.pdf": b"PDF",
                     "https://raw.githubusercontent.com/mathesis-papers/repo/main/source.archive": b"SOURCE"}

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self):
        return verifier.verify(self.stage, lambda url, limit: self.urls[url])

    def test_readback_checks_landing_version_reading_and_frozen_file_hashes(self):
        self.assertEqual(self.verify()["passed"], 1)
        for url in (self.site, self.site + "en/", self.site + "paper.pdf", "https://raw.githubusercontent.com/mathesis-papers/repo/main/source.archive"):
            original = self.urls[url]
            self.urls[url] = b"wrong"
            self.assertEqual(self.verify()["passed"], 0, url)
            self.urls[url] = original
        self.urls[self.site + "mathesis-materials.json"] = json.dumps(dict(self.manifest, version=1)).encode()
        self.assertEqual(self.verify()["passed"], 0)

    def test_authored_book_accepts_only_explicit_alias_and_keeps_frozen_source(self):
        self.row["mode"] = "authored_book"
        publisher.write(self.stage / "factory/factory.json", {"snapshot_time": "now", "entries": [self.row]})
        old = dict(self.manifest, site_url="https://papers.trureturing.org/old/", repository_url="https://github.com/mathesis-papers/old", reading={"en": "https://papers.trureturing.org/old/en/"})
        self.urls[self.site + "mathesis-materials.json"] = json.dumps(old).encode()
        self.urls["https://raw.githubusercontent.com/mathesis-papers/repo/main/paper.pdf"] = b"PDF"
        del self.urls["https://raw.githubusercontent.com/mathesis-papers/repo/main/source.archive"]
        self.assertEqual(self.verify()["passed"], 0)
        publisher.write(self.stage / "plan.json", {"mapping": {"paper": "repo"}, "config": {"repository_aliases": {"paper": ["old"]}}})
        self.assertEqual(self.verify()["passed"], 1)
