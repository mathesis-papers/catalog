"""A missing film must not rewrite checked mathematics or repeat completed work."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import produce_video as video
import serial_content as serial


class MissingVideo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paper = {"id": "example", "version": 2, "repository": "mathesis-papers/example"}
        self.run = self.root / "runs/example/key"
        self.repo = self.root / "authored/example"
        serial.write(self.root / "cohort.json", {"schema": "mathesis.serial-cohort.v1", "papers": [self.paper]})
        serial.write(self.run / "envelope.json", {"identity": {"paper": self.paper}, "repository_path": str(self.repo), "source_repository_path": str(self.root / "source")})
        serial.write(self.run / "checkpoint.json", {"state": "local_checks_passed"})
        serial.write(self.run / "content-plan.json", {"units": [{"unit": "video", "state": "needs_preparation"}]})
        serial.write(self.repo / ".mathesis/book.json", {"book": "example"})
        for lang in ("en", "zh"):
            page = self.repo / f"books/example/{lang}/README.md"
            page.parent.mkdir(parents=True)
            page.write_text("# Checked story\n\nThe existing mathematical argument.\n")

    def test_scope_allows_film_but_rejects_rewriting_paper_or_lean_mapping(self):
        before = {"paper.json": "original DOI", ".mathesis/book.json": "checked correspondence"}
        video.check_scope(before, dict(before, **{"books/example/films/001-intro/scenes.js": "new"}), "books/example/films/")
        for name in before:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "non-film sources"):
                video.check_scope(before, dict(before, **{name: "changed"}), "books/example/films/")
        with self.assertRaisesRegex(ValueError, "non-film sources"):
            video.check_scope(before, {}, "books/example/films/")

    def test_embedding_preserves_both_stories_and_is_idempotent(self):
        original = {lang: (self.repo / f"books/example/{lang}/README.md").read_text() for lang in ("en", "zh")}
        video.embed(self.repo, "example", "001-intro")
        once = {lang: (self.repo / f"books/example/{lang}/README.md").read_text() for lang in ("en", "zh")}
        video.embed(self.repo, "example", "001-intro")
        for lang in ("en", "zh"):
            result = (self.repo / f"books/example/{lang}/README.md").read_text()
            self.assertTrue(result.startswith(original[lang].rstrip()))
            self.assertEqual(result, once[lang])
            self.assertEqual(result.count('::: {.film src="001-intro"}'), 1)

    def test_existing_checked_video_never_starts_another_model(self):
        serial.write(self.run / "content-plan.json", {"units": [{"unit": "video", "state": "built"}]})
        with patch.object(serial, "validate_run"), patch.object(video.subprocess, "run") as execute:
            state = video.run(self.root, self.paper, self.root)
        self.assertEqual(state["state"], "local_checks_passed")
        execute.assert_not_called()
        self.assertFalse((self.run / "video").exists())

    def test_prior_failed_product_is_not_blindly_restarted(self):
        (self.run / "video").mkdir()
        with patch.object(serial, "validate_run"), patch.object(video.subprocess, "run") as execute:
            with self.assertRaisesRegex(RuntimeError, "existing film attempt"):
                video.run(self.root, self.paper, self.root)
        execute.assert_not_called()

    def test_unfinished_base_does_not_admit_more_work(self):
        serial.write(self.run / "checkpoint.json", {"state": "authoring"})
        with patch.object(video.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "finish and check"):
                video.run(self.root, self.paper, self.root)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
