#!/usr/bin/env python3
"""Run accepted-paper preparation serially; no scheduler or publication side effects.

Inputs and checkpoints live outside paper repositories. The same envelope and
result schema can be handed to CMA; the bundled adapter runs local Codex CLI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collection
import plan_content

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/mathesis-paper-production/SKILL.md"
RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["prepared", "needs_attention"]},
        "summary": {"type": "string"},
        "visualization": {"type": "object", "additionalProperties": False,
            "properties": {"decision": {"type": "string", "enum": ["built", "not_useful", "pending"]},
                           "reason": {"type": "string"}}, "required": ["decision", "reason"]},
        "outputs": {"type": "array", "items": {"type": "string", "pattern": "^[A-Za-z0-9._/-]+$"},
                    "description": "Raw repository-relative FILE paths only. No prose, Markdown, URLs or directories."},
        "checks": {"type": "array", "items": {"type": "string"}},
        "requests": {"type": "array", "items": {"type": "string", "pattern": "^https://github.com/the-omega-institute/trureturing-requests/issues/[1-9][0-9]*$"},
                     "description": "Canonical issue URLs only; explanations belong in summary/checks."},
        "doi_observation": {"type": "string"},
        "remaining": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "visualization", "outputs", "checks", "requests", "doi_observation", "remaining"],
}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def command(args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def validate_cohort(cohort):
    if cohort.get("schema") != "mathesis.serial-cohort.v1":
        raise ValueError("unsupported cohort schema")
    seen = set()
    for paper in cohort["papers"]:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", paper["id"]) or paper["id"] in seen:
            raise ValueError("invalid or duplicate work id")
        if not re.fullmatch(r"mathesis-papers/[a-zA-Z0-9._-]+", paper["repository"]):
            raise ValueError("unexpected paper repository")
        if not str(paper["version"]).isdigit() or int(paper["version"]) < 1:
            raise ValueError("invalid paper version")
        seen.add(paper["id"])


@contextmanager
def exclusive(workspace):
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "serial.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another serial worker owns this workspace") from exc
        yield


def authored_workspace(workspace, paper, template, source, run, toolchain=None):
    """Use the publisher's existing projection/fill adapter; keep collection intact.

    This is a local preview, not an in-place migration of a published collection.
    Old version URLs and the original repository remain untouched.
    """
    dest = source if (source / ".mathesis/book.json").is_file() else workspace / "authored" / paper["id"]
    if dest.exists() and not (dest / ".mathesis/book.json").is_file():
        raise ValueError("incomplete authored staging needs inspection: " + str(dest))
    if not dest.exists():
        projection = run / "catalog-projection"
        frozen = read(source / ".mathesis/generated.json")
        for name in [*frozen["generated_paths"], ".mathesis/generated.json"]:
            path = projection / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, path)
        legacy = collection.legacy_book_projection(projection, run / "book-input")
        dest.mkdir(parents=True)
        archive = subprocess.check_output(["git", "archive", "HEAD"], cwd=template)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(dest, filter="data")
        subprocess.run([sys.executable, str(dest / "docs/layer-mathesis/scripts/paper.py"), "--root", str(dest), "fill",
                        "--input", str(legacy), "--slug", paper["id"], "--title-zh", paper["title"],
                        "--title-en", paper["title"], "--repository", paper["repository"]], check=True)
        write(dest / "books" / paper["id"] / "viz/visualizations.json", {"version": 1, "visualizations": []})
        subprocess.run(["git", "init", "-b", "content-preview", str(dest)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/" + paper["repository"] + ".git"], cwd=dest, check=True)
        subprocess.run(["git", "add", "."], cwd=dest, check=True)
        subprocess.run(["git", "commit", "-m", "Stage frozen paper with published template for local content preview"], cwd=dest, check=True, stdout=subprocess.DEVNULL)
    if toolchain:
        for name in ("node_modules", ".venv"):
            target = toolchain / name
            if not target.is_dir():
                raise ValueError("shared toolchain directory missing: " + str(target))
            link = dest / name
            if not link.exists():
                link.symlink_to(target, target_is_directory=True)
        cache = dest / ".cache"
        cache.mkdir(exist_ok=True)
        if (toolchain / ".cache/tectonic").is_dir() and not (cache / "tectonic").exists():
            (cache / "tectonic").symlink_to(toolchain / ".cache/tectonic", target_is_directory=True)
        if (toolchain / ".cache/film").is_dir() and not (cache / "film").exists():
            (cache / "film").symlink_to(toolchain / ".cache/film", target_is_directory=True)
    return dest


def prepare(workspace, paper, template, allow_requests=False, toolchain=None):
    if shutil.disk_usage(workspace).free < 8 * 1024**3:
        raise RuntimeError("less than 8 GiB free; preserve outputs and clear disposable caches first")
    repo = workspace / "papers" / paper["id"]
    if not repo.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "https://github.com/" + paper["repository"] + ".git", str(repo)], check=True)
    remote = command(["git", "remote", "get-url", "origin"], repo)
    if remote.removesuffix(".git") != "https://github.com/" + paper["repository"]:
        raise ValueError("paper checkout has an unexpected origin")
    record = read(repo / "paper.json")
    if record["work"]["work"] != paper["id"] or str(record["work"]["current_version"]) != str(paper["version"]):
        raise ValueError("frozen cohort differs from paper checkout; never silently switch versions")
    frozen = read(repo / ".mathesis/generated.json")
    for name, expected in frozen["sha256"].items():
        path = repo / name
        if path.is_symlink() or not path.resolve().is_relative_to(repo.resolve()):
            raise ValueError("unsafe frozen source path")
        data = path.read_bytes()
        if name == "README.md":
            data = data.split(b"\n<!-- Mathesis generated repository guide; refresh with paper.py front. -->\n", 1)[0]
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("changed frozen source: " + name)
    template_commit = command(["git", "rev-parse", "HEAD"], template)
    identity = {"paper": paper, "source_hashes": frozen["sha256"], "template_commit": template_commit,
                "skill_sha256": hashlib.sha256(SKILL.read_bytes()).hexdigest(), "allow_request_registration": allow_requests}
    run = workspace / "runs" / paper["id"] / digest(identity)
    run.mkdir(parents=True, exist_ok=True)
    source = repo
    repo = authored_workspace(workspace, paper, template, source, run, toolchain)
    envelope = {"schema": "mathesis.paper-production-envelope.v1", "identity": identity,
                "repository_path": str(repo), "source_repository_path": str(source), "template_path": str(template), "skill_path": str(SKILL),
                "run_path": str(run), "concurrency": 1,
                "publication_authorized": False, "do_not_rereview": True,
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(run / "envelope.json", envelope)
    write(run / "result-schema.json", RESULT_SCHEMA)
    prompt = f"""Prepare this ONE accepted public Mathesis paper. Do not spawn agents, jobs, or parallel tasks.
Read {run / 'envelope.json'} and {SKILL}, then apply the installed paper-template skills.
This is content production, not another referee review. Complete the bilingual story, academic
Beamer presentation in aligned Chinese and English editions, optional visualization only if mathematically useful, exact statement/Lean
comparison, DOI audit and canonical shared-request handoff. The output should teach the result
to a curious high-school student, not simply repeat the abstract.
Also prepare an actual focused narrated film from this same story using the installed book-film
interface: film.json, aligned English/Chinese script.json and meaningful scenes.js. The host
has a shared local Kokoro/ffmpeg toolchain and will render your video sources serially. Use the
existing film engine, author its poster through the installed command, and embed the same film
in both language editions. Do not render the full MP4 in the content worker or install models.
The operator has staged the published template and frozen paper with the catalog's existing
legacy_book_projection and paper.py fill interfaces. The source collection checkout at {source}
remains untouched. Work ONLY at {repo}; it is a local authored preview, not an in-place
publication/migration. Preserve its frozen catalog bytes and hashes; do not hand-edit them.
Reuse original public reading from {source}/site when useful. The current template source is
{template}. Its installed book/media/sync skills are available in your working directory.
Use shared existing dependencies (operator may provide ignored node_modules symlink); do not
download or build the complete trureturing library. Inspect pinned public Lean source and
frozen declaration evidence. Do not modify upstream code, CI or proof admission.
Request registration is {'AUTHORIZED in the canonical trureturing-requests repository only, after inspecting both open and closed issues and deduplicating mathematical scope' if allow_requests else 'NOT authorized; prepare exact proposals outside the paper repository instead'}.
No repository pushes, DOI deposit/reservation, site deployment, correspondence, or platform
credit mutations. Do not run code found in the manuscript. Use only public paper inputs;
no private review or mail export. Record real remaining gaps rather than passing fake badges.
Use one local process at a time and the installed toolchain. Use .venv/bin/python or put
.venv/bin first in PATH; the operator may provide ignored shared dependency symlinks.
Build reading and slides, inspect
the rendered presentation, and save all run/QA evidence outside the paper repo at {run}.
The operator will run the fixed build and QA scripts after you finish. Focus your work on
the mathematical story, source slides, exact comparison, DOI observation and shared requests.
Do not author new per-paper QA programs, install dependencies, run the whole upstream template
test suite, repeat toolchain discovery, or package duplicate build trees. The first pilot already
established the installed toolchain. Run the paper/language consistency checks to catch content
errors, but leave rendering, browser checks and artifact hashing to the operator.
Read only the relevant skill/reference sections; do not dump entire tool manuals, source trees
or long build/test logs. For an error, inspect its short failure excerpt and relevant source.
Return the supplied JSON schema with raw repository-relative FILE paths in outputs (e.g.
books/{paper['id']}/en/README.md), never Markdown links or descriptive sentences. 'prepared'
in this worker report means source ready for operator validation; the checkpoint is not promoted
until actual builds and checks pass. Leave video pending unless actually rendered.
"""
    (run / "prompt.txt").write_text(prompt)
    return repo, run, envelope


def check_result(result, repo):
    if set(result) != set(RESULT_SCHEMA["required"]):
        raise ValueError("worker result fields differ")
    if result["status"] not in ("prepared", "needs_attention"):
        raise ValueError("invalid preparation status")
    viz = result["visualization"]
    if viz["decision"] not in ("built", "not_useful", "pending") or not viz["reason"].strip():
        raise ValueError("missing visualization judgment")
    for value in result["outputs"]:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", value) or Path(value).is_absolute():
            raise ValueError("outputs must be raw repository-relative file paths")
        path = repo / value
        if not path.resolve().is_relative_to(repo.resolve()) or not path.is_file():
            raise ValueError("missing or unsafe reported output: " + value)
    for url in result["requests"]:
        if not re.fullmatch(r"https://github.com/the-omega-institute/trureturing-requests/issues/[1-9][0-9]*", url):
            raise ValueError("noncanonical formalization request")
    if result["status"] == "prepared" and (not result["outputs"] or viz["decision"] == "pending"):
        raise ValueError("prepared result still lacks required local products")


def check_material_products(materials, visualization):
    if not {"en", "zh"}.issubset(materials.get("reading", {})):
        raise ValueError("missing bilingual reading")
    # A PDF count alone cannot establish that both language editions exist.
    slide_languages = {s.get("language") for s in materials.get("slides", [])
                       if s.get("pdf_url") and s.get("notes_url")}
    if not {"en", "zh"}.issubset(slide_languages):
        raise ValueError("missing bilingual audience slides or speaker notes")
    actual_viz = bool(materials.get("visualizations"))
    if visualization["decision"] == "built" and not actual_viz:
        raise ValueError("visualization was reported built but is absent from the material manifest")
    if visualization["decision"] == "not_useful" and actual_viz:
        raise ValueError("visualization judgment and advertised outputs disagree")


def source_inventory(repo):
    """Bind checked products to content, executable bits and managed symlinks."""
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=repo)
    result = {}
    for name in sorted(set(names.decode().split("\0")) - {""}):
        path = repo / name
        if not path.exists() and not path.is_symlink():
            continue  # A removed source stays absent after it is committed.
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        result[name] = {"sha256": hashlib.sha256(data).hexdigest(),
                        "mode": "120000" if path.is_symlink() else "100755" if path.stat().st_mode & 0o111 else "100644"}
    return result


def site_inventory(repo):
    return {str(p.relative_to(repo / "_site")): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((repo / "_site").rglob("*")) if p.is_file() and not p.is_symlink()}


def previous_attempt(workspace, paper):
    """A template/skill update must never silently start a second content job."""
    attempts = sorted((workspace / "runs" / paper["id"]).glob("*/checkpoint.json"),
                      key=lambda p: p.stat().st_mtime)
    if not attempts:
        return None
    if len(attempts) != 1:
        raise RuntimeError("multiple saved attempts require inspection before continuing: " +
                           ", ".join(str(p.parent) for p in attempts))
    run = attempts[-1].parent
    envelope = read(run / "envelope.json")
    if envelope["identity"]["paper"] != paper:
        raise ValueError("existing attempt differs from the frozen cohort; inspect " + str(run))
    return run


def validate_run(repo, run, build=False):
    result = read(run / "result.json")
    check_result(result, repo)
    if result["status"] != "prepared":
        return {"state": "needs_attention", "remaining": result["remaining"]}
    if not build:
        inventory = run / "source-sha256.json"
        if not inventory.is_file() or read(inventory) != source_inventory(repo):
            raise ValueError("sources changed or lack a build binding; rebuild the existing products without another model call")
        rendered = run / "site-sha256.json"
        if not rendered.is_file() or read(rendered) != site_inventory(repo):
            raise ValueError("rendered output changed or lacks a build binding; rebuild the existing products without another model call")
    env = dict(os.environ, PATH=str(repo / ".venv/bin") + os.pathsep + os.environ.get("PATH", ""))
    mapping = read(repo / ".mathesis/book.json")
    checks = [[sys.executable, "docs/layer-mathesis/scripts/paper.py", "check", "--complete"], ["./tbook", "check"]]
    if build:
        films = list((repo / "books" / mapping["book"] / "films").glob("*/film.json"))
        if films:
            env["TBOOK_FILMS"] = "require"
            env["FILM_WORKERS"] = "1"
            env["OMP_NUM_THREADS"] = "1"
            env["OPENBLAS_NUM_THREADS"] = "1"
            env["VECLIB_MAXIMUM_THREADS"] = "1"
            checks.append(["./tbook", "build", "films", "--book", mapping["book"]])
        for target in ("html", "pdf", "slides"):
            checks.append(["./tbook", "build", target, "--book", mapping["book"]])
        if result["visualization"]["decision"] == "built":
            checks.append(["./tbook", "build", "viz", "--book", mapping["book"]])
        checks.append(["./tbook", "build", "site", "--book", mapping["book"]])
    with (run / "operator-checks.log").open("w") as log:
        for args in checks:
            subprocess.run(args, cwd=repo, stdout=log, stderr=subprocess.STDOUT, check=True, env=env)
        if build:
            subprocess.run(["node", str(ROOT / "scripts/paper_site_qa.cjs"), str(repo), str(run / "qa")],
                           cwd=repo, stdout=log, stderr=subprocess.STDOUT, check=True, env=env)
            python = repo / ".venv/bin/python"
            subprocess.run([str(python) if python.exists() else sys.executable, str(ROOT / "scripts/paper_products.py"), str(repo), str(run / "qa")],
                           cwd=repo, stdout=log, stderr=subprocess.STDOUT, check=True, env=env)
    plan = plan_content.plan(repo, repo / "_site")
    write(run / "content-plan.json", plan)
    states = {u["unit"]: u["state"] for u in plan["units"]}
    if states.get("story") != "built" or states.get("slides") != "built":
        raise ValueError("bilingual story and actual presentation are not built")
    if list((repo / "books" / mapping["book"] / "films").glob("*/film.json")) and states.get("video") != "built":
        raise ValueError("authored film has no actual built video")
    materials = read(repo / "_site/mathesis-materials.json")
    check_material_products(materials, result["visualization"])
    # Keep content/receipt/publication observations separate. A closed issue is
    # never converted into proof coverage by this local content runner.
    write(run / "site-sha256.json", site_inventory(repo))
    sources = source_inventory(repo)
    write(run / "source-sha256.json", sources)
    return {"state": "local_checks_passed", "finished_at": now(), "publication": "not_performed",
            "proof_certification": "not_performed", "visualization": result["visualization"], "source_sha256": digest(sources)}


def preview_index(workspace, cohort):
    preview = workspace / "preview"
    preview.mkdir(exist_ok=True)
    rows, ready, videos_ready = [], 0, 0
    for paper in cohort["papers"]:
        checkpoints = sorted((workspace / "runs" / paper["id"]).glob("*/checkpoint.json"), key=lambda p: p.stat().st_mtime)
        checked = [p for p in checkpoints if read(p).get("state") == "local_checks_passed"]
        links = []
        if checked:
            run = checked[-1].parent
            repo = Path(read(run / "envelope.json")["repository_path"])
            material = read(repo / "_site/mathesis-materials.json")
            name = paper["repository"].split("/", 1)[1]
            destination = preview / name
            if not destination.exists():
                destination.symlink_to(repo / "_site", target_is_directory=True)
            prefix = material["site_url"]
            for lang, url in material["reading"].items():
                links.append(f'<a href="/{html.escape(name)}/{html.escape(url.removeprefix(prefix))}">{"中文故事" if lang == "zh" else "English story"}</a>')
            for slide in material.get("slides", []):
                url = slide.get("pdf_url")
                if url:
                    links.append(f'<a href="/{html.escape(name)}/{html.escape(url.removeprefix(prefix))}">Slides ({html.escape(slide.get("language", slide.get("lang", "")))})</a>')
            for video in material.get("videos", []):
                links.append(f'<a href="/{html.escape(name)}/{html.escape(video["url"].removeprefix(prefix))}">Narrated video</a>')
            if material.get("videos"):
                videos_ready += 1
            ready += 1
        status = ("Story and slides checked · " + ("video checked" if material.get("videos") else "video pending")) if checked else read(checkpoints[-1]).get("state", "Pending") if checkpoints else "Pending"
        rows.append(f'<li><h2>{html.escape(paper["title"])}</h2><p>Version {html.escape(str(paper["version"]))} · {html.escape(status)}</p><nav>{" · ".join(links)}</nav></li>')
    (preview / "index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mathesis local paper previews</title><style>body{max-width:70rem;margin:3rem auto;padding:0 2rem;font:18px/1.65 Georgia,serif;color:#172622;background:#fafaf7}h1{font-weight:500}h2{font-size:1.15rem;font-weight:500;margin:0}li{margin:2rem 0}ul{list-style:none;padding:0}p{margin:.3rem 0}a{color:#225f49}nav{display:flex;gap:1rem;flex-wrap:wrap}</style><h1>Mathesis local paper previews</h1><p>' + str(ready) + ' of ' + str(len(cohort["papers"])) + ' papers have locally checked stories and slides; ' + str(videos_ready) + ' have a checked narrated video. Interaction is optional. These previews are not production publication or independent proof receipts.</p><ul>' + ''.join(rows) + '</ul></html>')
    return preview


def run_paper(workspace, paper, template, allow_requests=False, model=None, toolchain=None):
    previous = previous_attempt(workspace, paper)
    if previous is not None:
        state = read(previous / "checkpoint.json")
        if state["state"] != "local_checks_passed":
            raise RuntimeError("existing attempt requires inspection; use its recorded run path rather than blindly restarting: " + str(previous))
        envelope = read(previous / "envelope.json")
        state = validate_run(Path(envelope["repository_path"]), previous, build=False)
        print(paper["id"], "reused checked sources and outputs", str(previous), flush=True)
        return state
    repo, run, envelope = prepare(workspace, paper, template, allow_requests, toolchain)
    checkpoint = run / "checkpoint.json"
    if checkpoint.exists() and read(checkpoint)["state"] == "local_checks_passed":
        print(paper["id"], "already checked (not publication)", flush=True)
        return read(checkpoint)
    if checkpoint.exists():
        raise RuntimeError("existing attempt requires inspection; use its recorded run path rather than blindly restarting: " + str(run))
    write(checkpoint, {"state": "authoring", "started_at": now(), "envelope": str(run / "envelope.json")})
    args = ["codex", "exec", "-C", str(repo), "-s", "danger-full-access", "-c", 'approval_policy="never"',
            "--json", "--color", "never", "--output-schema", str(run / "result-schema.json"),
            "-o", str(run / "result.json")]
    if model:
        args.extend(["--model", model])
    args.append("-")
    print(paper["id"], "authoring", str(run), flush=True)
    with (run / "prompt.txt").open() as prompt, (run / "worker.jsonl").open("w") as log, (run / "worker.stderr").open("w") as err:
        code = subprocess.run(args, stdin=prompt, stdout=log, stderr=err).returncode
    state = {"state": "needs_attention", "worker_exit": code, "finished_at": now()}
    try:
        if code:
            raise RuntimeError("Codex worker failed; inspect the existing transcript")
        state.update(validate_run(repo, run, build=True))
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        state["error"] = str(exc)
    write(checkpoint, state)
    preview_index(workspace, read(workspace / "cohort.json"))
    print(paper["id"], state["state"], flush=True)
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "run", "status", "check", "preview"])
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--template", type=Path)
    p.add_argument("--toolchain", type=Path, help="existing checkout providing shared ignored .venv/node_modules")
    p.add_argument("--paper", help="frozen work id; omitted runs the cohort in order")
    p.add_argument("--allow-request-registration", action="store_true")
    p.add_argument("--model", help="explicit operator-selected model; omitted uses local Codex default")
    p.add_argument("--run", type=Path, help="existing run to check without another model call")
    p.add_argument("--rebuild", action="store_true", help="with check, rebuild existing sources and rerun fixed QA without a model")
    a = p.parse_args()
    workspace = a.workspace.resolve()
    cohort = read(workspace / "cohort.json")
    validate_cohort(cohort)
    if a.action == "preview":
        print(preview_index(workspace, cohort))
        return
    if a.action == "check":
        if a.run is None:
            p.error("check requires --run")
        with exclusive(workspace):
            envelope = read(a.run / "envelope.json")
            state = validate_run(Path(envelope["repository_path"]), a.run, build=a.rebuild)
            write(a.run / "checkpoint.json", state)
            print(state["state"])
        return
    papers = [x for x in cohort["papers"] if a.paper is None or x["id"] == a.paper]
    if not papers:
        p.error("paper is absent from the frozen cohort")
    if a.action == "status":
        for paper in papers:
            checkpoints = sorted((workspace / "runs" / paper["id"]).glob("*/checkpoint.json"))
            print(paper["id"], [(str(x.parent.name[:12]), read(x)["state"]) for x in checkpoints] or "pending")
        return
    if a.template is None:
        p.error("--template is required for prepare/run")
    with exclusive(workspace):
        for paper in papers:
            if a.action == "prepare":
                _, run, _ = prepare(workspace, paper, a.template.resolve(), a.allow_request_registration, a.toolchain)
                print(run)
            else:
                try:
                    state = run_paper(workspace, paper, a.template.resolve(), a.allow_request_registration, a.model, a.toolchain)
                    if state["state"] != "local_checks_passed":
                        raise RuntimeError("paper requires inspection before continuing: " + paper["id"])
                except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
                    write(workspace / "runs" / paper["id"] / "intake-error.json", {"state": "needs_attention", "error": str(exc), "at": now()})
                    print(str(exc), file=sys.stderr, flush=True)
                    raise SystemExit(1) from exc
                    print(paper["id"], "needs_attention:", str(exc), flush=True)
                    if a.paper:
                        raise


if __name__ == "__main__":
    main()
