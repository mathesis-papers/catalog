"""Serial checkpoints preserve completed work and reject false output reports."""
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
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

    def test_real_film_duration_replaces_estimate_without_changing_other_metadata(self):
        path = self.root / "books/paper/films/001-demo/film.json"
        meta = {"duration": "2:56", "title": "Original title", "lean": ["original.scope"],
                "outputs": {"narrated_720p": "demo.mp4"}}
        runner.write(path, meta)
        output = self.root / "build/films/paper/001-demo/demo.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"probe fixture")
        with patch.object(runner, "command", return_value="173.734"):
            changes = runner.reconcile_film_durations(self.root, "paper", [path])
            self.assertEqual(changes[0]["before"], "2:56")
            self.assertEqual(runner.read(path), dict(meta, duration="2:54"))
            self.assertEqual(runner.reconcile_film_durations(self.root, "paper", [path], update=False), [])
        with patch.object(runner, "command", return_value="180"):
            with self.assertRaisesRegex(ValueError, "changed during"):
                runner.reconcile_film_durations(self.root, "paper", [path], update=False)
        for duration in ("nan", "0"):
            with patch.object(runner, "command", return_value=duration), self.assertRaisesRegex(ValueError, "invalid actual"):
                runner.reconcile_film_durations(self.root, "paper", [path])

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

    def attempt(self, state="local_checks_passed", key="old-template-and-skill", paper=None):
        paper = paper or {"id": "paper", "version": 2, "repository": "mathesis-papers/repo"}
        run = self.root / "runs" / paper["id"] / key
        runner.write(run / "envelope.json", {"identity": {"paper": paper}, "repository_path": str(self.root)})
        runner.write(run / "checkpoint.json", {"state": state})
        return paper, run

    def test_changed_template_reuses_existing_checked_run_without_preparing_or_calling_model(self):
        paper, run = self.attempt()
        with patch.object(runner, "prepare") as prepare, patch.object(runner, "validate_run", return_value={"state": "local_checks_passed"}) as validate, patch.object(runner.subprocess, "run") as execute:
            state = runner.run_paper(self.root, paper, self.root / "new-template")
        validate.assert_called_once_with(self.root, run, build=False)
        prepare.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(state["state"], "local_checks_passed")

    def test_changed_sources_stop_resume_without_another_model_call(self):
        paper, run = self.attempt()
        with patch.object(runner, "prepare") as prepare, patch.object(runner, "validate_run", side_effect=ValueError("sources changed")), patch.object(runner.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "sources changed"):
                runner.run_paper(self.root, paper, self.root)
        prepare.assert_not_called()
        execute.assert_not_called()

    def test_newer_failed_attempt_cannot_fall_back_to_earlier_success(self):
        paper, old = self.attempt()
        _, latest = self.attempt("needs_attention", "second-attempt")
        import os
        os.utime(old / "checkpoint.json", (1, 1))
        with patch.object(runner, "prepare") as prepare, self.assertRaisesRegex(RuntimeError, str(latest)):
            runner.run_paper(self.root, paper, self.root)
        prepare.assert_not_called()

    def test_changed_cohort_version_requires_inspection(self):
        paper, _ = self.attempt()
        with self.assertRaisesRegex(ValueError, "differs from the frozen cohort"):
            runner.previous_attempt(self.root, dict(paper, version=3))

    def test_replaced_render_cannot_be_blessed_by_check_without_rebuild(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("_site/\nrun/\n")
        run = self.root / "run"
        runner.write(run / "result.json", self.result)
        runner.write(run / "source-sha256.json", runner.source_inventory(self.root))
        (self.root / "_site").mkdir()
        page = self.root / "_site/index.html"
        page.write_text("Checked story")
        before = runner.site_inventory(self.root)
        runner.write(run / "site-sha256.json", before)
        page.write_text("Unvalidated replacement")
        with self.assertRaisesRegex(ValueError, "rendered output changed"):
            runner.validate_run(self.root, run)
        self.assertEqual(runner.read(run / "site-sha256.json"), before)

    def test_failed_paper_stops_cohort_and_returns_nonzero(self):
        papers = [{"id": name, "version": 2, "repository": "mathesis-papers/" + name} for name in ("first", "second")]
        runner.write(self.root / "cohort.json", {"schema": "mathesis.serial-cohort.v1", "papers": papers})
        for outcome in ({"state": "needs_attention"}, ValueError("frozen source differs")):
            with self.subTest(outcome=str(outcome)):
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with patch.object(sys, "argv", ["serial_content.py", "run", "--workspace", str(self.root), "--template", str(self.root)]), patch.object(runner, "run_paper", **kwargs) as run:
                    with self.assertRaises(SystemExit) as error:
                        runner.main()
                self.assertEqual(error.exception.code, 1)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[1]["id"], "first")
                self.assertEqual(runner.read(self.root / "runs/first/intake-error.json")["state"], "needs_attention")

    def test_failed_rebuild_replaces_success_and_can_resume_without_a_model(self):
        paper, run = self.attempt()
        runner.write(self.root / "cohort.json", {"schema": "mathesis.serial-cohort.v1", "papers": [paper]})
        def failed_build(*args, **kwargs):
            self.assertEqual(runner.read(run / "checkpoint.json")["state"], "rebuilding_products")
            raise subprocess.CalledProcessError(1, ["build", "films"])
        with patch.object(runner, "validate_run", side_effect=failed_build), patch.object(runner, "preview_index") as preview:
            with self.assertRaises(subprocess.CalledProcessError):
                runner.recheck(self.root, run, build=True)
            preview.assert_called_once()
        self.assertEqual(runner.read(run / "checkpoint.json")["state"], "needs_attention")
        with patch.object(runner, "validate_run", return_value={"state": "local_checks_passed"}), patch.object(runner, "preview_index"), patch.object(runner.subprocess, "run") as model:
            self.assertEqual(runner.recheck(self.root, run, build=True)["state"], "local_checks_passed")
            model.assert_not_called()
        history = [json.loads(line)["checkpoint"]["state"] for line in (run / "check-history.jsonl").read_text().splitlines()]
        self.assertEqual(history, ["local_checks_passed", "needs_attention"])

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
