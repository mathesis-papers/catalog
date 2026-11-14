#!/usr/bin/env python3
"""Inspect built PDF artifacts and render presentation contact sheets for review."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def inspect(root, output):
    import pikepdf
    from PIL import Image, ImageDraw

    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root):
        raise ValueError("write PDF evidence outside the paper repository")
    output.mkdir(parents=True, exist_ok=True)
    pdfs = sorted((root / "_site").rglob("*.pdf"))
    if len(pdfs) < 4:
        raise ValueError("missing bilingual audience and speaker-note presentations")
    results = []
    for path in pdfs:
        if path.is_symlink() or not path.resolve().is_relative_to(root / "_site"):
            raise ValueError("unsafe PDF artifact")
        with pikepdf.open(path) as pdf:
            if not pdf.pages:
                raise ValueError("empty PDF")
            if any(key in str(pdf.Root) for key in ("/JavaScript", "/Launch", "/RichMedia")):
                raise ValueError("unexpected active PDF content")
            pages = len(pdf.pages)
        text = subprocess.check_output(["pdftotext", str(path), "-"], text=True)
        fonts = subprocess.check_output(["pdffonts", str(path)], text=True)
        if "LastResort" in fonts or "$$" in text:
            raise ValueError("placeholder font or raw display delimiters in " + path.name)
        results.append({"path": path.relative_to(root).as_posix(), "pages": pages, "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if "slides" in path.parts and "notes" not in path.stem:
            prefix = output / path.stem
            subprocess.run(["pdftoppm", "-scale-to", "850", "-png", str(path), str(prefix)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            images = sorted(output.glob(path.stem + "-*.png"))
            for start in range(0, len(images), 6):
                sheet = Image.new("RGB", (1720, 2040), "#e5e7eb")
                draw = ImageDraw.Draw(sheet)
                for index, image_path in enumerate(images[start:start + 6]):
                    with Image.open(image_path) as frame:
                        frame = frame.convert("RGB")
                        frame.thumbnail((840, 630))
                        x, y = index % 2 * 860 + 10, index // 2 * 680 + 30
                        sheet.paste(frame, (x, y))
                        draw.text((x, y - 22), image_path.stem, fill="black")
                sheet.save(output / f"{path.stem}-contact-{start // 6 + 1}.png")
    (output / "pdfs.json").write_text(json.dumps({"schema": "mathesis.paper-pdf-check.v1", "artifacts": results,
            "visual_review": "contact sheets produced; human/model inspection still required"}, indent=2) + "\n")
    print(len(results), "PDF artifacts inspected; contact sheets ready")
    return results


if __name__ == "__main__":
    inspect(Path(sys.argv[1]), Path(sys.argv[2]))
