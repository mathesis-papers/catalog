"""Serial checkpoints preserve completed work and reject false output reports."""
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("serial_content", Path(__file__).parents[1] / "scripts/serial_content.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class SerialContent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "story.md").write_text("A real source file")
        self.result = {"status": "prepared", "summary": "Bilingual source prepared",
                       "visualization": {"decision": "not_useful", "reason": "The six-case table explains the mechanism."},
                       "outputs": ["story.md"], "checks": [], "requests": [],
                       "doi_observation": "Existing version DOI retained", "remaining": ["Video not rendered"]}

    def test_deliberately_omitted_interaction_is_valid(self):
        runner.check_result(self.result, self.root)

    def test_markdown_report_is_not_a_file(self):
        self.result["outputs"] = ["Story: [read it](story.md)"]
        with self.assertRaisesRegex(ValueError, "raw repository-relative"):
            runner.check_result(self.result, self.root)

    def test_external_files_and_missing_files_are_refused(self):
        for path in (str(self.root / "story.md"), "../outside.md", "missing.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.result["outputs"] = [path]
                runner.check_result(self.result, self.root)

    def test_issue_prose_or_a_pr_is_not_a_request(self):
        for url in ("Request: https://github.com/the-omega-institute/trureturing-requests/issues/13",
                    "https://github.com/the-omega-institute/trureturing-requests/pull/13"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "noncanonical"):
                self.result["requests"] = [url]
                runner.check_result(self.result, self.root)

    def test_pending_interaction_cannot_be_reported_prepared(self):
        self.result["visualization"]["decision"] = "pending"
        with self.assertRaisesRegex(ValueError, "still lacks"):
            runner.check_result(self.result, self.root)

    def test_bilingual_products_do_not_require_interaction(self):
        material = {"reading": {"en": "en/", "zh": "zh/"},
                    "slides": [{"language": lang, "pdf_url": lang + ".pdf", "notes_url": lang + "-notes.pdf"}
                               for lang in ("en", "zh")], "visualizations": []}
        runner.check_material_products(material, self.result["visualization"])
        with self.assertRaisesRegex(ValueError, "reported built"):
            runner.check_material_products(material, {"decision": "built"})
        material["visualizations"] = [{"url": "viz/"}]
        with self.assertRaisesRegex(ValueError, "judgment"):
            runner.check_material_products(material, self.result["visualization"])

    def test_duplicate_pdfs_in_one_language_cannot_pass_bilingual_checks(self):
        material = {"reading": {"en": "en/", "zh": "zh/"},
                    "slides": [{"language": "en", "pdf_url": "en.pdf", "notes_url": "notes.pdf"}] * 4}
        with self.assertRaisesRegex(ValueError, "bilingual audience"):
            runner.check_material_products(material, self.result["visualization"])
        material["slides"].append({"language": "zh", "pdf_url": "zh.pdf"})
        with self.assertRaisesRegex(ValueError, "speaker notes"):
            runner.check_material_products(material, self.result["visualization"])

    def test_workspace_cannot_have_two_workers(self):
        with runner.exclusive(self.root):
            with self.assertRaisesRegex(RuntimeError, "another serial worker"):
                with runner.exclusive(self.root):
                    self.fail("second lock was acquired")

    def test_completed_checkpoint_never_calls_model_again(self):
        run = self.root / "run"
        runner.write(run / "checkpoint.json", {"state": "local_checks_passed"})
        with patch.object(runner, "prepare", return_value=(self.root, run, {})), patch.object(runner.subprocess, "run") as execute:
            runner.run_paper(self.root, {"id": "paper"}, self.root)
        execute.assert_not_called()

    def test_interrupted_attempt_is_not_blindly_restarted(self):
        run = self.root / "run"
        runner.write(run / "checkpoint.json", {"state": "authoring"})
        with patch.object(runner, "prepare", return_value=(self.root, run, {})), patch.object(runner.subprocess, "run") as execute:
            with self.assertRaisesRegex(RuntimeError, "existing attempt requires inspection"):
                runner.run_paper(self.root, {"id": "paper"}, self.root)
        execute.assert_not_called()

    def test_cohort_rejects_duplicate_or_wrong_destination(self):
        paper = {"id": "paper", "version": 2, "repository": "mathesis-papers/repo"}
        runner.validate_cohort({"schema": "mathesis.serial-cohort.v1", "papers": [paper]})
        with self.assertRaises(ValueError):
            runner.validate_cohort({"schema": "mathesis.serial-cohort.v1", "papers": [paper, paper]})
        with self.assertRaises(ValueError):
            runner.validate_cohort({"schema": "mathesis.serial-cohort.v1", "papers": [dict(paper, repository="other/repo")]})

    def test_source_binding_detects_changes_but_ignores_build_caches(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("build/\n")
        before = runner.source_inventory(self.root)
        (self.root / "build").mkdir()
        (self.root / "build/render.pdf").write_bytes(b"a rendering")
        self.assertEqual(runner.source_inventory(self.root), before)
        (self.root / "story.md").write_text("A new, unbuilt explanation")
        self.assertNotEqual(runner.source_inventory(self.root), before)

    def test_source_binding_hashes_the_symlink_itself_and_executable_mode(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "shared-skill").symlink_to("story.md")
        before = runner.source_inventory(self.root)
        self.assertEqual(before["shared-skill"]["mode"], "120000")
        (self.root / "story.md").chmod(0o755)
        self.assertNotEqual(runner.source_inventory(self.root), before)


if __name__ == "__main__":
    unittest.main()
