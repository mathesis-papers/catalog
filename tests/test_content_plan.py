"""Local content plans fence versions, reuse unchanged inputs and refuse missing outputs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("plan_content", Path(__file__).parents[1] / "scripts/plan_content.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)
spec = importlib.util.spec_from_file_location("collection", Path(__file__).parents[1] / "scripts/collection.py")
collection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collection)


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

    def collect(self, languages=("en", "zh")):
        (self.root / ".mathesis/book.json").unlink(missing_ok=True)
        self.manifest["reading"] = {lang: self.manifest["site_url"] + lang + "/" for lang in languages}
        self.write("site/mathesis-materials.json", self.manifest)
        for lang in languages:
            self.write("site/" + lang + "/index.html", "<!doctype html><html lang='" + lang + "'><p>Existing explanation</p></html>")
        self.seal_collection()
        shutil.copytree(self.root / "site", self.site, dirs_exist_ok=True)

    def seal_collection(self):
        files = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in (self.root / "site").rglob("*") if p.is_file()}
        files["paper.json"] = (self.root / "paper.json").read_bytes()
        (self.root / collection.MARKER).write_bytes(collection.ownership(files, "paper", 2))

    def test_collection_reuses_existing_bilingual_readings_without_a_book(self):
        self.collect()
        pending = planner.plan(self.root)
        self.assertEqual(pending["units"][0]["state"], "needs_build_or_validation")
        built = planner.plan(self.root, self.site)
        self.assertEqual(built["units"][0]["state"], "built")
        self.assertEqual([u["state"] for u in built["units"][1:]], ["needs_preparation", "needs_preparation"])
        self.assertEqual(built, planner.plan(self.root, self.site))
        # A refreshed, owned explanation invalidates derived media inputs.
        self.write("site/en/index.html", "<html><p>Updated published explanation</p></html>")
        self.seal_collection()
        with self.assertRaisesRegex(ValueError, "built reading differs"):
            planner.plan(self.root, self.site)
        shutil.copytree(self.root / "site", self.site, dirs_exist_ok=True)
        updated = planner.plan(self.root, self.site)
        self.assertEqual(updated["units"][0]["state"], "built")
        self.assertEqual(built["units"][0]["input_key"], updated["units"][0]["input_key"])
        self.assertNotEqual(built["units"][1]["input_key"], updated["units"][1]["input_key"])

    def test_collection_with_one_language_still_needs_preparation(self):
        self.collect(("en",))
        self.assertEqual(planner.plan(self.root, self.site)["units"][0]["state"], "needs_preparation")

    def test_collection_rejects_stale_foreign_and_unowned_readings(self):
        self.collect()
        marker_path = self.root / collection.MARKER
        original = json.loads(marker_path.read_text())
        for field, value in (("work", "another"), ("version", 1), ("schema", "another")):
            self.write(collection.MARKER, dict(original, **{field: value}))
            with self.assertRaisesRegex(ValueError, "another paper version"):
                planner.plan(self.root, self.site)
        self.write(collection.MARKER, original)
        self.write("site/en/index.html", "<html>Untracked edit</html>")
        with self.assertRaisesRegex(ValueError, "bytes differ or are unowned"):
            planner.plan(self.root, self.site)
        self.seal_collection()
        marker = json.loads(marker_path.read_text())
        del marker["sha256"]["site/en/index.html"]
        self.write(collection.MARKER, marker)
        with self.assertRaisesRegex(ValueError, "bytes differ or are unowned"):
            planner.plan(self.root, self.site)

    def test_collection_rejects_foreign_manifest_and_symlinked_source(self):
        self.collect()
        self.manifest["version"] = 1
        self.write("site/mathesis-materials.json", self.manifest)
        self.seal_collection()
        with self.assertRaisesRegex(ValueError, "another paper version"):
            planner.plan(self.root, self.site)
        self.manifest["version"] = 2
        self.manifest["reading"]["en"] = "https://elsewhere.test/en/"
        self.write("site/mathesis-materials.json", self.manifest)
        self.seal_collection()
        with self.assertRaisesRegex(ValueError, "reading address differs"):
            planner.plan(self.root, self.site)
        self.manifest["reading"]["en"] = self.manifest["site_url"] + "en/"
        self.write("site/mathesis-materials.json", self.manifest)
        self.seal_collection()
        source = self.root / "site/en/index.html"
        source.unlink()
        source.symlink_to(self.site / "en/index.html")
        with self.assertRaisesRegex(ValueError, "symlink"):
            planner.plan(self.root, self.site)

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
