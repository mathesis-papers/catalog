#!/usr/bin/env python3
"""Deploy this run's exact uploaded Pages artifact through the public REST API."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request


def require(condition, message):
    if not condition:
        raise ValueError(message)


def github(method, path, body=None):
    command = ['gh', 'api', '--method', method, path]
    if body is not None:
        command += ['--input', '-']
    response = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                              text=True, capture_output=True, check=False)
    if response.returncode:
        # POST may have reached Pages. Never print the OIDC body or retry it.
        status = re.search(r'HTTP (\d{3})', response.stderr)
        detail = 'HTTP ' + status.group(1) if status else 'transport error'
        raise RuntimeError('Pages ' + method + ' failed (' + detail + '); retain this run and artifact for reconciliation')
    return json.loads(response.stdout)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def identity_token(environment):
    target = environment['ACTIONS_ID_TOKEN_REQUEST_URL']
    require(target.startswith('https://'), 'GitHub OIDC endpoint must use HTTPS')
    request = urllib.request.Request(target, headers={
        'Authorization': 'Bearer ' + environment['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        token = json.load(response)['value']
    require(isinstance(token, str) and token, 'GitHub OIDC token is missing')
    return token


def deploy(environment, api=github, oidc=identity_token, pause=time.sleep, emit=print):
    repository, source = environment['GITHUB_REPOSITORY'], environment['GITHUB_SHA']
    run, artifact = environment['GITHUB_RUN_ID'], environment['ARTIFACT_ID']
    name = environment['ARTIFACT_NAME']
    require(re.fullmatch(r'mathesis-papers/[A-Za-z0-9._-]+', repository)
            and re.fullmatch(r'[0-9a-f]{40}', source)
            and run.isdigit() and artifact.isdigit(), 'invalid deployment identity')
    prefix = 'repos/' + repository
    # Use the same public run listing as the upload visibility gate. Its
    # metadata can be visible before the individual-artifact lookup endpoint.
    for _ in range(18):
        listing = api('GET', prefix + '/actions/runs/' + run + '/artifacts?per_page=100')
        rows = [item for item in listing['artifacts'] if str(item.get('id')) == artifact]
        require(len(rows) <= 1, 'duplicate uploaded artifact identity')
        if rows:
            row = rows[0]
            break
        pause(5)
    else:
        raise RuntimeError('uploaded artifact is not visible; no deployment was submitted')
    origin = row.get('workflow_run', {})
    require(str(row.get('id')) == artifact and row.get('name') == name and row.get('expired') is False
            and str(origin.get('id')) == run and origin.get('head_sha') == source,
            'uploaded artifact belongs to another run or source')
    emit(json.dumps({'artifact_id': artifact, 'workflow_run': run, 'source_commit': source}))
    receipt = api('POST', prefix + '/pages/deployments', {
        'artifact_id': int(artifact), 'pages_build_version': source, 'oidc_token': oidc(environment)})
    deployment = str(receipt.get('id') or receipt.get('status_url', '').rstrip('/').split('/')[-1] or source)
    require(re.fullmatch(r'[A-Za-z0-9_-]+', deployment), 'Pages returned an invalid deployment ID')
    emit(json.dumps({'deployment_id': deployment, 'artifact_id': artifact, 'workflow_run': run}))
    for _ in range(90):
        status = api('GET', prefix + '/pages/deployments/' + deployment)
        if status.get('status') == 'succeed':
            url = receipt['page_url'] if 'page_url' in receipt else receipt['pages_url']
            require(isinstance(url, str) and url.startswith('https://') and not any(c in url for c in '\r\n'),
                    'invalid Pages URL')
            return {'deployment_id': deployment, 'page_url': url}
        require(isinstance(status.get('status'), str) and status['status'] not in
                ('deployment_failed', 'deployment_content_failed', 'deployment_cancelled', 'deployment_lost'),
                'Pages deployment did not succeed; inspect original deployment ' + deployment)
        pause(5)
    raise RuntimeError('Pages deployment remains pending; inspect original deployment ' + deployment)


if __name__ == '__main__':
    result = deploy(os.environ)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write('page_url=' + result['page_url'] + '\n')
