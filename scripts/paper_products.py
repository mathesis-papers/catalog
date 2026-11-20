#!/usr/bin/env python3
"""Inspect built PDF artifacts and render presentation contact sheets for review."""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys


def clear_presentation_previews(output, stem):
    """Drop only this PDF's reproducible page and contact-sheet images."""
    pattern = re.compile(re.escape(stem) + r"-(?:\d+|contact-\d+)\.png")
    for path in output.iterdir():
        if path.is_file() and pattern.fullmatch(path.name):
            path.unlink()


def video_facts(path):
    """Probe and decode the actual film, including its narration stream."""
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], text=True))
    duration = float(probe.get("format", {}).get("duration", "nan"))
    video = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    audio = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not math.isfinite(duration) or duration < 1 or len(video) != 1 or video[0].get("width", 0) < 640 or video[0].get("height", 0) < 360:
        raise ValueError("missing or invalid film video stream: " + str(path))
    if not audio or not all(int(s.get("sample_rate", 0)) > 0 and s.get("channels", 0) > 0 for s in audio):
        raise ValueError("film lacks its narration audio stream: " + str(path))
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-threads", "1", "-i", str(path),
                    "-f", "null", "-"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return {"duration_seconds": duration, "width": video[0]["width"], "height": video[0]["height"],
            "video_codec": video[0]["codec_name"], "audio_codec": audio[0]["codec_name"]}


def inspect_videos(root, output):
    from PIL import Image, ImageDraw

    site = root / "_site"
    material = json.loads((site / "mathesis-materials.json").read_text())
    prefix = material["site_url"]
    rows = []
    for number, entry in enumerate(material.get("videos", []), 1):
        url = entry["mp4_url"]
        if not url.startswith(prefix):
            raise ValueError("film URL leaves the selected paper")
        path = site / url[len(prefix):]
        if path.is_symlink() or not path.resolve().is_relative_to(site) or not path.is_file():
            raise ValueError("missing or unsafe film output")
        facts = video_facts(path)
        sheet = Image.new("RGB", (1300, 820), "#e5e7eb")
        draw = ImageDraw.Draw(sheet)
        shots = []
        for i, fraction in enumerate((0.12, 0.36, 0.60, 0.84)):
            seconds = facts["duration_seconds"] * fraction
            frame = output / f"film-{number}-{i + 1}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-ss", str(seconds),
                            "-i", str(path), "-frames:v", "1", "-y", str(frame)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            with Image.open(frame) as image:
                image = image.convert("RGB")
                image.thumbnail((640, 360))
                x, y = i % 2 * 650 + 5, i // 2 * 410 + 30
                sheet.paste(image, (x, y))
                draw.text((x, y - 22), f"{seconds:.1f} s", fill="black")
            shots.append({"seconds": seconds, "file": frame.name})
        contact = output / f"film-{number}-contact.png"
        sheet.save(contact)
        rows.append({"path": path.relative_to(root).as_posix(), **facts, "bytes": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "shots": shots, "contact": contact.name})
    (output / "videos.json").write_text(json.dumps({"schema": "mathesis.paper-video-check.v1", "artifacts": rows,
            "visual_review": "decoded actual audiovisual files; contact sheets require inspection"}, indent=2) + "\n")
    return rows


def inspect(root, output):
    import pikepdf
    from PIL import Image, ImageDraw

    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root):
        raise ValueError("write PDF evidence outside the paper repository")
    output.mkdir(parents=True, exist_ok=True)
    pdfs = sorted((root / "_site").rglob("*.pdf"))
    if not pdfs:
        raise ValueError("missing reading and presentation PDF artifacts")
    from serial_content import check_material_products
    material = json.loads((root / '_site/mathesis-materials.json').read_text())
    check_material_products(material, {'decision':'built' if material.get('visualizations') else 'not_useful'})
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
            clear_presentation_previews(output, path.stem)
            subprocess.run(["pdftoppm", "-scale-to", "850", "-png", str(path), str(prefix)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            images = sorted(p for p in output.glob(path.stem + "-*.png")
                            if re.fullmatch(re.escape(path.stem) + r"-\d+\.png", p.name))
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
    videos = inspect_videos(root, output)
    print(len(videos), "narrated videos decoded; contact sheets ready")
    return results


if __name__ == "__main__":
    inspect(Path(sys.argv[1]), Path(sys.argv[2]))
