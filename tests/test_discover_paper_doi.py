"""Wrong versions, authors and artifact bytes must never become source citations."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import discover_paper_doi as discovery


class DiscoverPaperDoi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf, self.source = b"exact version PDF", b"exact source archive"
        (self.root / "paper.pdf").write_bytes(self.pdf)
        (self.root / "source.archive").write_bytes(self.source)
        self.work = {"work": "paper", "current_version": 2, "doi": None,
                     "title": "A square-value result", "license": "CC-BY-4.0",
                     "authors": [{"name": "Haobo Ma", "affiliation": "ChronoAI, Singapore"},
                                 {"name": "Wenlin Zhang", "affiliation": "NUS; ChronoAI, Singapore"}]}
        self.paper = {"work": self.work, "files": {k: {"path": p, "sha256": hashlib.sha256(b).hexdigest()}
                      for k, p, b in [("pdf", "paper.pdf", self.pdf), ("source", "source.archive", self.source)]}}
        self.save_paper()
        self.metadata = {"doi": "10.5281/zenodo.101", "title": self.work["title"], "version": "2",
                         "license": {"id": "cc-by-4.0"},
                         "creators": [{"name": "Ma, Haobo", "affiliation": "ChronoAI, Singapore"},
                                      {"name": "Zhang, Wenlin", "affiliation": "NUS; ChronoAI, Singapore"}],
                         "relations": {"version": [{"parent": {"pid_value": "100"}}]}}
        self.record = {"id": 101, "metadata": self.metadata,
                       "files": [{"key": "note.pdf", "size": len(self.pdf)},
                                 {"key": "source.zip", "size": len(self.source)}]}
        self.hits = [{"attributes": {"doi": "10.5281/zenodo.101", "titles": [{"title": self.work["title"]}],
                                    "relatedIdentifiers": [{"relationType": "IsVersionOf"}]}}]
        self.urls = []

    def save_paper(self):
        (self.root / "paper.json").write_text(json.dumps(self.paper))

    def fetch(self, url, limit):
        self.urls.append(url)
        if url.startswith("https://api.datacite.org/"):
            return json.dumps({"data": self.hits}).encode()
        if url.endswith("/records/101"):
            return json.dumps(self.record).encode()
        if url.endswith("/note.pdf/content"):
            return self.pdf
        if url.endswith("/source.zip/content"):
            return self.source
        raise AssertionError(url)

    def test_null_catalog_resolves_exact_version_without_editing_publisher_bytes(self):
        before = (self.root / "paper.json").read_bytes()
        result = discovery.discover(self.root, self.fetch)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["version_doi"], "10.5281/zenodo.101")
        self.assertEqual(result["concept_doi"], "10.5281/zenodo.100")
        self.assertEqual(set(result["files"]), {"pdf", "source"})
        self.assertEqual((self.root / "paper.json").read_bytes(), before)

    def test_pdf_match_without_matching_source_is_unconfirmed(self):
        self.source = b"different source zip"
        result = discovery.discover(self.root, self.fetch)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("both frozen PDF and source", result["candidates"][0]["reason"])

    def test_source_match_without_matching_pdf_is_unconfirmed(self):
        self.pdf = b"wrong version PDF"
        self.assertEqual(discovery.discover(self.root, self.fetch)["status"], "unconfirmed")

    def test_wrong_metadata_cannot_reuse_identical_files(self):
        for field, changed in [("version", "1"), ("license", {"id": "cc-by-nc-4.0"}),
                               ("title", "A different result"),
                               ("creators", [{"name": "Ma, Haobo", "affiliation": "NUS"}])]:
            with self.subTest(field=field):
                original = self.metadata[field]
                self.metadata[field] = changed
                result = discovery.discover(self.root, self.fetch)
                self.metadata[field] = original
                self.assertEqual(result["status"], "unconfirmed")

    def test_concept_record_is_not_a_version_citation(self):
        self.hits[0]["attributes"]["relatedIdentifiers"] = [{"relationType": "HasVersion"}]
        self.assertEqual(discovery.discover(self.root, self.fetch)["status"], "unconfirmed")
        self.assertFalse(any(urlsplit(url).hostname == "zenodo.org" for url in self.urls))

    def test_unrelated_title_is_not_downloaded(self):
        self.hits[0]["attributes"]["titles"][0]["title"] = "Another paper"
        self.assertEqual(discovery.discover(self.root, self.fetch)["status"], "unconfirmed")
        self.assertEqual(len(self.urls), 1)

    def test_registry_failure_preserves_uncertainty(self):
        def unavailable(url, limit):
            raise OSError("registry unavailable")
        result = discovery.discover(self.root, unavailable)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("registry unavailable", result["search_limitation"])
        self.assertIn("absence is not established", result["limitation"])

    def test_existing_catalog_doi_can_be_verified_during_search_outage(self):
        self.work["doi"] = "10.5281/zenodo.101"
        self.save_paper()
        def unavailable_search(url, limit):
            if urlsplit(url).hostname == "api.datacite.org":
                raise OSError("search unavailable")
            return self.fetch(url, limit)
        self.assertEqual(discovery.discover(self.root, unavailable_search)["status"], "verified")

    def test_mismatched_frozen_artifact_stops_before_network(self):
        (self.root / "paper.pdf").write_bytes(b"edited")
        result = discovery.discover(self.root, self.fetch)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("frozen pdf hash differs", result["limitation"])
        self.assertEqual(self.urls, [])

    def test_registry_file_link_cannot_redirect_discovery_to_another_host(self):
        for file in self.record["files"]:
            file["links"] = {"self": "https://unrelated.example/secret"}
        self.assertEqual(discovery.discover(self.root, self.fetch)["status"], "verified")
        self.assertFalse(any(urlsplit(url).hostname == "unrelated.example" for url in self.urls))


if __name__ == "__main__":
    unittest.main()
