"""Independent publication preserves papers without asking a model to review them."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_publisher import PublisherTests, WORK, publisher

collection = publisher.collection


class RepositoryIdentifiers(unittest.TestCase):
    def test_doi_names_prefer_the_cross_version_identifier_and_handle_missing_dois(self):
        rows = {"work": {"doi": "10.5281/zenodo.123", "concept_doi": "10.5281/zenodo.122"},
                "no-doi": {}, "version-only": {"doi": "10.1234/ABC.def"}}
        mapping = publisher.plan_repositories({}, list(rows), rows, "doi-v1")
        self.assertEqual(mapping, {"work": "10.5281-zenodo.122", "no-doi": "no-doi", "version-only": "10.1234-abc.def"})
        rows["work"]["doi"] = "10.5281/zenodo.124"
        self.assertEqual(publisher.plan_repositories(mapping, list(rows), rows, "doi-v1"), mapping)
        with self.assertRaisesRegex(ValueError, "collision"):
            publisher.plan_repositories({}, ["one", "two"], {w: {"doi": "10.1234/same"} for w in ("one", "two")}, "doi-v1")
        with self.assertRaisesRegex(ValueError, "invalid DOI"):
            publisher.plan_repositories({}, ["work"], {"work": {"doi": "not-a-doi"}}, "doi-v1")

    def test_identifiers_sort_by_publication_and_remain_unique_for_simultaneous_papers(self):
        rows = {work: {"published_on": date, "title": "Same title"} for work, date in (
            ("later", "2026-11-10T00:00:01Z"), ("first", "2026-11-10T00:00:00Z"),
            ("simultaneous", "2026-11-10T00:00:00Z"))}
        planned = publisher.plan_repositories({}, list(rows), rows, "objectid-v1")
        self.assertEqual(len(set(planned.values())), 3)
        for name in planned.values():
            self.assertRegex(name, r"^[a-f0-9]{24}$")
        self.assertLess(planned["first"], planned["later"])
        self.assertEqual(planned, publisher.plan_repositories({}, list(reversed(rows)), rows, "objectid-v1"))

    def test_existing_mapping_survives_new_version_and_legacy_names_are_preserved(self):
        rows = {"work": {"published_on": "2026-11-10T00:00:00Z"}}
        mapping = publisher.plan_repositories({}, ["work"], rows, "objectid-v1")
        rows["work"]["published_on"] = "2026-11-14T00:00:00Z"
        self.assertEqual(publisher.plan_repositories(mapping, ["work"], rows, "objectid-v1"), mapping)
        self.assertEqual(publisher.plan_repositories({"work": "existing-paper"}, ["work"], rows, "objectid-v1"), {"work": "existing-paper"})
        rows["work"]["versions"] = [{"published_on": "2026-11-10T00:00:00Z"}]
        self.assertEqual(publisher.plan_repositories({}, ["work"], rows, "objectid-v1"), mapping)

    def test_invalid_or_missing_dates_cannot_allocate_an_identifier(self):
        for value in (None, "2026-11-10T00:00:00", "1960-01-01T00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                publisher.plan_repositories({}, ["work"], {"work": {"published_on": value}}, "objectid-v1")


class CollectionRendering(unittest.TestCase):
    def test_pages_transition_reuses_only_checked_public_materials(self):
        value = {"schema": "mathesis.catalog-materials.v1", "work": WORK, "version": 2,
                 "manifest_available": True, "repository_url": "https://github.com/mathesis-papers/old",
                 "site_url": "https://papers.trureturing.org/old/", "reading": {"en": "https://papers.trureturing.org/old/en/"},
                 "slides": [], "visualizations": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher.write(root / "previous" / WORK / "materials.json", value)
            opener = mock.Mock()
            opener.open.side_effect = lambda *args, **kwargs: (_ for _ in ()).throw(publisher.urllib.error.HTTPError("transition", 503, "unavailable", {}, None))
            with mock.patch.object(publisher.urllib.request, "build_opener", return_value=opener):
                publisher.material_downloads({WORK: "new"}, root / "new", {WORK: ["old"]}, root / "previous")
            actual = publisher.read(root / "new/new.json")
            self.assertEqual(actual["version"], 2)
            self.assertEqual(actual["schema"], "mathesis.materials.v1")
            self.assertEqual(actual["reading"]["en"], "https://papers.trureturing.org/new/en/")
            publisher.write(root / "previous" / WORK / "materials.json", dict(value, work="another-paper"))
            with mock.patch.object(publisher.urllib.request, "build_opener", return_value=opener), self.assertRaisesRegex(ValueError, "identity differs"):
                publisher.material_downloads({WORK: "new"}, root / "new", {WORK: ["old"]}, root / "previous")

    def test_renamed_site_can_serve_previous_metadata_until_first_republication(self):
        value = {"work": WORK, "repository_url": "https://github.com/mathesis-papers/old",
                 "site_url": "https://papers.trureturing.org/old/", "reading": {"en": "https://papers.trureturing.org/old/en/"}}
        with tempfile.TemporaryDirectory() as directory:
            opener = mock.Mock()
            opener.open.side_effect = [publisher.urllib.error.HTTPError("old", 404, "missing", {}, None), io.BytesIO(json.dumps(value).encode())]
            with mock.patch.object(publisher.urllib.request, "build_opener", return_value=opener):
                publisher.material_downloads({WORK: "new"}, Path(directory), {WORK: ["old"]})
            actual = publisher.read(Path(directory) / "new.json")
            self.assertEqual(actual["repository_url"], "https://github.com/mathesis-papers/new")
            self.assertEqual(actual["reading"]["en"], "https://papers.trureturing.org/new/en/")

    def test_aliases_preserve_nested_material_paths_and_reject_collisions(self):
        value = {"work": WORK, "repository_url": "https://github.com/mathesis-papers/old",
                 "site_url": "https://papers.trureturing.org/old/",
                 "reading": {"en": "https://papers.trureturing.org/old/book/en/"},
                 "slides": [{"pdf_url": "https://papers.trureturing.org/old/book/slides/a.pdf"}]}
        moved = collection.relocate_materials(value, "old", "new")
        self.assertEqual(moved["reading"]["en"], "https://papers.trureturing.org/new/book/en/")
        self.assertEqual(moved["slides"][0]["pdf_url"], "https://papers.trureturing.org/new/book/slides/a.pdf")
        self.assertEqual(value["site_url"], "https://papers.trureturing.org/old/")
        collection.validate_aliases({WORK: "new"}, {WORK: ["old"]})
        for aliases in ({WORK: ["../outside"]}, {WORK: ["new"]}, {WORK: ["old", "old"]}, {"unknown": ["old"]}):
            with self.assertRaises(ValueError):
                collection.validate_aliases({WORK: "new"}, aliases)

    def test_book_relocation_changes_only_addresses_and_updates_the_integrity_record(self):
        paper = json.dumps({"work": {"work": WORK, "repository_name": "old", "doi": "10.1234/version"}}).encode()
        marker = {"sha256": {"paper.json": hashlib.sha256(paper).hexdigest(), "paper.pdf": "pdf hash"}}
        files = {"paper.json": paper, ".mathesis/generated.json": json.dumps(marker).encode(),
                 "site.toml": b'[site]\nrepo = "mathesis-papers/old"\nbase_url = "https://papers.trureturing.org/old/"\n[books]\norder = ["original-book"]\n'}
        updated = collection.relocate_book(files, "old", "10.1234-paper")
        self.assertIn(b'order = ["original-book"]', updated["site.toml"])
        self.assertIn(b'repo = "mathesis-papers/old"', updated["site.toml"])
        self.assertIn(b'https://papers.trureturing.org/10.1234-paper/', updated["site.toml"])
        result = json.loads(updated["paper.json"])
        self.assertEqual(result["work"]["doi"], "10.1234/version")
        self.assertEqual(result["work"]["repository_name"], "old")
        hashes = json.loads(updated[".mathesis/generated.json"])["sha256"]
        self.assertEqual(hashes["paper.pdf"], "pdf hash")
        self.assertEqual(hashes["paper.json"], hashlib.sha256(updated["paper.json"]).hexdigest())
        self.assertEqual(collection.relocate_book(updated, "old", "10.1234-paper"), updated)

    def test_existing_readings_files_and_attribution_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "projection", root / "paper"
            source.mkdir()
            work = {"work": WORK, "record": WORK, "repository_name": WORK, "publication": "public",
                    "license": "CC-BY-4.0", "current_version": 2, "title": "A < B", "authors": [{"name": "Researcher"}],
                    "abstract_text": "An exact result.", "doi": "10.5281/zenodo.123"}
            publisher.write(source / "paper.json", {"work": work})
            (source / "paper.pdf").write_bytes(b"exact frozen PDF")
            publisher.write(source / "formalization/nodes.json", {"nodes": [{"status": "preparing"}]})
            publisher.write(source / ".mathesis/generated.json", {"original": True})
            detail = {"id": WORK, "revision": "2", "plain_language": {"en": "A useful result.\n\n<script>not markup</script>", "zh": "这是已有解读。"},
                      "lean": {"url": "https://github.com/source/library/blob/" + "a" * 40 + "/Proof.lean"}}
            collection.prepare(source, detail, destination, "https://mathesis.example")
            self.assertEqual((destination / "paper.pdf").read_bytes(), b"exact frozen PDF")
            self.assertEqual((destination / "versions/v2/paper.pdf").read_bytes(), b"exact frozen PDF")
            self.assertEqual((destination / ".mathesis/generated.json").read_bytes(), (source / ".mathesis/generated.json").read_bytes())
            landing = (destination / "site/index.html").read_text()
            self.assertIn("Published paper", landing)
            self.assertNotIn("preparing", landing)
            self.assertIn("A &lt; B", landing)
            self.assertIn("&lt;script&gt;not markup&lt;/script&gt;", (destination / "site/en/index.html").read_text())
            manifest = publisher.read(destination / "site/mathesis-materials.json")
            self.assertEqual(set(manifest["reading"]), {"en", "zh"})
            marker = publisher.read(destination / collection.MARKER)
            for path, digest in marker["sha256"].items():
                self.assertEqual(hashlib.sha256((destination / path).read_bytes()).hexdigest(), digest)
                self.assertEqual(publisher.sha((destination / path).read_bytes()), marker["git_blobs"][path])
            with self.assertRaisesRegex(ValueError, "frozen version"):
                collection.prepare(source, dict(detail, revision="3"), root / "bad", "https://mathesis.example")
            self.assertFalse((root / "bad").exists())

    def test_missing_exposition_does_not_prevent_publication_or_invent_a_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "projection"
            source.mkdir()
            publisher.write(source / "paper.json", {"work": {"work": WORK, "record": WORK, "repository_name": WORK,
                "publication": "public", "license": "CC-BY-4.0", "current_version": 1, "title": "A result", "authors": [{"name": "Author"}]}})
            (source / "paper.pdf").write_bytes(b"PDF")
            collection.prepare(source, {"id": WORK, "revision": "1"}, root / "paper", "https://mathesis.example")
            manifest = publisher.read(root / "paper/site/mathesis-materials.json")
            self.assertEqual(manifest["reading"], {})
            self.assertTrue((root / "paper/site/index.html").exists())
            self.assertFalse((root / "paper/site/en").exists())


class CollectionPublishing(PublisherTests):
    def test_missing_renamed_repository_never_creates_a_duplicate(self):
        self.make_collection()
        plan = publisher.read(self.stage / "plan.json")
        plan["config"]["repository_aliases"] = {WORK: ["previous-name"]}
        publisher.write(self.source / "publisher.json", plan["config"])
        publisher.write(self.stage / "plan.json", plan)
        files = self.fake.files("catalog")
        files["publisher.json"] = (self.source / "publisher.json").read_bytes()
        self.fake.add_repo("catalog", files)
        self.seal()
        with self.assertRaisesRegex(ValueError, "refusing a duplicate"):
            self.apply()
        self.assertEqual(self.fake.creations, 0)

    def make_collection(self, version=1):
        plan = publisher.read(self.stage / "plan.json")
        plan["collection_works"] = [WORK]
        publisher.write(self.stage / "plan.json", plan)
        root = self.stage / "papers" / WORK
        for path in [root / ".mathesis/book.json", root / "tbook", root / "AGENTS.md"]:
            path.unlink(missing_ok=True)
        publisher.write(root / "paper.json", {"work": {"work": WORK, "repository_name": WORK, "current_version": version}})
        (root / "README.md").write_text("Version " + str(version))
        publisher.write(root / ("versions/v" + str(version)) / "paper.json", {"version": version})
        paths = publisher.tree_files(root)
        publisher.write(root / collection.MARKER, {"schema": collection.SCHEMA, "work": WORK, "version": version,
            "sha256": {p: hashlib.sha256(b).hexdigest() for p, b in paths.items()},
            "git_blobs": {p: publisher.sha(b) for p, b in paths.items()}})
        self.seal()

    def test_collection_repeats_without_issue_permission_or_new_review(self):
        self.make_collection()
        self.fake.fail_issue = True
        self.apply()
        self.assertEqual(self.fake.creations, 1)
        self.assertEqual(self.fake.issues, {})
        self.assertNotIn(".mathesis/book.json", self.fake.files(WORK))
        writes = len(self.fake.writes)
        self.apply()
        self.assertEqual(len(self.fake.writes), writes)

    def test_template_initialization_waits_for_empty_repository_without_recreating_it(self):
        self.make_collection()
        request = self.fake.request
        pending = [409, 404]

        def initialize(method, path, body):
            if method == "GET" and path == f"/repos/mathesis-papers/{WORK}/git/ref/heads/main" and pending:
                return pending.pop(0), {}
            return request(method, path, body)

        with mock.patch.object(self.fake, "request", side_effect=initialize), mock.patch.object(publisher.time, "sleep") as wait:
            self.apply()
        self.assertEqual(self.fake.creations, 1)
        self.assertEqual(wait.call_count, 2)
        self.assertIn("paper.json", self.fake.files(WORK))

    def test_new_version_retains_history_and_unmanaged_material(self):
        self.make_collection()
        self.apply()
        files = self.fake.files(WORK)
        files["notes/collaboration.md"] = b"human notes"
        self.fake.add_repo(WORK, files)
        (self.stage / "papers" / WORK / "versions/v1/paper.json").unlink()
        self.make_collection(version=2)
        self.apply()
        files = self.fake.files(WORK)
        self.assertEqual(files["notes/collaboration.md"], b"human notes")
        self.assertEqual(json.loads(files["versions/v1/paper.json"]), {"version": 1})
        self.assertEqual(json.loads(files["versions/v2/paper.json"]), {"version": 2})

    def test_refresh_preserves_original_version_snapshot_bytes(self):
        self.make_collection()
        self.apply()
        (self.stage / "papers" / WORK / "versions/v1/paper.json").write_text('actual changed projection metadata')
        self.seal()
        self.apply()
        self.assertEqual(json.loads(self.fake.files(WORK)["versions/v1/paper.json"]), {"version": 1})
        self.apply()  # Ownership still matches the retained bytes on a repeat.

    def test_collection_refuses_edited_generated_content(self):
        self.make_collection()
        self.apply()
        files = self.fake.files(WORK)
        files["README.md"] = b"human edit"
        self.fake.add_repo(WORK, files)
        writes = len(self.fake.writes)
        with self.assertRaisesRegex(ValueError, "edited outside publisher"):
            self.apply()
        self.assertEqual(len(self.fake.writes), writes)

    def test_existing_book_is_never_replaced_by_collection(self):
        self.apply()
        self.make_collection()
        writes = len(self.fake.writes)
        with self.assertRaisesRegex(ValueError, "existing book cannot be replaced"):
            self.apply()
        self.assertEqual(len(self.fake.writes), writes)
