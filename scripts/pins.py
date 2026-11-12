#!/usr/bin/env python3
"""Validate a checked-in source pin before passing it to checkout."""
import json
import os
from pathlib import Path
import re

config = json.loads(Path("publisher.json").read_text())
repository, commit = config["mathesis_repository"], config["mathesis_commit"]
if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("publisher.json needs a GitHub repository and exact 40-character Mathesis commit")
with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(f"repository={repository}\ncommit={commit}\n")
