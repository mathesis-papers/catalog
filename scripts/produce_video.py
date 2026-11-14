"""Prepare only a missing film, retaining the checked story, slides and evidence."""
from pathlib import Path
import hashlib
import shutil
import subprocess

import serial_content as serial

SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "status": {"type": "string", "enum": ["prepared", "needs_attention"]},
    "summary": {"type": "string"},
    "outputs": {"type": "array", "items": {"type": "string"}},
    "remaining": {"type": "array", "items": {"type": "string"}},
}, "required": ["status", "summary", "outputs", "remaining"]}


def check_scope(before, after, prefix):
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    outside = sorted(name for name in changed if not name.startswith(prefix))
    if outside:
        raise ValueError("film worker changed checked non-film sources: " + ", ".join(outside))


def embed(repo, book, film_id):
    for lang, lead, caption in (
            ("en", "The short film below introduces the question and the idea behind the argument.",
             "A narrated introduction to the question and its mathematical mechanism."),
            ("zh", "下面的短片介绍问题的来由，以及论证背后的关键想法。",
             "通过旁白与图示，介绍问题和它背后的数学机制。")):
        page = repo / "books" / book / lang / "README.md"
        text = page.read_text()
        reference = f'::: {{.film src="{film_id}"}}'
        if reference not in text:
            page.write_text(text.rstrip() + f"\n\n{lead}\n\n{reference}\n{caption}\n:::\n")


def run(workspace, paper, template, toolchain=None, model=None):
    run = serial.previous_attempt(workspace, paper)
    if run is None or serial.read(run / "checkpoint.json")["state"] != "local_checks_passed":
        raise ValueError("finish and check this paper's existing story/slides attempt before adding a film")
    envelope = serial.read(run / "envelope.json")
    repo = Path(envelope["repository_path"])
    serial.validate_run(repo, run, build=False)
    plan = serial.read(run / "content-plan.json")
    if any(row["unit"] == "video" and row["state"] == "built" for row in plan["units"]):
        print(paper["id"], "reused checked narrated video", flush=True)
        return serial.read(run / "checkpoint.json")
    if shutil.disk_usage(workspace).free < 8 * 1024**3:
        raise RuntimeError("less than 8 GiB free; preserve outputs and clear disposable caches first")
    mapping = serial.read(repo / ".mathesis/book.json")
    book = mapping["book"]
    product = run / "video"
    if product.exists():
        raise RuntimeError("existing film attempt requires inspection, not another model call: " + str(product))
    serial.authored_workspace(workspace, paper, template, Path(envelope["source_repository_path"]), run, toolchain)
    before = serial.source_inventory(repo)
    product.mkdir()
    serial.write(product / "envelope.json", {"schema": "mathesis.paper-video-envelope.v1", "paper": paper,
        "base_run": str(run), "repository_path": str(repo), "source_sha256": serial.digest(before),
        "skill_sha256": hashlib.sha256(serial.SKILL.read_bytes()).hexdigest(),
        "publication_authorized": False, "request_registration_authorized": False, "concurrency": 1})
    serial.write(product / "base-checkpoint.json", serial.read(run / "checkpoint.json"))
    serial.write(product / "result-schema.json", SCHEMA)
    prompt = f"""Produce ONLY the missing narrated film for this ONE accepted paper.
Read {product / 'envelope.json'}, {serial.SKILL}, the installed
skills/layer-mathesis-paper-media/SKILL.md and skills/book-film/SKILL.md,
then the existing checked Chinese/English story under books/{book}/.
Reuse the story, mathematics, exact Lean scope and credit. Do not review the manuscript again,
rewrite the story or slides, repeat the declaration comparison, or create/edit requests.
Work only in books/{book}/films/. Use the installed film engine to author one focused 1–3 minute
film: film.json, English narration with aligned Chinese/English script.json, meaningful scenes.js,
and a real poster through the installed command. Do not make decorative interaction or a second
renderer. The host embeds the film in both reading editions and renders/checks the actual MP4.
Use the estimated timeline for your poster and sample shots. Do not generate TTS/audio or render
the full MP4: the host performs those once after source preparation. Retain authors' recorded
names; do not invent Chinese-character versions of names provided only in Latin script.
Do not install dependencies, run unrelated tests, spawn agents or parallel
jobs, change other files, push, deposit a DOI, deploy, send correspondence or assign points.
Use the existing shared toolchain. Save run evidence only at {product}, outside the paper tree.
Keep build/status/badge-policy bookkeeping out of the film; teach the question and mechanism,
credit the original problem/literature and retain the precise written/Lean evidence boundaries.
Return the supplied schema, with raw repository-relative film source FILE paths in outputs.
Prepared means authored sources ready for the host; an actual film is required before completion.
"""
    (product / "prompt.txt").write_text(prompt)
    serial.write(run / "checkpoint.json", {"state": "media_authoring", "product_run": str(product), "started_at": serial.now()})
    args = ["codex", "exec", "-C", str(repo), "-s", "danger-full-access", "-c", 'approval_policy="never"',
            "--json", "--color", "never", "--output-schema", str(product / "result-schema.json"),
            "-o", str(product / "result.json")]
    if model:
        args.extend(["--model", model])
    args.append("-")
    print(paper["id"], "authoring missing video only", str(product), flush=True)
    state = {"state": "needs_attention", "product_run": str(product)}
    try:
        with (product / "prompt.txt").open() as prompt, (product / "worker.jsonl").open("w") as log, (product / "worker.stderr").open("w") as error:
            subprocess.run(args, stdin=prompt, stdout=log, stderr=error, check=True)
        result = serial.read(product / "result.json")
        prefix = f"books/{book}/films/"
        check_scope(before, serial.source_inventory(repo), prefix)
        if result["status"] != "prepared":
            raise ValueError("film sources need attention; inspect " + str(product))
        serial.check_result(dict(serial.read(run / "result.json"), outputs=result["outputs"]), repo)
        if not result["outputs"] or any(not name.startswith(prefix) for name in result["outputs"]):
            raise ValueError("film outputs must be actual source files under this paper's film directory")
        films = list((repo / "books" / book / "films").glob("*/film.json"))
        if len(films) != 1:
            raise ValueError("prepare one focused film, not an empty or duplicate registry")
        embed(repo, book, films[0].parent.name)
        state.update(serial.validate_run(repo, run, build=True))
        if not any(row["unit"] == "video" and row["state"] == "built" for row in serial.read(run / "content-plan.json")["units"]):
            raise ValueError("film sources have not produced a checked video")
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        state.update(state="needs_attention", error=str(exc), finished_at=serial.now())
    serial.write(run / "checkpoint.json", state)
    serial.write(product / "checkpoint.json", state)
    serial.preview_index(workspace, serial.read(workspace / "cohort.json"))
    print(paper["id"], state["state"], flush=True)
    return state
