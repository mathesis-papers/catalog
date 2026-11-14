#!/usr/bin/env python3
"""Prepare a local collection-to-book migration; never push or edit publisher controls."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

import collection
import serial_content as serial

HISTORY = ".mathesis/collection-history.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_name(name):
    p = PurePosixPath(name)
    require(isinstance(name, str) and name and str(p) == name and not p.is_absolute()
            and not any(x in ("..", ".git") for x in p.parts) and "\\" not in name,
            "unsafe tracked path")
    return p


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def tracked_tree(root):
    require(not git(root, "status", "--porcelain", "--untracked-files=all").strip(),
            "commit or preserve source changes before promotion")
    result = {}
    with tarfile.open(fileobj=io.BytesIO(git(root, "archive", "HEAD"))) as archive:
        for item in archive:
            if item.isdir():
                continue
            safe_name(item.name)
            require(item.isfile() or item.issym(), "unsupported tracked file type")
            data = item.linkname.encode() if item.issym() else archive.extractfile(item).read()
            result[item.name] = (data, "120000" if item.issym() else "100755" if item.mode & 0o111 else "100644")
    return result


def owned_collection(tree, work, version):
    marker = json.loads(tree[collection.MARKER][0])
    require(marker["schema"] == collection.SCHEMA and marker["work"] == work
            and marker["version"] == version, "collection identity differs")
    require(set(marker["sha256"]) == set(marker["git_blobs"]), "collection inventory differs")
    for name, expected in marker["sha256"].items():
        safe_name(name)
        require(name in tree and tree[name][1] != "120000", "missing or linked collection file")
        data = tree[name][0]
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        require(hashlib.sha256(data).hexdigest() == expected and blob == marker["git_blobs"][name],
                "collection file edited outside publisher: " + name)
    return marker


def migration_tree(original, authored, work, version, source_commit):
    """Preserve original snapshots and refuse to overwrite unrelated authorship."""
    marker = owned_collection(original, work, version)
    source = json.loads(original["paper.json"][0])
    selected = json.loads(authored["paper.json"][0])
    require(source["work"] == selected["work"], "paper identity or DOI changed")
    expected_files = json.loads(json.dumps(source["files"]))
    expected_files["source"].update(path="manuscript/", original_path="manuscript/original-source.archive")
    require(selected["files"] == expected_files, "unexpected frozen source adaptation")
    generated = json.loads(original[".mathesis/generated.json"][0])
    front = b"\n<!-- Mathesis generated repository guide; refresh with paper.py front. -->\n"
    for name, expected in generated["sha256"].items():
        target = "manuscript/original-source.archive" if name == "source.archive" else name
        require(target in authored, "authored projection lost original file: " + name)
        data = authored[target][0].split(front, 1)[0] if name == "README.md" else authored[target][0]
        if name != "paper.json":
            require(hashlib.sha256(data).hexdigest() == expected, "authored projection changed original bytes: " + name)
    mapping = json.loads(authored[".mathesis/book.json"][0])
    require(mapping["work"] == work and mapping["version"] == version, "book mapping differs")
    # Old paths stay inert, publisher-owned snapshots, never new book inputs.
    history = {n: hashlib.sha256(v[0]).hexdigest() for n, v in original.items() if n.startswith("versions/")}
    require(history and all(n in marker["sha256"] for n in history), "unowned historical snapshot")
    for name in history:
        require(len(safe_name(name).parts) >= 3 and re.fullmatch(r"v[1-9][0-9]*", safe_name(name).parts[1]), "invalid historical version path")
        require(name not in authored or authored[name] == original[name], "authored tree rewrites historical snapshot")
    result = {n: v for n, v in original.items() if n.startswith("versions/") or n not in marker["sha256"]}
    result.pop(collection.MARKER, None)
    for name, entry in authored.items():
        require(name not in result or result[name] == entry, "unrelated file conflicts with authored tree: " + name)
        result[name] = entry
    require(HISTORY not in result, "history was already migrated; inspect the existing book")
    record = {"schema": "mathesis.collection-history.v1", "work": work,
              "source_commit": source_commit, "sha256": dict(sorted(history.items()))}
    result[HISTORY] = ((json.dumps(record, sort_keys=True, indent=2) + "\n").encode(), "100644")
    return result


def prepare(run, catalog, output):
    envelope = serial.read(run / "envelope.json")
    require(serial.read(run / "checkpoint.json")["state"] == "local_checks_passed", "paper has not passed local checks")
    paper = envelope["identity"]["paper"]
    source = Path(envelope["source_repository_path"]).resolve()
    authored = Path(envelope["repository_path"]).resolve()
    require(source != authored, "this command migrates collections only; update existing books normally")
    output = output.resolve()
    require(not output.exists(), "promotion output already exists; inspect it instead of overwriting")
    require(not any(output.is_relative_to(p) or p.is_relative_to(output) for p in (source, authored, catalog.resolve())),
            "promotion output must be outside source repositories")
    original, authored_tree = tracked_tree(source), tracked_tree(authored)
    require((run / "source-sha256.json").is_file()
            and serial.read(run / "source-sha256.json") == serial.source_inventory(authored),
            "authored sources differ from the checked build; rebuild without another model call")
    # Never promote sources whose fixed host outputs were subsequently replaced.
    expected = serial.read(run / "site-sha256.json")
    actual = {str(p.relative_to(authored / "_site")): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((authored / "_site").rglob("*")) if p.is_file() and not p.is_symlink()}
    require(actual == expected, "rendered output differs from checked artifacts")
    serial.check_result(serial.read(run / "result.json"), authored)
    require(git(source, "remote", "get-url", "origin").decode().strip().removesuffix(".git")
            == "https://github.com/" + paper["repository"], "source repository differs")
    source_commit = git(source, "rev-parse", "HEAD").decode().strip()
    tree = migration_tree(original, authored_tree, paper["id"], int(paper["version"]), source_commit)
    output.mkdir(parents=True)
    checkout = output / "repository"
    subprocess.run(["git", "clone", "--no-hardlinks", str(source), str(checkout)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(checkout), "remote", "set-url", "origin", "https://github.com/" + paper["repository"] + ".git"], check=True)
    subprocess.run(["git", "-C", str(checkout), "switch", "-c", "content/accepted-paper-products"], check=True, stdout=subprocess.DEVNULL)
    for name in original.keys() - tree.keys():
        (checkout / name).unlink()
    for name, (data, mode) in tree.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.is_file():
            path.unlink()
        if mode == "120000":
            path.symlink_to(data.decode())
        else:
            path.write_bytes(data)
            path.chmod(0o755 if mode == "100755" else 0o644)
    # Remove only directories emptied by removal of the collection projection.
    for path in sorted(checkout.rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink() and ".git" not in path.relative_to(checkout).parts:
            try:
                path.rmdir()
            except OSError:
                pass
    config = serial.read(catalog / "publisher.json")
    require(paper["id"] not in config.get("book_works", []), "publisher already treats this collection as an authored book")
    config["book_works"] = sorted(set(config.get("book_works", [])) | {paper["id"]})
    serial.write(output / "publisher-proposal.json", config)
    serial.write(output / "promotion.json", {"schema": "mathesis.local-content-promotion.v1", "work": paper["id"],
        "version": paper["version"], "repository": paper["repository"], "source_commit": source_commit,
        "authored_commit": git(authored, "rev-parse", "HEAD").decode().strip(), "run": str(run.resolve()),
        "publisher_before_sha256": hashlib.sha256((catalog / "publisher.json").read_bytes()).hexdigest(),
        "publisher_update_required_before_publication": True, "publication": "not_performed",
        "files": {n: {"sha256": hashlib.sha256(v[0]).hexdigest(), "mode": v[1]} for n, v in sorted(tree.items())}})
    print(checkout)
    return checkout


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--catalog", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.run.resolve(), args.catalog.resolve(), args.output)
