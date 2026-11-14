#!/usr/bin/env python3
"""Read back deployed paper files and pages; never submit, review or award credit."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import urllib.request

import collection


def get(url, limit=128 * 1024 * 1024):
    request = urllib.request.Request(url, headers={"User-Agent": "mathesis-paper-factory-verification"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("published file exceeds limit")
        return data


def verify(stage, fetch=get):
    plan = json.loads((stage / "plan.json").read_text())
    inventory = json.loads((stage / "factory/factory.json").read_text())

    def check(row):
        result = {"work": row["work"], "version": row["version"], "ok": False}
        try:
            repo = plan["mapping"][row["work"]]
            site = row["site_url"]
            root = fetch(site, 8 * 1024 * 1024)
            if b'<html' not in root.lower():
                raise ValueError("paper landing is missing")
            expected_landing = stage / "papers" / repo / "site/index.html"
            if row["mode"] == "paper" and root != expected_landing.read_bytes():
                raise ValueError("published landing differs from prepared paper")
            manifest = json.loads(fetch(site + "mathesis-materials.json", 1024 * 1024))
            if manifest.get("repository_url") != row["repository_url"]:
                aliases = plan["config"].get("repository_aliases", {}).get(row["work"], [])
                old = next((a for a in aliases if manifest.get("repository_url") == "https://github.com/mathesis-papers/" + a), None)
                if old is None:
                    raise ValueError("published material repository differs")
                manifest = collection.relocate_materials(manifest, old, repo)
            if manifest.get("work") != row["work"] or manifest.get("version") != row["version"] or manifest.get("site_url") != site:
                raise ValueError("published material version or identity differs")
            for language, url in manifest.get("reading", {}).items():
                if not url.startswith(site):
                    raise ValueError("reading leaves paper site")
                reading = fetch(url, 8 * 1024 * 1024)
                expected = stage / "papers" / repo / "site" / language / "index.html"
                if row["mode"] == "paper" and expected.is_file() and reading != expected.read_bytes():
                    raise ValueError("published reading differs from prepared text")
            expected_manifest = stage / "papers" / repo / "site/mathesis-materials.json"
            if row["mode"] == "paper" and manifest != json.loads(expected_manifest.read_text()):
                raise ValueError("paper manifest has not been published yet")
            for kind, name in (("pdf", "paper.pdf"), ("source", "source.archive")):
                expected = row["files"].get(kind, {}).get("sha256")
                projected = stage / "output/papers" / repo / name
                if kind == "source" and row["mode"] == "authored_book":
                    continue  # Preserve authored book content; this publisher does not refresh its frozen projection.
                if not expected or not projected.is_file():
                    continue
                url = site + name if kind == "pdf" and row["mode"] == "paper" else "https://raw.githubusercontent.com/mathesis-papers/" + repo + "/main/" + name
                if hashlib.sha256(fetch(url, 128 * 1024 * 1024)).hexdigest() != expected:
                    raise ValueError("published " + kind + " hash differs")
            result.update(ok=True, reading_languages=sorted(manifest.get("reading", {})))
        except Exception as exc:
            result["error"] = str(exc)
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(check, inventory["entries"]))
    return {"schema": "mathesis.paper-factory-verification.v1", "snapshot_time": inventory["snapshot_time"],
            "papers": len(rows), "passed": sum(row["ok"] for row in rows), "entries": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--attempts", type=int, default=1)
    args = parser.parse_args()
    if args.report.resolve().is_relative_to(args.stage.resolve()):
        parser.error("report must be outside sealed stage")
    if not 1 <= args.attempts <= 10:
        parser.error("attempts must be between 1 and 10")
    for attempt in range(args.attempts):
        report = verify(args.stage)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f'Live paper checks: {report["passed"]}/{report["papers"]}', flush=True)
        if report["passed"] == report["papers"]:
            return
        if attempt + 1 < args.attempts:
            time.sleep(20)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
