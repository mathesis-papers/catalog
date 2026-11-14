"""Publish existing public papers without a model, book conversion or proof job."""
import hashlib
import html
import json
from pathlib import Path
import re
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
  # Reuse the inherited template's deployment group: its first build must
  # finish before this paper, so it cannot later replace the published paper.
  group: pages
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


def validate_aliases(mapping, aliases):
    if not isinstance(aliases, dict) or any(w not in mapping for w in aliases):
        raise ValueError("repository aliases must belong to mapped works")
    used = {r.lower() for r in mapping.values()}
    for work, names in aliases.items():
        if not isinstance(names, list) or not names:
            raise ValueError("repository aliases must be nonempty lists")
        for name in names:
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}", name) or name.lower() in used:
                raise ValueError("repository alias is unsafe or collides with another address")
            used.add(name.lower())


def relocate_materials(value, old, new):
    old_base = f"https://papers.trureturing.org/{old}/"
    new_base = f"https://papers.trureturing.org/{new}/"
    if value.get("repository_url") != f"https://github.com/mathesis-papers/{old}" or value.get("site_url") != old_base:
        raise ValueError("legacy materials repository differs")

    def relocate(item):
        if isinstance(item, str) and item.startswith(old_base):
            return new_base + item[len(old_base):]
        if isinstance(item, dict):
            return {k: relocate(v) for k, v in item.items()}
        if isinstance(item, list):
            return [relocate(v) for v in item]
        return item

    result = relocate(value)
    result["repository_url"] = f"https://github.com/mathesis-papers/{new}"
    return result


def relocate_book(files, old, new):
    """Keep the book's frozen identity and request registrations; move its site."""
    paper = json.loads(files["paper.json"])
    marker = json.loads(files[".mathesis/generated.json"])
    if marker["sha256"]["paper.json"] != hashlib.sha256(files["paper.json"]).hexdigest():
        raise ValueError("book paper metadata edited outside publisher")
    current = paper["work"]["repository_name"]
    if current not in (old, new):
        raise ValueError("book repository differs")
    config = files["site.toml"].decode()
    repo_line = f'repo = "mathesis-papers/{current}"'
    bases = [f'base_url = "https://papers.trureturing.org/{name}/"' for name in (old, new)]
    found = [base for base in bases if config.count(base) == 1]
    if config.count(repo_line) != 1 or len(found) != 1:
        raise ValueError("book address config differs")
    # Upstream book/request contracts use the original repository identity as
    # a stable registration key and book fixture slug. GitHub redirects that
    # identity to this same repository. Public navigation uses the new base.
    config = config.replace(repo_line, f'repo = "mathesis-papers/{old}"').replace(found[0], f'base_url = "https://papers.trureturing.org/{new}/"')
    paper["work"]["repository_name"] = old
    encode = lambda value: (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    encoded = encode(paper)
    marker["sha256"]["paper.json"] = hashlib.sha256(encoded).hexdigest()
    front_marker = b"\n<!-- Mathesis generated repository guide; refresh with paper.py front. -->\n"
    prefix, separator, guide = files["README.md"].partition(front_marker)
    if not separator or marker["sha256"]["README.md"] != hashlib.sha256(prefix).hexdigest():
        raise ValueError("book README catalog prefix differs")
    guide = guide.replace(f"https://papers.trureturing.org/{old}/".encode(),
                          f"https://papers.trureturing.org/{new}/".encode())
    return {"paper.json": encoded, ".mathesis/generated.json": encode(marker), "site.toml": config.encode(),
            "README.md": prefix + separator + guide}


def redirects(site, mapping, aliases, projections):
    destinations = {old: mapping[w] for w, names in aliases.items() for old in names}
    if not destinations:
        return
    table = json.dumps(destinations, sort_keys=True)
    script = '<script>const names=' + table + ';const parts=location.pathname.split("/");if(names[parts[1]]){parts[1]=names[parts[1]];location.replace(parts.join("/")+location.search+location.hash)}</script>'
    write(site / "404.html", '<!doctype html><html lang="en"><meta charset="utf-8"><title>Mathesis papers</title>' + script + '<p>This paper may have moved. <a href="/">Browse the papers</a>.</p></html>\n')
    for work, names in aliases.items():
        repo = mapping[work]
        for old in names:
            for suffix in ("", "en/", "zh/"):
                url = f"{SITE_BASE}/{repo}/" + suffix
                document = '<!doctype html><html lang="en"><meta charset="utf-8"><link rel="canonical" href="' + html.escape(url, quote=True) + '">' + script + '<title>Mathesis paper</title><p><a href="' + html.escape(url, quote=True) + '">Open the paper</a>.</p></html>\n'
                write(site / old / suffix / "index.html", document)
            pdf = projections / repo / "paper.pdf"
            (site / old).mkdir(parents=True, exist_ok=True)
            shutil.copy2(pdf, site / old / "paper.pdf")


SITE_BASE = "https://papers.trureturing.org"


def ownership(files, work, version):
    files = {p: b for p, b in files.items() if p != MARKER}
    hashes = {p: hashlib.sha256(b).hexdigest() for p, b in files.items()}
    blobs = {p: hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest() for p, b in files.items()}
    return (json.dumps({"schema": SCHEMA, "work": work, "version": version, "sha256": hashes, "git_blobs": blobs}, sort_keys=True, indent=2) + "\n").encode()


def prepare(projection, detail, destination, api_base):
    paper = read(projection / "paper.json")
    work = paper["work"]
    if work.get("publication") != "public" or work.get("license") != "CC-BY-4.0":
        raise ValueError("collection paper is outside public archive scope")
    identity = detail.get("id") or detail.get("record")
    version = detail.get("revision") or detail.get("version", 0)
    if identity != work["work"] or int(version) != work["current_version"]:
        raise ValueError("collection details do not match the frozen version")
    shutil.copytree(projection, destination)
    title = work["title"]
    authors = ", ".join(a["name"] for a in work["authors"])
    repo = work["repository_name"]
    site = f"https://papers.trureturing.org/{repo}/"
    repository = f"https://github.com/mathesis-papers/{repo}"
    record = api_base.rstrip("/") + "/papers/" + urllib.parse.quote(work["record"], safe="") + "?version=" + str(work["current_version"])
    source = repository + "/raw/refs/heads/main/source.archive" if (projection / "source.archive").is_file() else repository + "/tree/main/manuscript"
    links = [("PDF", site + "paper.pdf"), ("Source", source), ("Citation", repository + "/blob/main/CITATION.cff"),
             ("Repository", repository), ("Mathesis record", record)]
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
    citation = authors + ". " + title + ". Version " + str(work["current_version"]) + "."
    if work.get("doi"):
        citation += " https://doi.org/" + work["doi"]
    content += "<h2>Citation</h2><p>" + html.escape(citation) + "</p>"
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
    files = {p.relative_to(destination).as_posix(): p.read_bytes()
             for p in destination.rglob("*") if p.is_file()}
    (destination / MARKER).parent.mkdir(parents=True, exist_ok=True)
    (destination / MARKER).write_bytes(ownership(files, work["work"], work["current_version"]))


def legacy_book_projection(source, destination):
    """Adapt the checked projection to the pinned book template's older file layout."""
    shutil.copytree(source, destination)
    original = destination / "source.archive"
    target = destination / "manuscript/original-source.archive"
    if not original.is_file() or target.exists():
        raise ValueError("legacy book original-source path missing or occupied")
    original.rename(target)
    paper = read(destination / "paper.json")
    paper["files"]["source"].update(path="manuscript/", original_path="manuscript/original-source.archive")
    encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    write(destination / "paper.json", encode(paper))
    manifest = read(destination / ".mathesis/generated.json")
    hashes = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(destination.rglob("*")) if p.is_file() and p.relative_to(destination).as_posix() != ".mathesis/generated.json"}
    manifest.update(generated_paths=sorted(hashes), sha256=hashes,
                    snapshot_identity=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest())
    write(destination / ".mathesis/generated.json", encode(manifest))
    return destination
