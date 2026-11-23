"""Transport adapter for the pinned Rust material-queue policy.

No Python fallback: admission and recovery must use the same tested policy as
the publisher's pinned Mathesis catalog binary.
"""
import json
import os
import shutil
import subprocess

SCHEMA = 'mathesis.material-policy.v1'


def decide(action, **fields):
    binary = os.environ.get('MATHESIS_CATALOG_BINARY') or shutil.which('mathesis-catalog')
    if not binary:
        raise ValueError('build the pinned mathesis-catalog and set MATHESIS_CATALOG_BINARY')
    request = {'schema': SCHEMA, 'action': action, **fields}
    result = subprocess.run([binary, 'material-plan'], input=json.dumps(request), text=True,
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError('Rust material policy refused the request: ' + result.stderr.strip()[:2000])
    response = json.loads(result.stdout)
    if response.get('schema') != SCHEMA:
        raise ValueError('unexpected Rust material policy response schema')
    return response


def transition(action, state, **fields):
    result = decide(action, state=state, **fields)
    if result['changed']:
        state.clear()
        state.update(result['state'])
    return result['changed']


def admission(jobs, capacity):
    return decide('admission', jobs=[{'phase': job['phase']} for job in jobs], capacity=capacity)
