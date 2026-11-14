"""Repeated PDF inspection must not feed old contact sheets back into itself."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("paper_products", Path(__file__).parents[1] / "scripts/paper_products.py")
products = importlib.util.module_from_spec(spec)
spec.loader.exec_module(products)


class PdfPreviews(unittest.TestCase):
    def test_rebuild_discards_stale_pages_and_contacts_only_for_this_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            stale = ["seminar.en-01.png", "seminar.en-20.png", "seminar.en-contact-1.png"]
            preserved = ["seminarXen-01.png", "seminar.en-notes-01.png", "film-1-contact.png", "seminar.en-source.png"]
            for name in stale + preserved:
                (output / name).write_bytes(b"preview")
            products.clear_presentation_previews(output, "seminar.en")
            self.assertEqual(sorted(p.name for p in output.iterdir()), sorted(preserved))
            products.clear_presentation_previews(output, "seminar.en")
            self.assertEqual(sorted(p.name for p in output.iterdir()), sorted(preserved))


if __name__ == "__main__":
    unittest.main()
