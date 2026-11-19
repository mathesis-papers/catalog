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


def advance_version_tree(original, authored, work, version, source_commit):
    """Keep old manuscript and exposition sources under the same repository.

    Template manifests fence managed files; unrecognized human files survive.
    An existing immutable version path is never replaced.
    """
    old = json.loads(original['paper.json'][0])['work']
    new = json.loads(authored['paper.json'][0])['work']
    require(old['work'] == new['work'] == work and old['current_version'] < version == new['current_version'], 'invalid paper version advancement')
    require(old['repository_name'] == new['repository_name'], 'paper repository identity changed')
    managed = set()
    for name, (data, mode) in original.items():
        if name == 'template-manifest.json' or (name.startswith('template.d/') and name.endswith('.manifest.json')):
            manifest = json.loads(data)
            for path, expected in manifest['files'].items():
                require(path in original and hashlib.sha256(original[path][0]).hexdigest() == expected['sha256']
                        and original[path][1] == expected['mode'], 'human-edited managed template needs explicit rebase: ' + path)
                managed.add(path)
    if collection.MARKER in original and '.mathesis/book.json' not in original:
        marker = owned_collection(original, work, old['current_version'])
        managed.update(marker['sha256'])
    require(managed, 'existing paper has no verifiable template ownership')
    frozen = json.loads(original['.mathesis/generated.json'][0])
    owned = set(frozen['sha256']) | {'.mathesis/generated.json', '.mathesis/book.json', HISTORY,
                                      collection.MARKER, 'template-manifest.json', 'template.toml'}
    result = dict(authored)
    for name, entry in original.items():
        if name.startswith('versions/'):
            require(name not in result or result[name] == entry, 'immutable historical version changed')
            result[name] = entry
        elif name.startswith('books/') or name in frozen['sha256'] or name == '.mathesis/book.json':
            # Archive current-version mathematical content and teaching sources,
            # without duplicating all template code or generated renderings.
            archive = f"versions/v{old['current_version']}/materials/{name}"
            require(archive not in result or result[archive] == entry, 'historical material snapshot differs')
            result[archive] = entry
        elif name not in owned and name not in managed and not name.startswith('template.d/'):
            require(name not in result or result[name] == entry, 'unrelated human file conflicts with template update: ' + name)
            result[name] = entry
    history = {name:hashlib.sha256(value[0]).hexdigest() for name,value in result.items() if name.startswith('versions/')}
    result[HISTORY] = ((json.dumps({'schema':'mathesis.collection-history.v1','work':work,'source_commit':source_commit,'sha256':history},sort_keys=True,indent=2)+'\n').encode(),'100644')
    return result


def working_tree(root):
    inventory = serial.source_inventory(root)
    return {name: ((str((root/name).readlink()).encode() if entry['mode'] == '120000' else (root/name).read_bytes()), entry['mode'])
            for name,entry in inventory.items()}


def attach_history(source, authored, work, version, source_commit):
    """Construct the final source tree before fixed build/QA, not after it."""
    original, current = tracked_tree(source), working_tree(authored)
    previous_version = json.loads(original['paper.json'][0])['work']['current_version']
    if previous_version < version:
        final = advance_version_tree(original,current,work,version,source_commit)
    elif collection.MARKER in original and '.mathesis/book.json' not in original:
        final = migration_tree(original,current,work,version,source_commit)
    else:
        require(previous_version == version, 'paper version moved backwards')
        final = dict(current)
        for name,entry in original.items():
            if name.startswith('versions/') or name == HISTORY:
                require(name not in final or final[name] == entry,'historical version changed')
                final[name] = entry
    for name,(data,mode) in final.items():
        if current.get(name) == (data,mode): continue
        safe_name(name)
        path=authored/name
        require(path.resolve().is_relative_to(authored.resolve()),'historical destination escapes repository')
        path.parent.mkdir(parents=True,exist_ok=True)
        require(mode != '120000','historical source must not introduce symlinks')
        path.write_bytes(data)
        path.chmod(0o755 if mode == '100755' else 0o644)
    return final
