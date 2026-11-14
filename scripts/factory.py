"""A public inventory of checked paper projections; never a proof or credit authority."""
import json


def render(output, planned, details, book_works):
    load = lambda path: json.loads(path.read_text())
    catalog = output / "catalog"
    destination = output.parent / "factory"
    destination.mkdir(parents=True, exist_ok=True)
    works = load(catalog / "works.json")["works"]
    snapshot = load(catalog / "snapshot.json")
    formal = {p["work"]: p for path in (output / "formalization/index/papers").glob("*.json") for p in [load(path)]}
    rows = []
    for work in works:
        identity = work["work"]
        if work["publication"] != "public" or identity not in planned:
            continue
        repo = planned[identity]
        paper = load(output / "papers" / repo / "paper.json")
        detail = details.get(identity, {})
        reading = detail.get("plain_language") or {}
        material = load(catalog / "materials" / identity / "materials.json")
        languages = sorted({k for k, v in reading.items() if k in ("en", "zh") and isinstance(v, str) and v.strip()} | set(material["reading"]))
        counts = {key: len(value) for key, value in formal.get(identity, {}).get("split", {}).items()}
        media = {key: len(material.get(key, [])) for key in ("slides", "visualizations", "videos", "formalization_requests")}
        next_steps = []
        if not work.get("doi"):
            next_steps.append("attach_verified_doi")
        if not {"en", "zh"}.issubset(languages):
            next_steps.append("prepare_bilingual_reading")
        for key, step in (("slides", "prepare_slides"), ("videos", "prepare_video")):
            if not media[key]:
                next_steps.append(step)
        if counts.get("no_target_yet", 0):
            next_steps.append("prepare_exact_targets")
        if counts.get("open_target", 0):
            next_steps.append("submit_proofs_for_open_targets")
        rows.append({"work": identity, "kind": "record" if "record" in detail and "id" not in detail else "collected",
                     "version": work["current_version"], "title": work["title"],
                     "repository_url": "https://github.com/mathesis-papers/" + repo,
                     "site_url": "https://papers.trureturing.org/" + repo + "/",
                     "mode": "authored_book" if identity in book_works else "paper",
                     "doi": work.get("doi"), "concept_doi": work.get("concept_doi"),
                     "files": paper["files"], "reading_languages": languages,
                     "materials": media,
                     "lean_source": work.get("lean_source"), "formalization": counts,
                     "trureturing_write_back": formal.get(identity, {}).get("trureturing_write_back"),
                     "next_steps": next_steps})
    rows.sort(key=lambda r: r["work"])
    value = {"schema": "mathesis.paper-factory.v1", "snapshot_time": snapshot["time"],
             "scope": "Checked current public archive projections; Pages deployment is checked separately. No new review or proof credit.",
             "papers": len(rows), "entries": rows}
    (destination / "factory.json").write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    lines = ["# Paper factory", "", value["scope"], "", "Snapshot: `" + snapshot["time"] + "`.", "",
             "| Paper | Version | DOI | Reading | Slides / videos / visuals | Formalization |", "|---|---:|---|---|---|---|"]
    for row in rows:
        progress = ", ".join(str(n) + " " + key.replace("_", " ") for key, n in row["formalization"].items() if n) or "No indexed statements"
        title = row["title"].replace("|", "&#124;").replace("\n", " ")
        doi = "[DOI](https://doi.org/" + row["doi"] + ")" if row["doi"] else "Awaiting DOI"
        media = row["materials"]
        lines.append(f'| [{title}]({row["repository_url"]}) | {row["version"]} | {doi} | {", ".join(row["reading_languages"]) or "Awaiting reading"} | {media["slides"]} / {media["videos"]} / {media["visualizations"]} | {progress} |')
    lines += ["", "Publication proceeds independently of exposition and proof work. A pinned Lean source is not statement-level verification. Exact targets, verified bundles and original attribution remain in [formalization](https://github.com/mathesis-papers/formalization). Public Mathesis receipts and leaderboards are authoritative.", ""]
    (destination / "FACTORY.md").write_text("\n".join(lines))
    return value
