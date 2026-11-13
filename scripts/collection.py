"""Publish existing public papers without a model, book conversion or proof job."""
import hashlib
import html
import json
from pathlib import Path
import shutil
import urllib.parse

SCHEMA = "mathesis.paper-collection.v1"
MARKER = ".mathesis/collection.json"
WORKFLOW = """name: Publish paper
on:
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: paper-pages
  cancel-in-progress: false
jobs:
  publish:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09
      - name: Stage the existing paper
        run: |
          mkdir -p _site
          cp -R site/. _site/
          cp paper.pdf _site/paper.pdf
          touch _site/.nojekyll
      - uses: actions/upload-pages-artifact@7b1f4a764d45c48632c6b24a0339c27f5614fb0b
        with:
          path: _site
      - uses: actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e
        id: deployment
"""


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def page(title, authors, content, language="en"):
    return """<!doctype html>
<html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s — Mathesis</title>
<style>html{color-scheme:light dark}body{max-width:68rem;margin:3rem auto;padding:0 2rem;font:18px/1.7 Georgia,"Noto Serif",serif}header,article{max-width:48rem}h1{font-size:2rem;line-height:1.3;font-weight:500}h2{font-size:1.3rem;margin-top:2rem}a{color:#357053}nav{display:flex;flex-wrap:wrap;gap:.6rem 1.5rem;margin:1.4rem 0}.meta{font:15px/1.6 system-ui,sans-serif}p{overflow-wrap:anywhere}footer{margin-top:3rem;font-size:.9rem}@media(max-width:600px){body{margin:1.5rem auto;padding:0 1.2rem}h1{font-size:1.6rem}}</style>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\\\(',right:'\\\\)',display:false},{left:'\\\\[',right:'\\\\]',display:true}],throwOnError:false})"></script>
</head><body><header><a href="https://papers.trureturing.org/">Mathesis papers</a><h1>%s</h1><p>%s</p></header>%s<footer><a href="https://mathesis-omega-research.apps.chrono-ai.fun/contribute">Contribute to the mathematics and its formalization</a></footer></body></html>
""" % (language, html.escape(title), html.escape(title), html.escape(authors), content)


def paragraphs(text):
    return "\n".join("<p>" + html.escape(p).replace("\n", " ") + "</p>"
                     for p in text.split("\n\n") if p.strip())


def prepare(projection, detail, destination, api_base):
    paper = read(projection / "paper.json")
    work = paper["work"]
    if work.get("publication") != "public" or work.get("license") != "CC-BY-4.0":
        raise ValueError("collection paper is outside public archive scope")
    if detail.get("id") != work["work"] or int(detail.get("revision", 0)) != work["current_version"]:
        raise ValueError("collection details do not match the frozen version")
    shutil.copytree(projection, destination)
    title = work["title"]
    authors = ", ".join(a["name"] for a in work["authors"])
    repo = work["repository_name"]
    site = f"https://papers.trureturing.org/{repo}/"
    repository = f"https://github.com/mathesis-papers/{repo}"
    record = api_base.rstrip("/") + "/papers/" + urllib.parse.quote(work["record"], safe="") + "?version=" + str(work["current_version"])
    links = [("PDF", site + "paper.pdf"), ("Source", repository + "/tree/main/manuscript"), ("Mathesis record", record)]
    if work.get("doi"):
        links.append(("DOI", "https://doi.org/" + work["doi"]))
    lean = detail.get("lean")
    if lean:
        links.append(("Lean source", lean["url"]))
    readings = {}
    for language, text in (detail.get("plain_language") or {}).items():
        if language not in ("en", "zh") or not isinstance(text, str) or not text.strip():
            continue
        label = "Read in English" if language == "en" else "中文解读"
        readings[language] = site + language + "/"
        links.append((label, readings[language]))
        content = '<nav><a href="../">' + ("Paper" if language == "en" else "论文") + '</a></nav><article>' + paragraphs(text) + "</article>"
        write(destination / "site" / language / "index.html", page(title, authors, content, language))
    nav = "<nav>" + "".join('<a href="' + html.escape(url, quote=True) + '">' + html.escape(label) + "</a>" for label, url in links) + "</nav>"
    content = '<p class="meta">Published paper · Version ' + str(work["current_version"]) + " · CC BY 4.0</p>" + nav
    content += "<article><h2>Abstract</h2>" + paragraphs(work.get("abstract_text") or detail.get("abstract") or "")
    content += "<h2>Citation</h2><p>" + html.escape(authors + ". " + title + ". Version " + str(work["current_version"]) + ".") + "</p>"
    if lean:
        content += '<h2>Formalization</h2><p>Published Lean source accompanies this paper. Exact statement coverage and independently checked contributions are recorded on its Mathesis page.</p>'
    content += "</article>"
    write(destination / "site/index.html", page(title, authors, content))
    manifest = {"schema": "mathesis.materials.v1", "work": work["work"], "version": work["current_version"], "repository_url": repository,
                "site_url": site, "doi": work.get("doi"), "reading": readings, "slides": [], "visualizations": []}
    write(destination / "site/mathesis-materials.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write(destination / ".github/workflows/pages.yml", WORKFLOW)
    # Versioned originals are retained on every subsequent update.
    for path in sorted(projection.rglob("*")):
        if path.is_file():
            target = destination / "versions" / ("v" + str(work["current_version"])) / path.relative_to(projection)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    files = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in destination.rglob("*") if p.is_file()}
    blobs = {}
    for name in files:
        data = (destination / name).read_bytes()
        blobs[name] = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    write(destination / MARKER, json.dumps({"schema": SCHEMA, "work": work["work"], "version": work["current_version"], "sha256": files, "git_blobs": blobs}, sort_keys=True, indent=2) + "\n")
