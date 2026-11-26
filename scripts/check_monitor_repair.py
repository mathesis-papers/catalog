#!/usr/bin/env python3
"""Verify a small publisher repair with its pinned policy and existing tests."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'publisher.json').read_text())
    assert config['mathesis_repository'] == 'the-omega-institute/mathesis'
    pin = config['mathesis_commit']
    assert re.fullmatch('[0-9a-f]{40}', pin)
    with tempfile.TemporaryDirectory(prefix='catalog-monitor-policy-') as temp:
        source = Path(temp) / 'mathesis'
        def run(args, **kwargs):
            subprocess.run(args, check=True, **kwargs)
        run(['git', 'init', '-q', str(source)])
        run(['git', 'remote', 'add', 'origin', 'https://github.com/' + config['mathesis_repository'] + '.git'], cwd=source)
        run(['git', 'sparse-checkout', 'set', 'sdk/catalog', 'infra/verifier'], cwd=source)
        run(['git', 'fetch', '-q', '--depth=1', '--filter=blob:none', 'origin', pin], cwd=source)
        run(['git', 'checkout', '-q', '--detach', 'FETCH_HEAD'], cwd=source)
        run(['cargo', 'build', '--locked', '--manifest-path', str(source / 'sdk/catalog/Cargo.toml')], cwd=source)
        environment = dict(os.environ, MATHESIS_CATALOG_BINARY=str(source / 'sdk/catalog/target/debug/mathesis-catalog'),
                           PYTHONDONTWRITEBYTECODE='1')
        run(['python3', '-m', 'unittest', 'discover', '-s', 'tests'], cwd=root, env=environment)


if __name__ == '__main__':
    main()
