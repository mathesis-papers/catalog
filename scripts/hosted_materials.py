#!/usr/bin/env python3
"""Reconcile one CMA material job with private durable state and fixed host QA.

The hourly publisher remains the catalog authority. This module owns only
source production checkpoints and checked source publication, never review,
DOI deposits, proof admission or credit.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import cma_content
import material_policy
import promote_content
import publish
import serial_content as serial
import verify_factory

require = publish.require


class Ledger:
    """Git transactions, private repository, no expiring Actions-cache authority."""
    def __init__(self, api, repository):
        require(repository == 'production-state', 'unexpected production state repository')
        require(api.repo(repository).get('private') is True, 'production operation state must remain private')
        self.api, self.repository = api, repository
        self.expected = {}

    def get_bytes(self, path):
        cma_content.safe_path(path)
        entry = self.api.state(self.repository)[3].get(path)
        self.expected[path] = entry['sha'] if entry else None
        return self.api.blob(self.repository, entry) if entry else None

    def get(self, key):
        data = self.get_bytes('jobs/' + key + '.json')
        return json.loads(data) if data else None

    def put(self, state, files=None):
        data = dict(files or {})
        data['jobs/' + state['key'] + '.json'] = (json.dumps(state, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()
        current=self.api.state(self.repository)
        path='jobs/'+state['key']+'.json'
        old=current[3].get(path,{})
        require(path in self.expected and self.expected[path] == old.get('sha'), 'production checkpoint advanced under another operator')
        for name,content in data.items():
            if name != path and name in current[3]:
                require(current[3][name]['sha'] == publish.sha(content), 'refusing to replace an existing immutable source artifact')
        self.api.sync(self.repository, data, lambda p: p in data, 'Checkpoint exact-version material production',current)
        self.expected[path]=publish.sha(data[path])

    def jobs(self):
        entries = self.api.state(self.repository)[3]
        jobs=[]
        for name,row in sorted(entries.items()):
            if name.startswith('jobs/') and name.endswith('.json'):
                self.expected[name]=row['sha']
                jobs.append(json.loads(self.api.blob(self.repository,row)))
        return jobs


def public_detail(base, work, fetch=verify_factory.get):
    """Determine lane from actual anonymous public resources, not ID spelling."""
    require(work['publication'] == 'public' and work.get('license') == 'CC-BY-4.0', 'work is not eligible public archive input')
    base = base.rstrip('/')
    require(base == 'https://mathesis-omega-research.apps.chrono-ai.fun', 'unexpected publication source')
    encoded = urllib.parse.quote(work['work'], safe='')
    try:
        detail = json.loads(fetch(base + '/api/v1/collected-papers/' + encoded + '?version=' + str(work['current_version']), 8 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
        record = urllib.parse.quote(work['record'], safe='')
        detail = json.loads(fetch(base + '/api/v1/papers/' + record, 8 * 1024 * 1024))
        summary = detail['summary']
        require(summary.get('archive_consent') is True and detail.get('archive')
                and detail['publication']['version'] == work['current_version'], 'external paper is not archive-eligible at this version')
        return 'external', detail
    require(detail['id'] == work['work'] and int(detail['revision']) == work['current_version']
            and detail.get('license') == work['license'], 'collected public version differs')
    return 'collection', detail


def candidate_works(root):
    config = serial.read(root / 'publisher.json')
    mapping = serial.read(root / 'repositories.json')
    reservations = serial.read(root / 'production.json').get('manual_reservations', {})
    candidates = []
    for work in serial.read(root / 'works.json')['works']:
        if work['publication'] != 'public' or work['work'] not in mapping:
            continue
        # Existing human/cohort work is reserved; never start another producer
        # over the running historical backfill. New records remain automatic.
        if reservations.get(work['work']) == work['current_version']:
            continue
        candidates.append(dict(work, repository_name=mapping[work['work']]))
    return sorted(candidates, key=lambda w: (w.get('published_on') or '', w['work']))


def checkout(repository, destination, commit=None):
    if not destination.exists():
        subprocess.run(['git', '-c', 'credential.helper=', 'clone', '--no-checkout', 'https://github.com/' + repository + '.git', str(destination)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['git', '-C', str(destination), 'checkout', '--detach', commit or 'origin/HEAD'], check=True, stdout=subprocess.DEVNULL)
    head = serial.command(['git', 'rev-parse', 'HEAD'], destination)
    require(commit is None or head == commit, 'existing production source pin changed')
    return head


def prepare(root, workspace, work, config, state=None, factory_stage=None):
    paper = {'id': work['work'], 'version': work['current_version'], 'title': work['title'],
             'repository': 'mathesis-papers/' + work['repository_name']}
    template = workspace / 'template'
    template_pin=(state.get('template_adaptation') or {}).get('template_commit',state['identity']['template_commit']) if state else config['template_commit']
    checkout('mathesis-papers/paper-template', template, template_pin)
    # Each immutable source has its own working directory. Caches remain outside
    # committed source and are shared by the fixed host toolchain.
    folder = workspace / ('job-' + serial.digest({'work': paper['id'], 'version': paper['version']}))
    folder.mkdir(parents=True, exist_ok=True)
    serial.write(folder / 'cohort.json', {'schema':'mathesis.serial-cohort.v1', 'papers':[paper]})
    source = folder / 'papers' / paper['id']
    head = checkout(paper['repository'], source, state.get('publication_base',state['paper_commit']) if state else None)
    projection = folder / 'frozen-input'
    if state and state.get('input_packet') is None:
        projection = None  # Original operator admission used the pinned public repo.
    elif state:
        cma_content.restore_projection(state['input_packet'], projection)
    else:
        require(factory_stage is not None, 'material admission requires the publisher-checked public projection')
        packet = cma_content.projection_packet(factory_stage / 'output/papers' / work['repository_name'], config['api_base'])
        cma_content.restore_projection(packet, projection)
    skill = None
    if state and state['identity']['skill_sha256'] != hashlib.sha256(serial.SKILL.read_bytes()).hexdigest():
        commit = state['catalog_commit']
        require(re.fullmatch(r'[a-f0-9]{40}', commit) is not None, 'production skill needs an immutable source revision')
        request = urllib.request.Request('https://raw.githubusercontent.com/mathesis-papers/catalog/' + commit
                                         + '/skills/mathesis-paper-production/SKILL.md', headers={'User-Agent':'mathesis-paper-production'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(256 * 1024 + 1)
        require(len(data) <= 256 * 1024 and hashlib.sha256(data).hexdigest() == state['identity']['skill_sha256'],
                'original production skill bytes differ')
        skill = folder / 'original-production-skill.md'
        skill.write_bytes(data)
    allow_requests = state['identity']['allow_request_registration'] if state else config.get('allow_request_registration', False)
    repo, run, envelope = serial.prepare(folder, paper, template, allow_requests, source_projection=projection, skill_path=skill)
    if state:
        expected=copy.deepcopy(state['identity'])
        expected['template_commit']=template_pin
        require(envelope['identity'] == expected, 'saved production input changed; retain original result for inspection')
    return repo, run, envelope, head


def save_sources(cma, ledger, state):
    prefix = cma_content.artifact_prefix(state)
    handoff = cma.file(state['agent_id'], prefix + 'handoff.json')
    pack = cma.file(state['agent_id'], prefix + 'sources.tar.gz')
    receipt = json.loads(handoff)
    require(receipt['identity'] == state['identity'] and hashlib.sha256(pack).hexdigest() == receipt['archive_sha256'], 'CMA source receipt differs')
    state.update(phase='sources_saved', source_archive_sha256=receipt['archive_sha256'], visualization=receipt['result']['visualization'])
    ledger.put(state, {'sources/' + state['key'] + '/handoff.json':handoff,
                       'sources/' + state['key'] + '/sources.tar.gz':pack})


def restore_sources(ledger, state, repo, run):
    approved = state.get('validated_handoff')
    if approved:
        prefix = approved['prefix']
        require(prefix.startswith('validated/' + state['key'] + '/'), 'validated handoff belongs to another job')
        manifest_bytes = ledger.get_bytes(prefix + 'archive-manifest.json')
        require(manifest_bytes is not None and hashlib.sha256(manifest_bytes).hexdigest() == approved['manifest_sha256'],
                'validated handoff manifest differs')
        manifest = json.loads(manifest_bytes)
        require(manifest['key'] == state['key'], 'validated handoff source identity differs')
    class SavedFiles:
        def file(self, agent, path):
            name = path.rsplit('/',1)[1]
            data = ledger.get_bytes((approved['prefix'] if approved else 'sources/' + state['key'] + '/') + name)
            require(data is not None, 'durable source pack is missing; never restart authoring')
            if approved:
                entry = manifest['files'][name]
                require(len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256'],
                        'validated handoff artifact differs')
            return data
    imported = copy.deepcopy(state)
    imported['phase'] = 'sources_ready'
    return cma_content.import_sources(SavedFiles(), imported, repo, run,
                                      approved_template_rebase=bool(state.get('template_adaptation')),
                                      approved_operator_handoff=bool(approved),
                                      approved_formalization_pin=manifest.get('formalization_pin') if approved else None)


def material_workspaces(environment):
    shared = environment.get('CMA_COLLECTION_WORKSPACE') or environment.get('CMA_EXTERNAL_WORKSPACE')
    require(shared, 'configure an existing Mathesis CMA workspace')
    return {'collection':shared, 'external':environment.get('CMA_EXTERNAL_WORKSPACE') or shared}


def isolation_workspace(config, workspaces, lane):
    return workspaces['external' if lane == 'collection' else 'collection'] if config.get('execution_mode') == 'isolated' else None


def cleanup(cma, ledger, state):
    if not state.get('agent_id') or state.get('cleanup_complete'):
        return
    require(state.get('source_archive_sha256'), 'never delete the only copy of authored work')
    agent = cma.call('GET','/api/v1/agents/'+state['agent_id'],missing=True)
    if agent is None or agent.get('status') == 'deleted':
        state['cleanup_complete'] = True
        ledger.put(state)
        return
    if agent.get('status') != 'stopped':
        if not state.get('cleanup_stop_key'):
            state['cleanup_stop_key'] = 'mathesis:paper-materials:stop:' + state['key']
            ledger.put(state)
        if not state.get('cleanup_stop_requested'):
            cma.call('POST','/api/v1/agents/'+state['agent_id']+'/stop',{},key=state['cleanup_stop_key'])
            state['cleanup_stop_requested'] = True
            ledger.put(state)
        agent = cma.call('GET','/api/v1/agents/'+state['agent_id'],missing=True)
        if agent is None or agent.get('status') == 'deleted':
            state['cleanup_complete'] = True
            ledger.put(state)
            return
        if agent.get('status') != 'stopped':
            return
    if not state.get('cleanup_intent') or not state.get('cleanup_idempotency_key'):
        state['cleanup_intent'] = True
        state['cleanup_idempotency_key'] = 'mathesis:paper-materials:delete:' + state['key']
        ledger.put(state)
    if not state.get('cleanup_requested'):
        receipt = cma.call('DELETE','/api/v1/agents/'+state['agent_id'],key=state['cleanup_idempotency_key'],missing=True)
        state['cleanup_requested'] = True
        if receipt and receipt.get('id'):
            state['cleanup_receipt_id'] = cma_content.identifier(receipt['id'])
        ledger.put(state)
    agent = cma.call('GET','/api/v1/agents/'+state['agent_id'],missing=True)
    if agent is None or agent.get('status') == 'deleted':
        state['cleanup_complete'] = True
        ledger.put(state)


def recover_publication(ledger,state):
    intent=state.get('publication_intent')
    if not intent:
        return False
    repository=state['identity']['paper']['repository'].split('/',1)[1]
    remote=ledger.api.state(repository)
    actual={name:{'sha':row['sha'],'mode':row['mode']} for name,row in remote[3].items()}
    if actual == intent['source_tree']:
        state.update(phase='publication_pending',published_commit=remote[1],expected_manifest=intent['manifest'])
        ledger.put(state)
        return True
    require(remote[1] == state.get('publication_base',state['paper_commit']),'paper repository advanced; inspect saved checked output without restarting the model')
    return False


def plan_template_adaptation(state, template):
    return material_policy.transition('adapt_template', state, template_commit=template)


def wait_for(state, stage, error, ledger):
    state['waiting_stage'] = stage
    state['last_error'] = {'type': type(error).__name__, 'observed_at': serial.now()}
    if isinstance(error, cma_content.ProviderError):
        state['last_error'].update(error.observation())
    ledger.put(state)


def resume_saved_build(state, config, ledger):
    recovery = config.get('saved_build_recovery', {}).get(state['key'])
    if material_policy.transition('resume_build', state, recovery=recovery,
                                  toolchain_image=config.get('toolchain_image', ''), observed_at=serial.now()):
        ledger.put(state)


def reconcile(root, workspace, config, ledger, cma, workspaces, factory_stage):
    jobs = ledger.jobs()
    capacity = config.get('authoring_concurrency', 1)
    material_policy.admission(jobs, capacity)  # Validate before any provider side effect.
    ready = None
    def outcome(result):
        attention = [s['key'] for s in jobs if s['phase'] == 'needs_attention']
        waiting = [{'key': s['key'], 'stage': s['waiting_stage'], 'error': s.get('last_error')}
                   for s in jobs if s.get('waiting_stage') and s['phase'] != 'published']
        result.update(needs_attention=attention, waiting=waiting,
                      phases={phase: sum(s['phase'] == phase for s in jobs) for phase in sorted({s['phase'] for s in jobs})})
        if result['state'] == 'waiting_or_up_to_date':
            if attention: result['state'] = 'needs_attention'
            elif waiting: result['state'] = 'waiting_on_provider'
        return result
    # Original operations have priority, even after template/config changes.
    # An occupied or broken lane does not prevent observing the other lane.
    for state in jobs:
        resume_saved_build(state, config, ledger)
        if state['phase'] == 'published':
            if not state.get('cleanup_complete'):
                try: cleanup(cma,ledger,state)
                except (OSError,RuntimeError,ValueError): pass
            current=next((w for w in serial.read(root/'works.json')['works'] if w['work']==state['identity']['paper']['id']),None)
            if current and current['current_version']==state['identity']['paper']['version'] and plan_template_adaptation(state,config['template_commit']):
                ledger.put(state)
            else:
                continue
        if state['phase'] == 'needs_attention':
            continue
        if state['phase'] == 'sources_saved':
            try:
                if recover_publication(ledger,state): continue
            except (OSError,RuntimeError,ValueError) as error:
                state.update(phase='needs_attention',blocked_stage='publication_fence',reason=str(error)[:1000])
                ledger.put(state)
                continue
        if state['phase'] == 'publication_pending':
            try:
                readback(ledger, state)
            except (OSError, ValueError, KeyError):
                state['waiting_stage']='public_readback'
                ledger.put(state)
            continue
        if state['phase'] in ('intent', 'provisioning', 'authoring'):
            try:
                cma_content.reconcile(cma, state, ledger.put, isolation_workspace(config, workspaces, state['lane']))
            except (OSError, ValueError, RuntimeError) as error:
                wait_for(state, 'provider', error, ledger)
                continue
            state.pop('waiting_stage', None)
            state.pop('last_error', None)
            ledger.put(state)
        if state['phase'] == 'sources_ready':
            try: save_sources(cma, ledger, state)
            except (OSError,RuntimeError,ValueError,KeyError) as error:
                wait_for(state, 'source_retrieval', error, ledger)
                continue
        if state['phase'] == 'sources_saved':
            try: cleanup(cma,ledger,state)
            except (OSError,RuntimeError,ValueError): pass
            if ready is not None:
                continue  # One host render/publication at a time.
            work = next((w for w in serial.read(root/'works.json')['works'] if w['work'] == state['identity']['paper']['id']), None)
            try:
                require(work is not None and work['current_version'] == state['identity']['paper']['version'], 'saved output is historical; current publication needs inspection')
                work = dict(work, repository_name=state['identity']['paper']['repository'].split('/',1)[1])
                public_detail(config['api_base'], work)
                repo, run, envelope, head = prepare(root, workspace, work, config, state)
                restore_sources(ledger, state, repo, run)
                promote_content.attach_history(Path(envelope['source_repository_path']), repo, work['work'],work['current_version'], head)
            except (OSError,RuntimeError,ValueError,KeyError,subprocess.CalledProcessError) as error:
                state.update(phase='needs_attention',blocked_stage='restore_checked_inputs',reason=str(error)[:1000])
                ledger.put(state)
                continue
            serial.write(workspace/'ready.json', {'key':state['key'], 'run':str(run), 'repository':str(repo)})
            ready = {'state':'ready_for_host_qa', 'run':str(run), 'repository':str(repo)}
    # Keep remote Codex tasks busy while one finished paper renders, without
    # allowing an unbounded pile of source packages to await the host.
    slots = material_policy.admission(jobs, capacity)['slots']
    if not slots:
        return outcome(ready or {'state':'waiting_for_original_job'})
    known = {(s['identity']['paper']['id'], s['identity']['paper']['version'])
             for s in jobs if s.get('identity')}
    admitted = []
    for work in candidate_works(root):
        if len(admitted) >= slots:
            break
        if (work['work'], work['current_version']) in known:
            continue
        lane, detail = public_detail(config['api_base'], work)
        if lane not in config.get('lanes', ['collection']):
            continue
        repo, run, envelope, head = prepare(root, workspace, work, config, factory_stage=factory_stage)
        packet = cma_content.projection_packet(Path(envelope['input_projection_path']), config['api_base'])
        state = cma_content.intent(envelope, head, serial.command(['git','rev-parse','HEAD'], root), workspaces[lane], lane, packet)
        existing = ledger.get(state['key'])
        if existing:
            # Source identity, not a changed template, owns this mathematical job.
            continue
        ledger.put(state)
        jobs.append(state)
        known.add((work['work'],work['current_version']))
        try:
            cma_content.reconcile(cma, state, ledger.put, isolation_workspace(config, workspaces, lane))
        except (OSError, ValueError, RuntimeError) as error:
            wait_for(state, 'provider', error, ledger)
        admitted.append(work['work'])
    return outcome(ready or {'state':'authoring_started' if admitted else 'waiting_or_up_to_date','admitted':admitted})


def configure_container_links(repo, toolchain=Path('/opt/tbook')):
    link = repo/'node_modules'
    if not link.is_symlink():
        return
    require(link.resolve() == (toolchain/'node_modules').resolve() and link.is_dir(),
            'unexpected container dependency link')
    require(not serial.command(['git', 'ls-files', '--', 'node_modules'], repo),
            'container dependency link must not be tracked paper content')
    # The template ignores node_modules/ as a directory, which does not match
    # the symlink made by the verified toolchain image. Keep this runtime-only
    # exclusion in local Git metadata; do not edit the paper or its template.
    exclude = Path(serial.command(['git', 'rev-parse', '--git-path', 'info/exclude'], repo))
    if not exclude.is_absolute():
        exclude = repo/exclude
    existing = exclude.read_text() if exclude.exists() else ''
    if '/node_modules' not in existing.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open('a') as stream:
            stream.write('\n/node_modules\n')


def tail_bytes(path, limit):
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size-limit))
        return stream.read(limit)


def record_failed_check(run, stage, error):
    failure = {'stage': stage, 'type': type(error).__name__, 'observed_at': serial.now()}
    if isinstance(error, subprocess.CalledProcessError):
        failure.update(command=error.cmd, exit_code=error.returncode)
    elif isinstance(error, subprocess.TimeoutExpired):
        failure.update(command=error.cmd, timeout_seconds=error.timeout)
    elif isinstance(error, (ValueError, RuntimeError, OSError)):
        failure['reason'] = str(error)[:1000]
    log = run/'operator-checks.log'
    if stage == 'host_qa' and log.is_file():
        failure['output_tail'] = tail_bytes(log, 6000).decode('utf-8', errors='replace')
    checkpoint = serial.read(run/'checkpoint.json') if (run/'checkpoint.json').exists() else {}
    checkpoint['last_failure'] = failure
    serial.write(run/'checkpoint.json', checkpoint)


def build(run):
    envelope = serial.read(run/'envelope.json')
    repo = Path(envelope['repository_path'])
    result=serial.read(run/'result.json')
    require(result['visualization']['decision'] in ('built','not_useful'),'visualization assessment is still pending')
    serial.write(run/'checkpoint.json', {'state':'rebuilding_products', 'started_at':serial.now()})
    try:
        configure_container_links(repo)
        state = serial.validate_run(repo, run, build=True)
        require(state['state'] == 'local_checks_passed', 'host material checks did not pass')
    except Exception as error:
        checkpoint = serial.read(run/'checkpoint.json')
        checkpoint.update(state='needs_attention', stage='host_qa')
        serial.write(run/'checkpoint.json', checkpoint)
        record_failed_check(run, 'host_qa', error)
        raise
    serial.write(run/'checkpoint.json', state)
    return state


def record_build_failure(ledger, state, run):
    logs = {name: tail_bytes(run/name, 2*1024*1024)
            for name in ('operator-checks.log', 'checkpoint.json', 'qa/browser.json') if (run/name).is_file()}
    # Sources and previous checks stay immutable. A retry can produce different
    # diagnostics, so address each bundle by its bytes instead of replacing it.
    inventory = {name: hashlib.sha256(data).hexdigest() for name, data in logs.items()}
    prefix = 'checks/' + state['key'] + '/' + serial.digest(inventory) + '/'
    state.update(phase='needs_attention', blocked_stage='host_build_or_publication',
                 latest_checks={'prefix': prefix, 'sha256': inventory})
    checkpoint = json.loads(logs.get('checkpoint.json', b'{}'))
    if checkpoint.get('last_failure'):
        state['last_failure'] = checkpoint['last_failure']
    elif checkpoint.get('active_check'):
        state['last_failure'] = {'stage': checkpoint['active_check']['name'],
                                 'type': 'InterruptedCheck',
                                 'started_at': checkpoint['active_check']['started_at']}
    ledger.put(state, {prefix+name: data for name, data in logs.items()})


def publish_checked(root, workspace, ledger, api):
    ready = serial.read(workspace/'ready.json')
    run = Path(ready['run'])
    state = ledger.get(ready['key'])
    if recover_publication(ledger,state): return state
    require(state['phase'] == 'sources_saved', 'job is not awaiting checked publication')
    envelope = serial.read(run/'envelope.json')
    repo, source = Path(envelope['repository_path']), Path(envelope['source_repository_path'])
    serial.validate_run(repo, run, build=False)
    paper = state['identity']['paper']
    repository = paper['repository'].split('/',1)[1]
    # Optimistic publication fence protects intervening human edits.
    remote = api.state(repository)
    require(remote[1] == state.get('publication_base',state['paper_commit']), 'paper repository advanced; preserve generated sources and rebase without another model call')
    work = next(w for w in serial.read(root/'works.json')['works'] if w['work'] == paper['id'])
    public_detail(serial.read(root/'publisher.json')['api_base'], work)
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Mathesis materials','-c','user.email=contact@chrono-ai.fun',
                    'commit','--allow-empty','-m','Prepare checked exact-version paper materials'],check=True,stdout=subprocess.DEVNULL)
    authored = promote_content.tracked_tree(repo)
    original = promote_content.tracked_tree(source)
    tree = authored  # Immutable history was attached before the fixed build.
    control_state = api.state('catalog')
    control = json.loads(api.blob('catalog', control_state[3]['publisher.json']))
    control['book_works'] = sorted(set(control.get('book_works',[])) | {paper['id']})
    api.sync('catalog', {'publisher.json':(json.dumps(control,sort_keys=True,indent=2)+'\n').encode()},
             lambda p:p == 'publisher.json', 'Reserve checked authored paper from collection refresh', control_state)
    # Commit creation/ref update is one optimistic Git transaction. If its
    # receipt is lost, the exact source tree is compared on resumption.
    files = {name:value[0] for name,value in tree.items()}
    modes = {name:value[1] for name,value in tree.items()}
    state['build_receipt']={'checkpoint':serial.read(run/'checkpoint.json'), 'source_inventory_sha256':serial.digest(serial.source_inventory(repo)), 'toolchain_image':serial.read(root/'production.json')['toolchain_image']}
    state.update(publication_intent={'source_tree':{name:{'sha':publish.sha(data),'mode':modes[name]} for name,data in files.items()},
                                    'manifest':serial.read(repo/'_site/mathesis-materials.json')})
    ledger.put(state)
    api.sync(repository, files, lambda p:p in set(original)|set(tree), 'Publish checked bilingual paper materials', remote, modes)
    state.update(phase='publication_pending', published_commit=api.receipts[repository]['after'], expected_manifest=state['publication_intent']['manifest'])
    ledger.put(state)
    return state


def readback(ledger, state, fetch=verify_factory.get, probe=verify_factory.prefix):
    manifest = state['expected_manifest']
    base = manifest['site_url']
    require(base == 'https://papers.trureturing.org/' + state['identity']['paper']['repository'].split('/',1)[1] + '/', 'unexpected public material site')
    repository=state['identity']['paper']['repository']
    query=urllib.parse.urlencode({'head_sha':state['published_commit'],'per_page':100})
    runs=json.loads(fetch('https://api.github.com/repos/'+repository+'/actions/runs?'+query,4*1024*1024))
    deployed=next((r for r in runs.get('workflow_runs',[]) if r.get('path') == '.github/workflows/pages.yml'
                   and r.get('head_sha') == state['published_commit'] and r.get('status') == 'completed' and r.get('conclusion') == 'success'),None)
    if deployed is None: return False
    current = json.loads(fetch(base+'mathesis-materials.json',1024*1024))
    if current != manifest:
        return False
    serial.check_material_products(current, state['visualization'])
    for url in current.get('reading',{}).values():
        require(url.startswith(base) and b'<html' in fetch(url,8*1024*1024).lower(), 'reading readback failed')
    for slide in current.get('slides',[]):
        for field in ('pdf_url','notes_url'):
            require(slide[field].startswith(base) and probe(slide[field],5).startswith(b'%PDF-'), 'slide readback failed')
    for video in current.get('videos',[]):
        require(video['mp4_url'].startswith(base) and probe(video['mp4_url'],32)[4:8] == b'ftyp', 'film readback failed')
    for visual in current.get('visualizations',[]):
        require(visual['url'].startswith(base) and b'<html' in fetch(visual['url'],8*1024*1024).lower(), 'visualization readback failed')
    state['pages_receipt']={'run_id':deployed['id'],'head_sha':deployed['head_sha'],'conclusion':'success'}
    state.update(phase='published', last_published_template=(state.get('template_adaptation') or {}).get('template_commit',state['identity']['template_commit']), published_at=serial.now(), products={'story':'published','slides':'published', 'video':'published' if current.get('videos') else 'pending', 'visualization':state['visualization']['decision']})
    ledger.put(state)
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan','probe','reconcile','build','publish','attention'])
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--run',type=Path)
    parser.add_argument('--factory-stage',type=Path)
    args=parser.parse_args()
    root,workspace=args.root.resolve(),args.workspace.resolve()
    workspace.mkdir(parents=True,exist_ok=True)
    if args.action == 'build':
        require(args.run is not None,'build requires an existing run')
        result=build(args.run.resolve())
    elif args.action == 'plan':
        result={'schema':'mathesis.hosted-material-plan.v1','candidates':[{'work':w['work'],'version':w['current_version']} for w in candidate_works(root)],'provider_calls':0}
    else:
        config=serial.read(root/'production.json')
        require(args.action == 'probe' or config['enabled'] is True,'hosted material production is disabled')
        api=publish.API(os.environ['PUBLISHER_TOKEN'])
        ledger=Ledger(api,config['state_repository'])
        if args.action == 'probe':
            cma=cma_content.CMA(os.environ['CMA_TOKEN'])
            workspaces=material_workspaces(os.environ)
            for lane in config.get('lanes', ['collection']):
                sandbox=cma.facts(workspaces[lane])
                other=isolation_workspace(config,workspaces,lane)
                if other: require(cma.facts(other) != sandbox,'material lanes resolve to one sandbox')
            result={'state':'setup_ready'}
        elif args.action == 'attention':
            ready=serial.read(workspace/'ready.json')
            state=ledger.get(ready['key'])
            record_build_failure(ledger, state, Path(ready['run']))
            result=state
        elif args.action == 'publish':
            try:
                result=publish_checked(root,workspace,ledger,api)
            except Exception as error:
                ready=serial.read(workspace/'ready.json')
                record_failed_check(Path(ready['run']), 'publication', error)
                raise
        else:
            cma=cma_content.CMA(os.environ['CMA_TOKEN'])
            workspaces=material_workspaces(os.environ)
            result=reconcile(root,workspace,config,ledger,cma,workspaces,args.factory_stage.resolve() if args.factory_stage else None)
            if result.get('state') == 'ready_for_host_qa' and os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'],'a') as output:
                    output.write('ready=true\nrun='+result['run']+'\nrepository='+result['repository']+'\n')
    # Keep opaque operation handles and full checkpoints in the private ledger.
    status = {k: result[k] for k in ('state', 'phases', 'needs_attention', 'waiting', 'admitted') if k in result}
    status.setdefault('state', result.get('phase'))
    serial.write(workspace/'status.json', status)
    if os.environ.get('GITHUB_STEP_SUMMARY') and args.action == 'reconcile':
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('\n### Paper material processing\n\n```json\n' + json.dumps(status, indent=2) + '\n```\n')
    print(json.dumps(result if args.action == 'plan' else status,ensure_ascii=False))


if __name__ == '__main__': main()
