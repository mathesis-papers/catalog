#!/usr/bin/env python3
"""Plan local paper content work without calling models, executing uploads or publishing."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def materials_identity(material, inputs, work):
    if material.get("schema") != "mathesis.materials.v1" or material.get("work") != inputs["work"] or material.get("version") != inputs["version"]:
        raise ValueError("built materials belong to another paper version")
    prefix = "https://papers.trureturing.org/" + work["repository_name"] + "/"
    if material.get("site_url") != prefix or material.get("repository_url") != "https://github.com/mathesis-papers/" + work["repository_name"]:
        raise ValueError("built materials belong to another repository")
    return prefix


def collected_story(root, inputs, work):
    """Reuse the publisher's exact-version readings, bound to its ownership hashes."""
    marker = root / ".mathesis/collection.json"
    if marker.is_symlink():
        raise ValueError("collection marker is a symlink")
    collection = json.loads(marker.read_text())
    if collection.get("schema") != "mathesis.paper-collection.v1" or collection.get("work") != inputs["work"] or collection.get("version") != inputs["version"]:
        raise ValueError("collection belongs to another paper version")

    def owned(name):
        path = root / name
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
            raise ValueError("collection path leaves paper repository or is a symlink")
        if not path.is_file():
            raise ValueError("collection file is missing: " + name)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != collection.get("sha256", {}).get(name):
            raise ValueError("collection bytes differ or are unowned: " + name)
        return data

    owned("paper.json")
    material = json.loads(owned("site/mathesis-materials.json"))
    prefix = materials_identity(material, inputs, work)
    text = {}
    for lang in ("en", "zh"):
        url = material.get("reading", {}).get(lang)
        if not url:
            continue
        if url != prefix + lang + "/":
            raise ValueError("collection reading address differs")
        name = "site/" + lang + "/index.html"
        data = owned(name)
        if b"<html" not in data[:1024].lower():
            raise ValueError("collection reading has the wrong format")
        text[lang] = {name: hashlib.sha256(data).hexdigest()}
    return text


def plan(repository, site=None):
    root = repository.resolve()
    paper = json.loads((root / "paper.json").read_text())
    work = paper["work"]
    frozen = json.loads((root / ".mathesis/generated.json").read_text())
    # Never bless changed source bytes, including an edited paper identity.
    for name, expected in frozen["sha256"].items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("frozen path leaves paper repository")
        data = path.read_bytes()
        if name == "README.md":
            data = data.split(b"\n<!-- Mathesis generated repository guide; refresh with paper.py front. -->\n", 1)[0]
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("frozen paper bytes differ: " + name)
    inputs = {"work": work["work"], "version": work["current_version"], "record_sha256": frozen["sha256"]["paper.json"],
              "source_sha256": (paper.get("files", {}).get("source") or {}).get("sha256")}
    mapping_path = root / ".mathesis/book.json"
    mapping = json.loads(mapping_path.read_text()) if mapping_path.is_file() else None
    text = {}
    collection_reading = {}
    story_prepared = False
    if mapping:
        if mapping["work"] != inputs["work"] or mapping["version"] != inputs["version"]:
            raise ValueError("book belongs to another paper version")
        slug = mapping["book"]
        book = root / "books" / slug
        if not book.resolve().is_relative_to(root / "books"):
            raise ValueError("book leaves paper repository")
        for lang in ("en", "zh"):
            text[lang] = {p.relative_to(book).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted((book / lang).glob("*.md")) if p.is_file() and not p.is_symlink()}
        story_prepared = bool(mapping.get("stage") == "complete" and not mapping.get("pending_claims") and all(text.values()))
    elif (root / ".mathesis/collection.json").exists():
        text = collected_story(root, inputs, work)
        story_prepared = {"en", "zh"}.issubset(text)
        collection_reading = {lang: next(iter(paths.values())) for lang, paths in text.items()}
    material = {}
    if site:
        material = json.loads((site / "mathesis-materials.json").read_text())
        prefix = materials_identity(material, inputs, work)

        def actual(url, kind, expected=None):
            if not isinstance(url, str) or not url.startswith(prefix):
                raise ValueError("material leaves paper site")
            path = site / url[len(prefix):].split("#", 1)[0]
            if url.split("#", 1)[0].endswith("/"):
                path = path / "index.html"
            if not path.resolve().is_relative_to(site.resolve()) or not path.is_file():
                raise ValueError("material is not built")
            with path.open("rb") as stream:
                head = stream.read(1024)
            if (kind == "pdf" and not head.startswith(b"%PDF-")) or (kind == "video" and head[4:8] != b"ftyp") or (kind == "html" and b"<html" not in head.lower()):
                raise ValueError("material output has the wrong format")
            if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("built reading differs from prepared collection")
        for lang, url in material.get("reading", {}).items(): actual(url, "html", collection_reading.get(lang))
        for row in material.get("slides", []):
            for field in ("pdf_url", "notes_url"):
                if row.get(field): actual(row[field], "pdf")
        for row in material.get("videos", []):
            actual(row["url"], "html")
            actual(row["mp4_url"], "video")
        for row in material.get("visualizations", []): actual(row["url"], "html")
    media_inputs = dict(inputs, story_sha256=digest(text))
    units = []
    for name, ready, identity in (
        ("story", story_prepared and {"en", "zh"}.issubset(material.get("reading", {})), inputs),
        ("slides", bool(material.get("slides")), media_inputs),
        ("video", bool(material.get("videos")), media_inputs),
    ):
        units.append({"unit": name, "input_key": digest(dict(identity, unit=name)),
                      "state": "built" if ready else "needs_build_or_validation" if name == "story" and story_prepared else "needs_preparation",
                      "depends_on": [] if name == "story" else ["story"]})
    return {"schema": "mathesis.local-content-plan.v1", "paper": inputs, "units": units,
            "formalization_requests": material.get("formalization_requests", []),
            "limits": "Local content readiness only. No semantic proof check, live issue status, verification credit or publication. CMA runner not invoked."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--site", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.repository.resolve()):
        parser.error("write the run plan outside the paper repository")
    result = plan(args.repository, args.site)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(" · ".join(row["unit"] + ": " + row["state"] for row in result["units"]))


if __name__ == "__main__":
    main()
