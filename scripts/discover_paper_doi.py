#!/usr/bin/env python3
"""Read-only DOI discovery, bound to the exact frozen paper and both artifacts.

A failed registry lookup is uncertainty, never evidence that no DOI exists.
The report belongs outside the paper repository; publisher bytes stay frozen.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def person(value):
    parts = value.split(",", 1)
    return normalized(" ".join(reversed(parts)) if len(parts) == 2 else value)


def fetch_public(url, limit):
    with urlopen(Request(url, headers={"User-Agent": "Mathesis-paper-identity/1.0"}), timeout=30) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("public response exceeds expected size")
    return body


def discover(repo: Path, fetch=fetch_public):
    paper = json.loads((repo / "paper.json").read_text())
    work = paper["work"]
    report = {"schema": "mathesis.paper-doi-discovery.v1", "work": work["work"],
              "version": work["current_version"], "catalog_doi": work.get("doi"),
              "status": "unconfirmed", "candidates": [], "publication": "not_performed"}
    try:
        return resolve(repo, paper, report, fetch)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["limitation"] = str(exc)
        return report


def resolve(repo, paper, report, fetch):
    work = paper["work"]
    expected = {}
    for kind, default in (("pdf", "paper.pdf"), ("source", "source.archive")):
        info = paper["files"][kind]
        path = repo / info.get("original_path", info.get("path", default))
        if not path.is_file():
            raise ValueError("missing frozen " + kind + " artifact")
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != info["sha256"]:
            raise ValueError("frozen " + kind + " hash differs")
        expected[kind] = {"bytes": len(blob), "sha256": info["sha256"]}
    candidates = []
    if work.get("doi"):
        candidates.append(work["doi"])
    # Search even when the frozen catalog omits its existing public DOI.
    title = work["title"].replace("\\", "\\\\").replace('"', '\\"')
    search = "https://api.datacite.org/dois?" + urlencode({"query": f'titles.title:"{title}"', "page[size]": "10"})
    report["search_url"] = search
    try:
        hits = json.loads(fetch(search, 2 * 1024**2))
        for hit in hits.get("data", []):
            attrs = hit["attributes"]
            if not any(normalized(t["title"]) == normalized(work["title"]) for t in attrs.get("titles", [])):
                continue
            # Concept records are not source-version citations.
            if any(r.get("relationType") == "HasVersion" for r in attrs.get("relatedIdentifiers", [])):
                continue
            candidates.append(attrs["doi"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["search_limitation"] = str(exc)
    verified = []
    for doi in dict.fromkeys(candidates):
        candidate = {"doi": doi, "status": "unconfirmed"}
        report["candidates"].append(candidate)
        match = re.fullmatch(r"10\.5281/zenodo\.([1-9][0-9]*)", doi, re.I)
        if not match:
            candidate["reason"] = "Registry adapter currently verifies Zenodo artifacts only"
            continue
        try:
            record = json.loads(fetch("https://zenodo.org/api/records/" + match[1], 2 * 1024**2))
            if str(record["id"]) != match[1]:
                raise ValueError("requested registry record identity differs")
            candidate.update(verify_record(record, work, expected, fetch))
            if candidate["status"] == "verified":
                verified.append(candidate)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            candidate["reason"] = str(exc)
    if len(verified) == 1:
        report.update(status="verified", version_doi=verified[0]["doi"],
                      concept_doi=verified[0]["concept_doi"], files=verified[0]["files"])
    elif len(verified) > 1:
        report["limitation"] = "Multiple matching public deposits require operator reconciliation"
    else:
        report["limitation"] = "No exact-version DOI confirmed; absence is not established"
    return report


def verify_record(record, work, expected, fetch):
    metadata = record["metadata"]
    doi = "10.5281/zenodo." + str(record["id"])
    if metadata["doi"].lower() != doi:
        raise ValueError("record DOI identity differs")
    if normalized(metadata["title"]) != normalized(work["title"]):
        raise ValueError("title differs")
    if str(metadata["version"]) != str(work["current_version"]):
        raise ValueError("paper version differs")
    authors = [(person(a["name"]), normalized(a.get("affiliation", ""))) for a in work["authors"]]
    creators = [(person(a["name"]), normalized(a.get("affiliation", ""))) for a in metadata["creators"]]
    if authors != creators:
        raise ValueError("authors or affiliations differ")
    slug = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    if slug(metadata["license"]["id"]) != slug(work["license"]):
        raise ValueError("licence differs")
    parents = {v["parent"]["pid_value"] for v in metadata["relations"]["version"]}
    if len(parents) != 1 or not re.fullmatch(r"[1-9][0-9]*", next(iter(parents))):
        raise ValueError("concept DOI relationship is ambiguous")
    concept = "10.5281/zenodo." + next(iter(parents))
    if concept == doi:
        raise ValueError("concept record cannot identify the paper version")
    found = {}
    for file in record["files"]:
        for kind, identity in expected.items():
            if kind in found or file["size"] != identity["bytes"]:
                continue
            # Construct the public registry URL; never follow an arbitrary file URL.
            key = file["key"]
            if not key or "/" in key or "\\" in key:
                continue
            url = f'https://zenodo.org/api/records/{record["id"]}/files/{quote(key, safe="")}/content'
            body = fetch(url, identity["bytes"])
            sha = hashlib.sha256(body).hexdigest()
            if len(body) == identity["bytes"] and sha == identity["sha256"]:
                found[kind] = dict(identity, key=key, url=url)
    if set(found) != {"pdf", "source"}:
        raise ValueError("both frozen PDF and source hashes must match")
    return {"status": "verified", "doi": doi, "concept_doi": concept, "files": found,
            "metadata_checked": ["title", "version", "authors", "affiliations", "licence"]}
