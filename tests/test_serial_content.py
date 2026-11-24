"""Serial checkpoints preserve completed work and reject false output reports."""
import importlib.util
import io
import json
import os
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

    def test_stalled_inspector_is_terminated_and_reported_as_a_timeout(self):
        args=[sys.executable, '-c', 'import time; print("inspection started",flush=True); time.sleep(60)']
        with (self.root/'inspection.log').open('w+') as log:
            with self.assertRaises(subprocess.TimeoutExpired) as error:
                runner.inspect_products(args, self.root, dict(os.environ), log, timeout=0.2)
            self.assertEqual(error.exception.cmd, args)
            self.assertEqual(error.exception.timeout, 0.2)
            log.seek(0)
            self.assertIn('inspection started', log.read())
        with self.assertRaises(subprocess.CalledProcessError) as error:
            runner.inspect_products([sys.executable,'-c','raise SystemExit(7)'],self.root,dict(os.environ),subprocess.DEVNULL)
        self.assertEqual(error.exception.returncode,7)

    def test_live_stage_retains_completed_work_and_identifies_the_unfinished_check(self):
        runner.write(self.root/'checkpoint.json', {'state':'rebuilding_products'})
        private_command=['python','/private/worktree/opaque-operation/renderer.py']
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            with runner.host_stage(self.root, 'Paper layout check'):
                pass
            with self.assertRaisesRegex(ValueError, 'render failed'):
                with runner.host_stage(self.root, 'Render narrated film', private_command):
                    raise ValueError('render failed')
        checkpoint=runner.read(self.root/'checkpoint.json')
        self.assertEqual([c['name'] for c in checkpoint['completed_checks']], ['Paper layout check'])
        self.assertEqual(checkpoint['active_check']['name'], 'Render narrated film')
        self.assertEqual(checkpoint['active_check']['command'], private_command)
        self.assertIn('Material stage completed: Paper layout check', output.getvalue())
        self.assertIn('Material stage started: Render narrated film', output.getvalue())
        self.assertNotIn('opaque-operation', output.getvalue())
        self.assertNotIn('Material stage completed: Render narrated film', output.getvalue())

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

    def test_english_slides_are_sufficient_but_reading_stays_bilingual(self):
        material = {"reading": {"en": "en/", "zh": "zh/"},
                    "slides": [{"language": "en", "pdf_url": "en.pdf", "notes_url": "notes.pdf"}]}
        runner.check_material_products(material, self.result["visualization"])
        material['reading'].pop('zh')
        with self.assertRaisesRegex(ValueError, "bilingual reading"):
            runner.check_material_products(material, self.result["visualization"])

    def test_english_audience_and_notes_are_both_required(self):
        material = {"reading": {"en": "en/", "zh": "zh/"},
                    "slides": [{"language": "zh", "pdf_url": "zh.pdf", "notes_url": "notes.pdf"}] * 4}
        with self.assertRaisesRegex(ValueError, "English audience"):
            runner.check_material_products(material, self.result["visualization"])
        material["slides"].append({"language": "en", "pdf_url": "en.pdf"})
        with self.assertRaisesRegex(ValueError, "speaker notes"):
            runner.check_material_products(material, self.result["visualization"])

    def test_epub_validation_rejects_missing_editions_and_successful_skips(self):
        config = self.root / "books/paper/book.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[book]\nversion="2"\neditions=["zh","en"]\n[outputs]\nepub=true\n')
        with (self.root / "checks.log").open("w") as log:
            with self.assertRaisesRegex(ValueError, "editions are not built"):
                runner.validate_epubs(self.root, "paper", dict(os.environ), log)
            outputs = self.root / "build/dist/paper"
            outputs.mkdir(parents=True)
            for lang in ("zh", "en"):
                (outputs / f"paper-2-{lang}.epub").write_bytes(b"existing product")
            tools = self.root / "tools/tbook"
            tools.mkdir(parents=True)
            (tools / "__init__.py").write_text("")
            checker = tools / "pandoc.py"
            checker.write_text('from types import SimpleNamespace\ndef check_epub(path):\n'
                               '    return SimpleNamespace(valid=True, skipped="epubcheck not installed", errors=[])\n')
            java = self.root / "bin/java"
            java.parent.mkdir()
            java.write_text("#!/bin/sh\nexit 0\n")
            java.chmod(0o755)
            env = dict(os.environ, PATH=str(java.parent) + os.pathsep + os.environ.get("PATH", ""))
            with self.assertRaises(subprocess.CalledProcessError):
                runner.validate_epubs(self.root, "paper", env, log)
            checker.write_text(checker.read_text().replace('skipped="epubcheck not installed"', 'skipped=""'))
            runner.validate_epubs(self.root, "paper", env, log)
        checked = (self.root / "checks.log").read_text()
        self.assertIn("EPUB not validated", checked)
        self.assertIn("paper-2-zh.epub", checked)
        self.assertIn("paper-2-en.epub", checked)

    def test_workspace_cannot_have_two_workers(self):
        with runner.exclusive(self.root):
            with self.assertRaisesRegex(RuntimeError, "another serial worker"):
                with runner.exclusive(self.root):
                    self.fail("second lock was acquired")

    def test_shared_tools_stay_untracked_in_checkout_and_worktree(self):
        source = self.root / "source"
        subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
        (source / ".mathesis").mkdir()
        (source / ".mathesis/book.json").write_text("{}")
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                        "-c", "commit.gpgsign=false", "commit", "-m", "Fixture"],
                       cwd=source, check=True, capture_output=True)
        worktree = self.root / "worktree"
        subprocess.run(["git", "worktree", "add", "-b", "test", str(worktree)],
                       cwd=source, check=True, capture_output=True)
        tools = self.root / "tools"
        for name in (".venv", "node_modules"):
            (tools / name).mkdir(parents=True)
        for checkout in (source, worktree):
            with self.subTest(checkout=checkout.name):
                runner.authored_workspace(self.root, {"id": "paper"}, self.root,
                                          checkout, self.root, tools)
                self.assertTrue((checkout / ".venv").is_symlink())
                ignored = runner.command(["git", "check-ignore", ".venv", "node_modules"], checkout)
                self.assertEqual(set(ignored.splitlines()), {".venv", "node_modules"})
                self.assertEqual(runner.command(["git", "status", "--porcelain"], checkout), "")

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

    def test_new_film_measures_narration_before_first_render(self):
        path = self.root / "books/paper/films/001-demo/film.json"
        meta = {"duration": "2:56", "title": "Original title", "lean": ["original.scope"],
                "outputs": {"narrated_720p": "demo.mp4"}}
        runner.write(path, meta)
        def synthesize(args, **kwargs):
            self.assertEqual(args, ["theme/film/film.sh", str(path.parent), "tts"])
            runner.write(path.parent / "out/timeline.json", {"duration": 173.734})
        with patch.object(runner.subprocess, "run", side_effect=synthesize) as execute:
            changes = runner.measure_new_film_durations(self.root, "paper", [path], io.StringIO(), {})
        execute.assert_called_once()
        self.assertEqual(changes[0]["narration_seconds"], 173.734)
        self.assertEqual(runner.read(path), dict(meta, duration="2:54"))
        self.assertFalse((self.root / "build").exists())

    def test_existing_video_skips_narration_preflight_even_with_stale_timeline(self):
        path = self.root / "books/paper/films/001-demo/film.json"
        runner.write(path, {"duration": "2:56", "outputs": {"narrated_720p": "demo.mp4"}})
        runner.write(path.parent / "out/timeline.json", {"duration": 1})
        output = self.root / "build/films/paper/001-demo/demo.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"existing output; normal build and final probe remain required")
        with patch.object(runner.subprocess, "run") as execute:
            self.assertEqual(runner.measure_new_film_durations(self.root, "paper", [path], io.StringIO(), {}), [])
        execute.assert_not_called()
        self.assertEqual(runner.read(path)["duration"], "2:56")

    def test_invalid_narration_cannot_replace_declared_duration(self):
        path = self.root / "books/paper/films/001-demo/film.json"
        meta = {"duration": "2:56", "outputs": {"narrated_720p": "demo.mp4"}}
        runner.write(path, meta)
        for seconds in (float("nan"), 0):
            with self.subTest(seconds=seconds):
                def synthesize(*args, **kwargs):
                    runner.write(path.parent / "out/timeline.json", {"duration": seconds})
                with patch.object(runner.subprocess, "run", side_effect=synthesize), self.assertRaisesRegex(ValueError, "invalid measured narration"):
                    runner.measure_new_film_durations(self.root, "paper", [path], io.StringIO(), {})
                self.assertEqual(runner.read(path), meta)

    def test_completed_checkpoint_never_calls_model_again(self):
        run = self.root / "run"
        runner.write(run / "checkpoint.json", {"state": "local_checks_passed"})
        with patch.object(runner, "prepare", return_value=(self.root, run, {})), patch.object(runner.subprocess, "run") as execute:
            runner.run_paper(self.root, {"id": "paper"}, self.root)
        execute.assert_not_called()

    def test_source_inspection_checkpoint_does_not_render_or_claim_validation(self):
        run = self.root / "run"
        runner.write(run / "result.json", self.result)
        (run / "prompt.txt").write_text("Prepare this paper")
        with patch.object(runner, "prepare", return_value=(self.root, run, {})), \
                patch.object(runner.subprocess, "run") as author, \
                patch.object(runner, "validate_run") as render, \
                patch.object(runner, "preview_index"):
            author.return_value.returncode = 0
            runner.write(self.root / "cohort.json", {})
            state = runner.run_paper(self.root, {"id": "paper"}, self.root, source_only=True)
        self.assertEqual(author.call_count, 1)
        render.assert_not_called()
        self.assertEqual(state["state"], "sources_prepared")
        self.assertNotIn("source_sha256", state)
        self.assertFalse((run / "site-sha256.json").exists())
        self.assertEqual(state["publication"], "not_performed")
        self.assertEqual(runner.read(run / "checkpoint.json"), state)

    def test_source_inspection_still_rejects_missing_or_unfinished_outputs(self):
        for result in (dict(self.result, outputs=["missing.md"]), dict(self.result, status="needs_attention")):
            with self.subTest(result=result):
                run = self.root / str(len(list(self.root.iterdir())))
                runner.write(run / "result.json", result)
                (run / "prompt.txt").write_text("Prepare this paper")
                runner.write(self.root / "cohort.json", {})
                with patch.object(runner, "prepare", return_value=(self.root, run, {})), \
                        patch.object(runner.subprocess, "run") as author, \
                        patch.object(runner, "validate_run") as render, \
                        patch.object(runner, "preview_index"):
                    author.return_value.returncode = 0
                    state = runner.run_paper(self.root, {"id": "paper"}, self.root, source_only=True)
                render.assert_not_called()
                self.assertEqual(state["state"], "needs_attention")

    def test_source_only_cli_requires_a_single_paper_and_accepts_unrendered_sources(self):
        args = ["serial_content.py", "run", "--workspace", str(self.root), "--template", str(self.root), "--source-only"]
        with patch.object(sys, "argv", args), self.assertRaises(SystemExit) as error:
            runner.main()
        self.assertEqual(error.exception.code, 2)
        paper = {"id": "paper", "version": 2, "repository": "mathesis-papers/paper"}
        runner.write(self.root / "cohort.json", {"schema": "mathesis.serial-cohort.v1", "papers": [paper]})
        with patch.object(sys, "argv", args + ["--paper", "paper"]), \
                patch.object(runner, "run_paper", return_value={"state": "sources_prepared"}) as author:
            runner.main()
        self.assertTrue(author.call_args.kwargs["source_only"])

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

    def checked_material(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("_site/\nrun/\n")
        run = self.root / "run"
        runner.write(run / "result.json", self.result)
        runner.write(self.root / "_site/mathesis-materials.json", {
            "reading": {"en": "en/", "zh": "zh/"},
            "slides": [{"language": "en", "pdf_url": "slides.pdf", "notes_url": "notes.pdf"}],
            "visualizations": []})
        sources = runner.source_inventory(self.root)
        runner.write(run / "source-sha256.json", sources)
        runner.write(run / "site-sha256.json", runner.site_inventory(self.root))
        receipt = {"state": "local_checks_passed", "source_sha256": runner.digest(sources),
                   "visualization": self.result["visualization"], "completed_checks": [
                       {"name": name, "finished_at": "2026-11-24T00:00:00Z"} for name in
                       ("Validate EPUB editions", "Inspect rendered pages", "Inspect PDFs, slides and video")]}
        runner.write(run / "checkpoint.json", receipt)
        (run / "operator-checks.log").write_text("Original container evidence\n")
        return run, receipt

    def test_host_readback_reuses_exact_container_checks_without_tools_or_receipt_writes(self):
        run, receipt = self.checked_material()
        before = {p.name: p.read_bytes() for p in run.iterdir()}
        execute = subprocess.run
        def git_only(args, **kwargs):
            self.assertEqual(args[0], "git", "publication readback must not rerun container tools")
            return execute(args, **kwargs)
        with patch.object(runner, "inspect_products") as inspect, \
                patch.object(runner, "validate_epubs") as epub, \
                patch.object(runner.subprocess, "run", side_effect=git_only):
            self.assertEqual(runner.checked_run(self.root, run), receipt)
        inspect.assert_not_called()
        epub.assert_not_called()
        self.assertEqual({p.name: p.read_bytes() for p in run.iterdir()}, before)

    def test_host_readback_refuses_changed_content_or_incomplete_container_checks(self):
        run, receipt = self.checked_material()
        for changed in [dict(receipt, state="needs_attention"),
                        dict(receipt, completed_checks=receipt["completed_checks"][:-1]),
                        dict(receipt, source_sha256="0" * 64),
                        dict(receipt, last_failure={"reason": "publication failed"}),
                        dict(receipt, visualization={"decision": "built"})]:
            with self.subTest(changed=changed):
                runner.write(run / "checkpoint.json", changed)
                with self.assertRaisesRegex(ValueError, "receipt is incomplete or differs"):
                    runner.checked_run(self.root, run)
        runner.write(run / "checkpoint.json", receipt)
        page = self.root / "_site/mathesis-materials.json"
        original = page.read_bytes()
        page.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "rendered output changed"):
            runner.checked_run(self.root, run)
        page.write_bytes(original)
        (self.root / "story.md").write_text("Unbuilt source revision")
        with self.assertRaisesRegex(ValueError, "sources changed"):
            runner.checked_run(self.root, run)

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
