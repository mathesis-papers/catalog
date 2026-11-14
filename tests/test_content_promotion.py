"""A local authored-content migration must preserve original papers and history."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collection
import promote_content as promotion


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


class Promotion(unittest.TestCase):
    def setUp(self):
        self.work = {"work": "example", "current_version": 2, "doi": None,
                     "title": "An original theorem", "authors": [{"name": "Original author"}]}
        self.original = {"paper.json": encoded({"work": self.work, "files": {"source": {"path": "source.archive"}}}),
                         "README.md": b"Original credited catalog prefix", "source.archive": b"original source archive",
                         "paper.pdf": b"original paper PDF", "versions/v1/paper.pdf": b"first paper PDF",
                         "versions/v2/paper.pdf": b"original paper PDF", "site/index.html": b"old landing"}
        self.generated = {"sha256": {k: hashlib.sha256(self.original[k]).hexdigest()
                                     for k in ("paper.json", "README.md", "source.archive", "paper.pdf")}}
        self.original[".mathesis/generated.json"] = encoded(self.generated)
        self.original[collection.MARKER] = collection.ownership(self.original, "example", 2)
        selected = json.loads(self.original["paper.json"])
        selected["files"]["source"].update(path="manuscript/", original_path="manuscript/original-source.archive")
        self.authored = {"paper.json": encoded(selected), "paper.pdf": self.original["paper.pdf"],
                         "README.md": self.original["README.md"] + b"\n<!-- Mathesis generated repository guide; refresh with paper.py front. -->\nA reader guide",
                         "manuscript/original-source.archive": self.original["source.archive"],
                         ".mathesis/generated.json": b"adapted publisher manifest",
                         ".mathesis/book.json": encoded({"work": "example", "version": 2}),
                         "books/example/en/README.md": b"A substantial mathematical story"}

    def tree(self, value):
        return {k: (v, "100644") for k, v in value.items()}

    def migrate(self):
        return promotion.migration_tree(self.tree(self.original), self.tree(self.authored), "example", 2, "a" * 40)

    def test_preserves_every_historical_byte_and_original_authorship(self):
        result = self.migrate()
        self.assertNotIn(collection.MARKER, result)
        self.assertNotIn("site/index.html", result)
        self.assertNotIn("source.archive", result)
        self.assertEqual(result["manuscript/original-source.archive"][0], b"original source archive")
        for name in ("versions/v1/paper.pdf", "versions/v2/paper.pdf"):
            self.assertEqual(result[name][0], self.original[name])
        history = json.loads(result[promotion.HISTORY][0])
        self.assertEqual(history["source_commit"], "a" * 40)
        self.assertEqual(set(history["sha256"]), {"versions/v1/paper.pdf", "versions/v2/paper.pdf"})
        self.assertEqual(json.loads(result["paper.json"][0])["work"], self.work)

    def test_unrelated_authored_files_survive_and_conflicts_need_review(self):
        self.original["personal.txt"] = b"An existing collaborator note"
        self.assertEqual(self.migrate()["personal.txt"][0], self.original["personal.txt"])
        self.authored["personal.txt"] = b"would overwrite a collaborator"
        with self.assertRaisesRegex(ValueError, "unrelated file conflicts"):
            self.migrate()

    def test_edited_history_is_not_blessed_by_a_new_inventory(self):
        self.original["versions/v1/paper.pdf"] = b"changed after publication"
        with self.assertRaisesRegex(ValueError, "edited outside publisher"):
            self.migrate()

    def test_doi_identity_and_frozen_source_cannot_change(self):
        value = json.loads(self.authored["paper.json"])
        value["work"]["doi"] = "10.1234/a-different-paper"
        self.authored["paper.json"] = encoded(value)
        with self.assertRaisesRegex(ValueError, "identity or DOI changed"):
            self.migrate()
        value["work"]["doi"] = None
        self.authored["paper.json"] = encoded(value)
        self.authored["paper.pdf"] = b"unreviewed replacement PDF"
        with self.assertRaisesRegex(ValueError, "changed original bytes"):
            self.migrate()

    def test_a_new_authoring_run_cannot_overwrite_old_versions(self):
        self.authored["versions/v1/paper.pdf"] = b"a replacement"
        with self.assertRaisesRegex(ValueError, "rewrites historical snapshot"):
            self.migrate()

    def test_symlinks_and_path_traversal_are_not_frozen_papers(self):
        tree = self.tree(self.original)
        tree["paper.pdf"] = (b"/etc/passwd", "120000")
        with self.assertRaisesRegex(ValueError, "linked collection"):
            promotion.owned_collection(tree, "example", 2)
        for name in ("../paper.pdf", "/paper.pdf", "versions/../paper.pdf", ".git/config"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unsafe tracked"):
                promotion.safe_name(name)


if __name__ == "__main__":
    unittest.main()
