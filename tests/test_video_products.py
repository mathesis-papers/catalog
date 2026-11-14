"""Actual narrated media must decode; a valid silent-video container is insufficient."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("paper_products", Path(__file__).parents[1] / "scripts/paper_products.py")
products = importlib.util.module_from_spec(spec)
spec.loader.exec_module(products)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "video checks require ffmpeg/ffprobe")
class ActualVideo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "film.mp4"

    def render(self, audio):
        args = ["ffmpeg", "-v", "error", "-threads", "1", "-f", "lavfi", "-i", "color=c=white:s=640x360:r=2"]
        if audio:
            args.extend(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"])
        args.extend(["-t", "1.25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1"])
        if audio:
            args.extend(["-c:a", "aac"])
        subprocess.run([*args, str(self.path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def test_actual_audiovisual_file_decodes(self):
        self.render(audio=True)
        facts = products.video_facts(self.path)
        self.assertEqual((facts["width"], facts["height"]), (640, 360))
        self.assertEqual(facts["audio_codec"], "aac")
        self.assertGreaterEqual(facts["duration_seconds"], 1)

    def test_video_without_narration_is_rejected(self):
        self.render(audio=False)
        with self.assertRaisesRegex(ValueError, "narration audio"):
            products.video_facts(self.path)

    def test_mp4_header_is_not_playable_media(self):
        self.path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200)
        with self.assertRaises(subprocess.CalledProcessError):
            products.video_facts(self.path)


if __name__ == "__main__":
    unittest.main()
