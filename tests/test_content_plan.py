"""Local content plans fence versions, reuse unchanged inputs and refuse missing outputs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("plan_content", Path(__file__).parents[1] / "scripts/plan_content.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


class LocalPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paper = {"work": {"work": "paper", "current_version": 2, "repository_name": "repo"}, "files": {"source": {"sha256": "a" * 64}}}
        self.write("paper.json", self.paper)
        self.write(".mathesis/generated.json", {"sha256": {"paper.json": hashlib.sha256((self.root / "paper.json").read_bytes()).hexdigest()}})
        self.write(".mathesis/book.json", {"work": "paper", "version": 2, "book": "paper", "stage": "complete", "pending_claims": []})
        for lang in ("en", "zh"):
            self.write("books/paper/" + lang + "/README.md", "# A readable story\n\nThe question, result, mechanism and limits.")
        self.site = self.root / "_site"
        self.manifest = {"schema": "mathesis.materials.v1", "work": "paper", "version": 2,
                         "site_url": "https://papers.trureturing.org/repo/", "repository_url": "https://github.com/mathesis-papers/repo", "reading": {}, "slides": [], "videos": []}
        self.write("_site/mathesis-materials.json", self.manifest)

    def write(self, path, data):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data if isinstance(data, str) else json.dumps(data))

    def test_unchanged_plan_is_stable_and_media_inputs_follow_story(self):
        one = planner.plan(self.root, self.site)
        self.assertEqual(one, planner.plan(self.root, self.site))
        self.assertEqual([u["state"] for u in one["units"]], ["needs_build_or_validation", "needs_preparation", "needs_preparation"])
        self.write("books/paper/en/README.md", "# Revised explanation\n\nThe manuscript has not changed.")
        two = planner.plan(self.root, self.site)
        self.assertEqual(one["units"][0]["input_key"], two["units"][0]["input_key"])
        self.assertNotEqual(one["units"][1]["input_key"], two["units"][1]["input_key"])

    def test_changed_version_missing_outputs_and_wrong_format_are_refused(self):
        for field, value in (("version", 1), ("site_url", "https://evil.test/")):
            changed = dict(self.manifest, **{field: value})
            self.write("_site/mathesis-materials.json", changed)
            with self.assertRaises(ValueError): planner.plan(self.root, self.site)
        self.manifest["videos"] = [{"url": self.manifest["site_url"] + "films/", "mp4_url": self.manifest["site_url"] + "video.mp4"}]
        self.write("_site/mathesis-materials.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "not built"): planner.plan(self.root, self.site)
        self.write("_site/films/index.html", "<html>Film</html>")
        self.write("_site/video.mp4", "not a film")
        with self.assertRaisesRegex(ValueError, "wrong format"): planner.plan(self.root, self.site)
        self.write("paper.json", dict(self.paper, work=dict(self.paper["work"], current_version=3)))
        with self.assertRaisesRegex(ValueError, "frozen paper bytes differ"): planner.plan(self.root, self.site)
