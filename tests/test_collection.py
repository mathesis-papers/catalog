"""Independent publication preserves papers without asking a model to review them."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from test_publisher import PublisherTests, WORK, publisher

collection = publisher.collection


class CollectionRendering(unittest.TestCase):
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
