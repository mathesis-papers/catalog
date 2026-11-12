#!/usr/bin/env python3
"""Stage anonymous public inputs, then publish checked projections via the GitHub App.

No token is read in prepare. Apply uses Git data transactions (one atomic ref
update per repository), never force-pushes, and resumes incomplete paper setup.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ORG = "mathesis-papers"
SITE = "https://papers.trureturing.org"
TITLE = "Prepare the reading, visualizations and slides"
MARKER = "<!-- mathesis-publisher:prepare-materials:v1 -->"
CATALOG_FILES = {"README.md", "works.json", "nodes.json", "stats.json", "snapshot.json", "repositories.json", "archive-index.json"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(*args, cwd=None):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


def sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def tree_files(root, exclude=()):
    result = {}
    for directory, dirs, files in os.walk(root):
        current = Path(directory)
        if current == root:
            dirs[:] = sorted(d for d in dirs if d not in {".git", ".cache", "build", "node_modules", "_site", *exclude})
        for name in sorted([*dirs, *files]):
            path = current / name
            if path.is_symlink():
                target = os.readlink(path)
                require(not os.path.isabs(target) and path.resolve().is_relative_to(root.resolve()), f"symlink escapes staged output: {path}")
                result[path.relative_to(root).as_posix()] = target.encode()
                if name in dirs:
                    dirs.remove(name)
        for name in sorted(files):
            path = current / name
            if not path.is_symlink():
                result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def file_mode(path):
    return "120000" if path.is_symlink() else "100755" if path.stat().st_mode & 0o111 else "100644"


def seal_files(stage):
    return {p: {"sha256": hashlib.sha256(b).hexdigest(), "mode": file_mode(stage / p)}
            for p, b in tree_files(stage, exclude=("template", "snapshot")).items() if p != "seal.json"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, token, base="https://api.github.com"):
        parsed = urllib.parse.urlsplit(base)
        require(base == "https://api.github.com" or (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")), "API override must be loopback")
        self.base = base.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, method, path, data=None, missing=False):
        require(path.startswith("/") and not path.startswith("//"), "invalid API path")
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, method=method, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json", "User-Agent": "mathesis-papers-publisher"})
        try:
            with self.opener.open(req, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if missing and code == 404:
                return None
            # Do not echo request headers or token, even on API failures.
            raise RuntimeError(f"GitHub {method} {path}: HTTP {code}") from None

    def repo(self, name, missing=False):
        return self.call("GET", f"/repos/{ORG}/{name}", missing=missing)

    def state(self, name):
        info = self.repo(name)
        branch = info["default_branch"]
        prefix = f"/repos/{ORG}/{name}/git"
        ref = self.call("GET", prefix + "/ref/heads/" + urllib.parse.quote(branch, safe=""))
        commit = self.call("GET", prefix + "/commits/" + ref["object"]["sha"])
        tree = self.call("GET", prefix + "/trees/" + commit["tree"]["sha"] + "?recursive=1")
        require(not tree.get("truncated"), "Git tree truncated; refusing partial synchronization")
        entries = {x["path"]: x for x in tree["tree"] if x["type"] != "tree"}
        return branch, ref["object"]["sha"], commit["tree"]["sha"], entries

    def blob(self, name, entry):
        value = self.call("GET", f"/repos/{ORG}/{name}/git/blobs/{entry['sha']}")
        require(value["encoding"] == "base64", "unexpected blob encoding")
        return base64.b64decode(value["content"])

    def sync(self, name, files, managed, message, state=None, modes=None):
        branch, parent, base_tree, entries = state or self.state(name)
        changes = []
        prefix = f"/repos/{ORG}/{name}/git"
        for path, data in sorted(files.items()):
            require(managed(path), f"unmanaged destination: {path}")
            old = entries.get(path, {})
            mode = (modes or {}).get(path, "100644")
            if old.get("sha") == sha(data) and old.get("mode") == mode:
                continue
            blob = self.call("POST", prefix + "/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            changes.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
        for path, entry in sorted(entries.items()):
            if managed(path) and path not in files:
                require(path != "CNAME", "refusing to remove CNAME")
                changes.append({"path": path, "mode": entry["mode"], "type": entry["type"], "sha": None})
        if not changes:
            print(f"{name}: unchanged")
            return False
        tree = self.call("POST", prefix + "/trees", {"base_tree": base_tree, "tree": changes})
        commit = self.call("POST", prefix + "/commits", {"message": message, "tree": tree["sha"], "parents": [parent]})
        self.call("PATCH", prefix + "/refs/heads/" + urllib.parse.quote(branch, safe=""), {"sha": commit["sha"], "force": False})
        print(f"{name}: committed {len(changes)} paths")
        return True

    def pages(self, name, build_type):
        path = f"/repos/{ORG}/{name}/pages"
        existing = self.call("GET", path, missing=True)
        if existing is None:
            self.call("POST", path, {"build_type": build_type})
        elif existing.get("build_type") != build_type:
            self.call("PUT", path, {"build_type": build_type})

    def issue(self, name, checklist):
        page = 1
        while True:
            items = self.call("GET", f"/repos/{ORG}/{name}/issues?state=all&per_page=100&page={page}")
            for item in items:
                if "pull_request" not in item and (MARKER in (item.get("body") or "") or item["title"] == TITLE):
                    return
            if len(items) < 100:
                break
            page += 1
        self.call("POST", f"/repos/{ORG}/{name}/issues", {"title": TITLE, "body": MARKER + "\n\n" + checklist})


def material_downloads(mapping, destination):
    opener = urllib.request.build_opener(NoRedirect())
    for repo in sorted(set(mapping.values())):
        url = f"{SITE}/{repo}/mathesis-materials.json"
        try:
            # The site sits behind Cloudflare, which refuses the default Python-urllib agent.
            request = urllib.request.Request(url, headers={"User-Agent": "mathesis-papers-publisher", "Accept": "application/json"})
            with opener.open(request, timeout=30) as response:
                raw = response.read(1024 * 1024 + 1)
                require(len(raw) <= 1024 * 1024, "materials manifest exceeds 1 MiB")
                value = json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # A repository may not yet have published any materials.
                continue
            raise
        write(destination / (repo + ".json"), value)


def prepare(binary, source, stage):
    require(not stage.exists(), "stage already exists; use a fresh directory")
    stage.mkdir(parents=True)
    config = read(source / "publisher.json")
    mapping = read(source / "repositories.json")
    allowed = read(source / "publish.json")
    require(isinstance(allowed, list) and len(allowed) == len(set(allowed)) and all(isinstance(w, str) for w in allowed), "publish.json must be a unique list of work ids")
    snapshot, output = stage / "snapshot", stage / "output"
    run(binary, "snapshot", "--api-base", config["api_base"], "--output", snapshot)
    run(binary, "fetch", "--snapshot", snapshot)
    args = [binary, "render", "--snapshot", snapshot, "--output", output, "--organization", ORG,
            "--site-base", config["api_base"], "--mapping", source / "repositories.json"]
    run(*args)
    run(binary, "check", "--output", output)
    works = read(output / "catalog/works.json")["works"]
    selected = {w["work"]: w for w in works if w["publication"] == "public"}
    for work in allowed:
        require(work in selected, f"allow-listed work is absent or out of scope: {work}")
    planned = dict(mapping)
    for work in allowed:
        planned.setdefault(work, selected[work]["repository_name"])
    require(len({r.lower() for r in planned.values()}) == len(planned), "repository mapping collision")
    require(all(r not in ("catalog", ".github", "paper-template", "mathesis-papers.github.io", ".", "..") for r in planned.values()), "reserved repository name")
    write(stage / "planned-repositories.json", planned)
    material_downloads(mapping, stage / "materials")
    args[-1] = stage / "planned-repositories.json"
    run(*args, "--materials", stage / "materials")
    run(binary, "check", "--output", output)
    write(stage / "plan.json", {"original_mapping": mapping, "mapping": planned, "new_works": sorted(set(planned) - set(mapping)), "allow_list": allowed, "config": config})
    # Pin a template snapshot and prepare every fill/check locally before the first write.
    template = stage / "template"
    require(re.fullmatch(r"[a-f0-9]{40}", config["template_commit"]) is not None, "template commit must be a full SHA")
    run("git", "-c", "credential.helper=", "-c", "core.askPass=", "clone", "--no-checkout", "https://github.com/mathesis-papers/paper-template.git", template)
    run("git", "checkout", "--detach", config["template_commit"], cwd=template)
    checklist = (template / "docs/layer-mathesis/PREPARATION_CHECKLIST.md").read_text()
    (stage / "checklist.md").write_text(checklist)
    for work in read(stage / "plan.json")["new_works"]:
        repo = planned[work]
        dest = stage / "papers" / repo
        # Archive only committed template bytes; .git and task caches never enter a paper.
        dest.mkdir(parents=True)
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=template, check=True, capture_output=True).stdout
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(dest, filter="data")
        row = selected[work]
        # Reuse the exact recorded title explicitly, pending a reviewed Chinese translation.
        run(sys.executable, dest / "docs/layer-mathesis/scripts/paper.py", "--root", dest, "fill",
            "--input", output / "papers" / repo, "--slug", repo, "--title-zh", row["title"], "--title-en", row["title"], "--repository", ORG + "/" + repo)
        run(sys.executable, dest / "docs/layer-mathesis/scripts/paper.py", "--root", dest, "check")
        run("./tbook", "check", "--offline", cwd=dest)
    # Seal staged bytes, including plan and checklist. Apply rechecks before any mutation.
    write(stage / "seal.json", seal_files(stage))
    print("Prepared and checked; no authenticated requests or remote writes.")


def apply(api, source, stage):
    seal = read(stage / "seal.json")
    actual = seal_files(stage)
    require(actual == seal, "staged publisher bytes changed after validation")
    plan = read(stage / "plan.json")
    require(plan["original_mapping"] == read(source / "repositories.json") and plan["allow_list"] == read(source / "publish.json"), "publisher control files changed since prepare")
    require(plan["config"] == read(source / "publisher.json"), "publisher pins changed since prepare")
    output = stage / "output"
    require((output / "site/CNAME").read_bytes() == b"papers.trureturing.org\n", "generated CNAME differs")
    # Read and validate all shared destination heads before the first write.
    states = {name: api.state(name) for name in ("catalog", ".github", "mathesis-papers.github.io")}
    for name in ("repositories.json", "publish.json", "publisher.json"):
        entry = states["catalog"][3].get(name)
        require(entry is not None, f"remote publisher control missing: {name}")
        remote = json.loads(api.blob("catalog", entry))
        expected = [read(source / name)]
        if name == "repositories.json":
            expected.append(plan["mapping"])  # A completed apply is safe to rerun.
        require(remote in expected, f"remote publisher control changed since checkout: {name}")
    current_cname = states["mathesis-papers.github.io"][3].get("CNAME")
    if current_cname:
        require(api.blob("mathesis-papers.github.io", current_cname).strip() == b"papers.trureturing.org", "remote CNAME differs; refusing replacement")
    for work in plan["new_works"]:
        repo = plan["mapping"][work]
        info = api.repo(repo, missing=True)
        created = info is None
        if created:
            info = api.call("POST", f"/repos/{ORG}/paper-template/generate", {"owner": ORG, "name": repo, "private": False, "include_all_branches": False})
        require(not info.get("private") and not info.get("archived"), "paper repository must be public and active")
        # Template generation can take a few seconds to expose its default branch.
        for attempt in range(6):
            try:
                state = api.state(repo)
                break
            except RuntimeError as exc:
                if "HTTP 404" not in str(exc) or attempt == 5:
                    raise
                time.sleep(min(2 ** attempt, 8))
        entries = state[3]
        if "paper.json" in entries:
            remote = json.loads(api.blob(repo, entries["paper.json"]))["work"]
            require(remote["work"] == work and remote["repository_name"] == repo, "existing paper belongs to a different work")
            require(".mathesis/book.json" in entries and ".mathesis/generated.json" in entries, "existing paper import incomplete; review required")
        else:
            require(created or info.get("template_repository", {}).get("full_name") == ORG + "/paper-template", "existing unpopulated repository is not from paper-template; review required")
        # Enable after provenance/identity checks and before the initialization commit.
        api.pages(repo, "workflow")
        if "paper.json" not in entries:
            local = tree_files(stage / "papers" / repo)
            # Import the pinned template too; this makes template-main drift harmless.
            modes = {p: file_mode(stage / "papers" / repo / p) for p in local}
            api.sync(repo, local, lambda p: True, "Initialize Mathesis paper " + work, state, modes)
        api.issue(repo, (stage / "checklist.md").read_text())
    api.sync(".github", {"profile/README.md": (output / "profile/README.md").read_bytes()}, lambda p: p == "profile/README.md", "Refresh Mathesis organization profile", states[".github"])
    site_files = tree_files(output / "site")
    api.sync("mathesis-papers.github.io", site_files, lambda p: p in {"index.html", "CNAME", ".nojekyll"} or p.startswith("doi/"), "Refresh Mathesis paper site", states["mathesis-papers.github.io"])
    catalog_files = tree_files(output / "catalog")
    api.sync("catalog", catalog_files, lambda p: p in CATALOG_FILES or p.startswith("materials/"), "Refresh Mathesis catalog and repository mapping", states["catalog"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "apply"))
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--catalog-binary", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        require(args.catalog_binary is not None, "prepare needs --catalog-binary")
        prepare(args.catalog_binary.resolve(), args.source.resolve(), args.stage.resolve())
    else:
        apply(API(os.environ["PUBLISHER_TOKEN"]), args.source.resolve(), args.stage.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Publisher: {exc}")
