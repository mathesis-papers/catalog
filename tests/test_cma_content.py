import copy
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cma_content as cma
import hosted_materials
import serial_content as serial


def envelope(template='a' * 40):
    return {'schema':'mathesis.paper-production-envelope.v1', 'do_not_rereview':True, 'publication_authorized':False,
            'identity':{'paper':{'id':'example-note', 'repository':'mathesis-papers/example-note', 'version':1, 'title':'A bound'},
                        'source_hashes':{'paper.json':'c' * 64}, 'template_commit':template, 'skill_sha256':'d' * 64,
                        'allow_request_registration':False}}


class FakeCMA:
    def __init__(self):
        self.calls = []
        self.posts = 0
        self.lose_receipt = False
        self.fail = False
        self.same = False
    def facts(self, workspace):
        return 'sbx_same' if self.same else 'sbx_' + workspace
    def call(self, method, path, body=None, key=None):
        self.calls.append((method, path, copy.deepcopy(body), key))
        if method == 'POST':
            self.posts += 1
            if self.lose_receipt:
                self.lose_receipt = False
                raise TimeoutError('uncertain create')
            return {'id':'operation_original'}
        if 'agent-lifecycle' in path:
            return {'status':'failed' if self.fail else 'succeeded', 'agent_id':'agent_original'}
        return {'status':'ready', 'sandbox':{'id':'sbx_wks_internal'}, 'first_message':{'state':'completed'},
                'worktree':{'path':'/home/cma/workspace/.worktrees/wt-186'}}


class EndpointTests(unittest.TestCase):
    def test_source_retrieval_and_cleanup_keep_each_jobs_original_broker(self):
        for origin in (None, cma.CMA_ORIGIN):
            with self.subTest(origin=origin):
                state = {'key': 'a' * 64, 'agent_id': 'original-agent', 'identity': {},
                         'worktree_path': '/home/cma/workspace/.worktrees/wt-186'}
                if origin:
                    state['cma_origin'] = origin
                visualization = {'decision': 'not_useful', 'reason': 'A written example suffices.'}
                pack = b'Preserved source archive'
                files = {'sources.tar.gz': pack, 'handoff.json': json.dumps({
                    'identity': state['identity'], 'archive_sha256': hashlib.sha256(pack).hexdigest(),
                    'result': {'visualization': visualization}}).encode()}
                seen = []
                def call(provider, method, path, body=None, key=None, missing=False):
                    seen.append((provider.base, method))
                    if '/files/content?' in path:
                        name = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['path'][0].rsplit('/', 1)[1]
                        raw = files[name]
                        return {'content_base64': base64.b64encode(raw).decode(), 'size_bytes': len(raw),
                                'sha256': hashlib.sha256(raw).hexdigest()}
                    return {'status': 'deleted'}
                class Ledger:
                    def put(self, state, files=None):
                        if files is not None:
                            self.files = files
                ledger = Ledger()
                provider = cma.CMA('synthetic-test-key')
                with patch.object(cma.CMA, 'call', autospec=True, side_effect=call):
                    hosted_materials.save_sources(provider, ledger, state)
                    hosted_materials.cleanup(provider, ledger, state)
                self.assertEqual(state['phase'], 'sources_saved')
                self.assertTrue(state['cleanup_complete'])
                self.assertEqual(ledger.files['sources/' + state['key'] + '/sources.tar.gz'], pack)
                self.assertEqual(seen, [(origin or cma.LEGACY_CMA_PROXY, 'GET')] * 3)

    def test_public_cma_origin_is_default_and_original_commands_and_auth_are_preserved(self):
        provider = cma.CMA('test-only-credential')
        with patch.object(provider.opener, 'open', return_value=io.BytesIO(b'{"id":"original-operation"}')) as send:
            body = {'name': 'Existing task', 'workspace_id': 'wks_internal', 'initial_message': 'Exact admitted input'}
            self.assertEqual(provider.call('POST', '/api/v1/agents', body, 'original-key'), {'id':'original-operation'})
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'https://cma.nyxid.ai/api/v1/agents')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-credential')
        self.assertEqual(request.get_header('Idempotency-key'), 'original-key')
        self.assertEqual(json.loads(request.data), body)
        self.assertTrue(any(isinstance(h, cma.NoRedirect) for h in provider.opener.handlers))

    def test_saved_jobs_keep_the_original_broker_and_new_jobs_pin_the_cma_origin(self):
        provider = cma.CMA('test')
        old = {'phase':'intent','idempotency_key':'original-key','request':{'workspace_id':'original-workspace'}}
        before = copy.deepcopy(old)
        legacy = provider.for_job(old)
        self.assertEqual(legacy.base, cma.LEGACY_CMA_PROXY)
        self.assertEqual(old, before)
        state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        self.assertEqual(state['cma_origin'], cma.CMA_ORIGIN)
        self.assertIs(provider.for_job(state), provider)
        self.assertEqual(state['request']['workspace_id'], 'wks_internal')

    def test_endpoint_selection_refuses_credential_and_origin_escapes(self):
        for origin in ['https://cma.nyxid.ai','https://cma.nyxid.ai/', 'https://api.nyxid.ai/api/v1/proxy/s/cma', 'http://127.0.0.1:8765']:
            with self.subTest(origin=origin): cma.CMA('test', origin)
        for origin in ['http://cma.nyxid.ai','https://cma.nyxid.ai.evil.test','https://user:secret@cma.nyxid.ai','https://cma.nyxid.ai:444','https://cma.nyxid.ai/other','https://cma.nyxid.ai?token=secret','https://cma.nyxid.ai/#part','https://evil.test','http://localhost/other']:
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(ValueError,'unexpected CMA endpoint'): cma.CMA('test', origin)


class AdapterTests(unittest.TestCase):
    def test_long_work_id_uses_a_bounded_distinguishable_provider_name(self):
        e = envelope()
        e['identity']['paper']['id'] = 'delgado-suer-yesil-2026-symmetrization-surjectivity'
        state = cma.intent(e, 'b'*40, 'e'*40, 'wks_internal', 'collection')
        self.assertLessEqual(len(state['request']['name']), 64)
        self.assertLessEqual(len(state['request']['name'].encode()), 80)
        self.assertTrue(state['request']['name'].endswith(state['key'][:12]))

    def test_definitively_rejected_legacy_name_is_corrected_once_without_changing_the_task(self):
        e = envelope()
        work = 'delgado-suer-yesil-2026-symmetrization-surjectivity'
        e['identity']['paper']['id'] = work
        state = cma.intent(e, 'b'*40, 'e'*40, 'wks_internal', 'collection')
        state['request']['name'] = 'Mathesis paper materials ' + work
        original = copy.deepcopy(state)
        saved = []
        provider = FakeCMA()
        call = provider.call
        def reject(method, path, body=None, key=None):
            if method == 'POST' and len(body['name']) > 64:
                raise cma.ProviderError(method, path, 422, 'invalid_request', 'name')
            return call(method, path, body, key)
        provider.call = reject
        with self.assertRaises(cma.ProviderError):
            cma.reconcile(provider, state, lambda s: saved.append(copy.deepcopy(s)))
        self.assertEqual(state['phase'], 'intent')
        self.assertEqual(state['request']['initial_message'], original['request']['initial_message'])
        self.assertEqual(state['identity'], original['identity'])
        self.assertEqual(state['admission_rejections'][0]['request'], original['request'])
        self.assertEqual(saved[-1]['idempotency_key'], original['idempotency_key'] + ':name-v2')
        cma.reconcile(provider, state, lambda s: saved.append(copy.deepcopy(s)))
        self.assertEqual(state['phase'], 'sources_ready')
        self.assertEqual(provider.posts, 1)

    def test_unknown_or_uncertain_refusals_never_change_the_original_request(self):
        for error in (TimeoutError('uncertain'), cma.ProviderError('POST', '/api/v1/agents', 422, 'invalid_request')):
            with self.subTest(error=type(error).__name__):
                state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
                state['request']['name'] = 'long-name-' * 9
                original = copy.deepcopy(state)
                provider = FakeCMA()
                provider.call = lambda *args: (_ for _ in ()).throw(error)
                with self.assertRaises(type(error)):
                    cma.reconcile(provider, state, lambda s: None)
                self.assertEqual(state['request'], original['request'])
                self.assertEqual(state['idempotency_key'], original['idempotency_key'])

    def test_problem_observation_does_not_include_arbitrary_provider_content(self):
        error = cma.provider_error('POST', '/api/v1/agents', 422, json.dumps({
            'code': 'invalid_request', 'detail': 'secret request text', 'token': 'do not retain'}).encode())
        self.assertEqual(error.observation(), {'status': 422, 'code': 'invalid_request', 'field': None})
        self.assertNotIn('secret', str(error))
        self.assertIsNone(cma.provider_error('GET', '/api/v1/agents', 503, b'not JSON').code)

    def generated_index_case(self, root):
        repo, run = root/'repo', root/'run'
        repo.mkdir(); run.mkdir()
        marker = b'\n<!-- Mathesis generated repository guide; refresh with paper.py front. -->\n'
        readme = b'Frozen catalog README\n' + marker + b'Skeleton guide\n'
        lock = {'schema':'trureturing-books.lock.v1', 'repo':'the-omega-institute/trureturing',
                'branch':'dev', 'commit':'a'*40, 'pinned_at':'2026-11-08T12:32:04Z', 'declarations':{}}
        initial = {'paper.json':b'original paper', 'README.md':readme,
                   'trureturing.lock.json':json.dumps(lock).encode(), '.mathesis/book.json':b'{"book":"example-note"}'}
        for name, data in initial.items():
            path=repo/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        serial.command(['git','init','-q',str(repo)])
        serial.command(['git','add','.'],repo)
        resolved = copy.deepcopy(lock)
        resolved['declarations']={'Module.result':{'kind':'theorem','statement_id':'sha256:'+'f'*64}}
        expected = {'README.md':readme.replace(b'Skeleton guide',b'Result guide'),
                    'trureturing.lock.json':json.dumps(resolved).encode()}
        files = dict(initial, **expected)
        files['.mathesis/review.json']=b'{"results":{"result":{"comparison_verdict":"same"}}}'
        state=cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        state.update(phase='sources_ready',agent_id='agent',worktree_path='/home/cma/workspace/.worktrees/wt-186')
        return repo,run,state,files,expected

    def handoff_files(self, state, files):
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w:gz') as archive:
            for name,data in files.items():
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
                archive.addfile(info,io.BytesIO(data))
        pack=stream.getvalue()
        result={'status':'prepared','summary':'source prepared',
                'visualization':{'decision':'not_useful','reason':'Static examples explain the proof'},
                'outputs':['.mathesis/review.json'],'checks':[],'requests':[], 'doi_observation':'unchanged','remaining':[]}
        handoff={'schema':'mathesis.cma-content-handoff.v1','identity':state['identity'],'result':result,
                 'archive_sha256':hashlib.sha256(pack).hexdigest(),
                 'inventory':{name:{'sha256':hashlib.sha256(data).hexdigest(),'mode':'100644'} for name,data in files.items()}}
        class Files:
            def file(self,agent,path):return json.dumps(handoff).encode() if path.endswith('handoff.json') else pack
        return Files()

    def test_normal_authoring_indexes_are_regenerated_without_changing_frozen_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo,run,state,files,expected=self.generated_index_case(Path(tmp))
            original_run=cma.subprocess.run
            generated=[]
            def generate(command,**kwargs):
                if command[0]!='./tbook' and command[-1]!='front':return original_run(command,**kwargs)
                generated.append(command)
                name='trureturing.lock.json' if command==['./tbook','pin','--refresh'] else 'README.md'
                (repo/name).write_bytes(expected[name])
            with patch.object(cma.subprocess,'run',side_effect=generate):
                cma.import_sources(self.handoff_files(state,files),state,repo,run)
            self.assertEqual(len(generated),2)
            self.assertEqual((repo/'paper.json').read_bytes(),b'original paper')
            self.assertEqual((repo/'.mathesis/review.json').read_bytes(),files['.mathesis/review.json'])
            for name,data in expected.items():self.assertEqual((repo/name).read_bytes(),data)

    def test_forged_declaration_metadata_must_match_independent_pin_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo,run,state,files,expected=self.generated_index_case(Path(tmp))
            forged=json.loads(files['trureturing.lock.json']);forged['declarations']['Module.result']['statement_id']='sha256:'+'0'*64
            files['trureturing.lock.json']=json.dumps(forged).encode()
            original_run=cma.subprocess.run
            def generate(command,**kwargs):
                if command[0]!='./tbook' and command[-1]!='front':return original_run(command,**kwargs)
                name='trureturing.lock.json' if command==['./tbook','pin','--refresh'] else 'README.md'
                (repo/name).write_bytes(expected[name])
            with patch.object(cma.subprocess,'run',side_effect=generate):
                with self.assertRaisesRegex(ValueError,'installed generator'):
                    cma.import_sources(self.handoff_files(state,files),state,repo,run)

    def test_generated_indexes_cannot_change_the_source_prefix_or_library_revision(self):
        for target in ('README.md','trureturing.lock.json'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as tmp:
                repo,run,state,files,expected=self.generated_index_case(Path(tmp))
                if target=='README.md':files[target]=files[target].replace(b'Frozen catalog',b'Changed catalog')
                else:
                    lock=json.loads(files[target]);lock['commit']='0'*40;files[target]=json.dumps(lock).encode()
                with patch.object(cma.subprocess,'run',wraps=cma.subprocess.run) as generator:
                    with self.assertRaises(ValueError):cma.import_sources(self.handoff_files(state,files),state,repo,run)
                    self.assertFalse(any(call.args[0][0]=='./tbook' or call.args[0][-1]=='front' for call in generator.call_args_list))
                self.assertFalse((repo/'.mathesis/review.json').exists())

    def test_operator_pin_upgrade_is_exact_and_still_replays_the_installed_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo,run,state,files,expected=self.generated_index_case(Path(tmp))
            marker=json.dumps({'sha256':{'paper.json':hashlib.sha256(files['paper.json']).hexdigest()}}).encode()
            (repo/'.mathesis/generated.json').write_bytes(marker)
            files['.mathesis/generated.json']=marker
            lock=json.loads(files['trureturing.lock.json'])
            lock.update(commit='b'*40,pinned_at='2026-11-23T00:00:00Z')
            files['trureturing.lock.json']=json.dumps(lock).encode()
            expected['trureturing.lock.json']=files['trureturing.lock.json']
            approval={'from_commit':'a'*40,'to_commit':'b'*40,
                      'lock_sha256':hashlib.sha256(files['trureturing.lock.json']).hexdigest()}
            provider=self.handoff_files(state,files)
            original=(repo/'trureturing.lock.json').read_bytes()
            for operator,approved in [(False,approval),(True,dict(approval,lock_sha256='0'*64)),
                                      (True,dict(approval,from_commit='c'*40))]:
                with self.assertRaisesRegex(ValueError,'exact operator approval'):
                    cma.import_sources(provider,state,repo,run,approved_operator_handoff=operator,
                                       approved_formalization_pin=approved)
                self.assertEqual((repo/'trureturing.lock.json').read_bytes(),original)
                self.assertFalse((repo/'.mathesis/review.json').exists())
            original_run=cma.subprocess.run
            resolutions=[]
            def generate(command,**kwargs):
                if command[0]!='./tbook' and command[-1]!='front':return original_run(command,**kwargs)
                name='trureturing.lock.json' if command==['./tbook','pin','--refresh'] else 'README.md'
                if name=='trureturing.lock.json':
                    self.assertEqual(json.loads((repo/name).read_bytes())['commit'],'b'*40)
                    resolutions.append(command)
                (repo/name).write_bytes(expected[name])
            with patch.object(cma.subprocess,'run',side_effect=generate):
                cma.import_sources(provider,state,repo,run,approved_operator_handoff=True,
                                   approved_formalization_pin=approval)
            self.assertEqual(resolutions,[['./tbook','pin','--refresh']])
            self.assertEqual((repo/'paper.json').read_bytes(),files['paper.json'])

    def test_uncertain_admission_replays_original_intent_and_never_regenerates_after_success(self):
        state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        saved = []
        save = lambda s: saved.append(copy.deepcopy(s))
        provider = FakeCMA()
        provider.lose_receipt = True
        with self.assertRaises(TimeoutError): cma.reconcile(provider, state, save, 'wks_external')
        self.assertEqual(saved[-1]['phase'], 'intent')
        resumed = copy.deepcopy(saved[-1])
        cma.reconcile(provider, resumed, save, 'wks_external')
        posts = [c for c in provider.calls if c[0] == 'POST']
        self.assertEqual(posts[0], posts[1])
        self.assertEqual(resumed['phase'], 'sources_ready')
        cma.reconcile(provider, resumed, save, 'wks_external')
        self.assertEqual(provider.posts, 2)  # same provider key, one admitted operation
        self.assertEqual(resumed['agent_id'], 'agent_original')
        self.assertEqual(resumed['worktree_path'], '/home/cma/workspace/.worktrees/wt-186')
        updated = cma.intent(envelope('f'*40), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        self.assertEqual(updated['key'], state['key'])
        self.assertNotEqual(updated['identity']['template_commit'], state['identity']['template_commit'])

    def test_failed_provisioning_remains_parked_and_same_sandbox_is_refused(self):
        state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        provider = FakeCMA(); provider.same = True
        with self.assertRaisesRegex(ValueError, 'distinct'): cma.reconcile(provider, state, lambda s: None, 'wks_external')
        self.assertEqual(provider.posts, 0)
        provider.same = False; provider.fail = True
        cma.reconcile(provider, state, lambda s: None, 'wks_external')
        self.assertEqual(state['phase'], 'needs_attention')
        cma.reconcile(provider, state, lambda s: None, 'wks_external')
        self.assertEqual(provider.posts, 1)

    def test_serial_shared_admission_uses_existing_workspace_without_allocating_a_sandbox(self):
        state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        provider=FakeCMA();provider.same=True
        provider.call_original=provider.call
        def call(method,path,body=None,key=None):
            row=provider.call_original(method,path,body,key)
            if method=='GET' and '/agents/' in path:row['sandbox']['id']='sbx_same'
            return row
        provider.call=call
        cma.reconcile(provider,state,lambda s:None)
        self.assertEqual(state['phase'],'sources_ready')
        self.assertEqual(provider.posts,1)
        self.assertEqual(provider.calls[0][2]['workspace_id'],'wks_internal')
        self.assertFalse(any('/sandboxes' in path for _,path,_,_ in provider.calls))

    def test_request_registration_is_explicit_and_bound_to_the_original_envelope(self):
        allowed=envelope();allowed['identity']['allow_request_registration']=True
        state=cma.intent(allowed,'b'*40,'e'*40,'wks_internal','collection')
        self.assertIn('Register only deduplicated formalization requests',state['request']['initial_message'])
        code=state['request']['initial_message'].split('```python\n')[1].split('```')[0]
        self.assertIn("spec['identity']['allow_request_registration']",code)
        denied=cma.intent(envelope(),'b'*40,'e'*40,'wks_internal','collection')
        self.assertIn('Do not register requests.',denied['request']['initial_message'])

    def test_bootstrap_is_valid_python_and_contains_no_publisher_authority(self):
        prompt = cma.bootstrap(envelope()['identity'], 'b'*40, 'e'*40, 'c'*64)
        code = prompt.split('```python\n')[1].split('```')[0]
        compile(code, 'bootstrap', 'exec')
        self.assertIn('allow_request_registration', code)
        self.assertNotIn('PUBLISHER_TOKEN', code)
        self.assertNotIn('gh auth', code)
        self.assertIn('git', code)

    def test_bad_scope_and_paths_are_rejected(self):
        e = envelope(); e['publication_authorized'] = True
        with self.assertRaises(ValueError): cma.intent(e, 'b'*40, 'e'*40, 'wks_internal', 'collection')
        for name in ('../secret', '/tmp/file', '.git/config', 'x\\y', 'x/../y', './x'):
            with self.assertRaises(ValueError): cma.safe_path(name)

    def test_handoff_is_read_from_provider_worktree_under_retained_home(self):
        state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        cma.reconcile(FakeCMA(), state, lambda s: None, 'wks_external')
        pack = b'actual source pack'
        handoff = json.dumps({'identity':state['identity'], 'archive_sha256':hashlib.sha256(pack).hexdigest(),
                              'result':{'visualization':{'decision':'not_useful','reason':'A short proof suffices'}}}).encode()
        prefix = 'cma/workspace/.worktrees/wt-186/mathesis-content/' + state['key'] + '/'
        files = {prefix+'handoff.json':handoff, prefix+'sources.tar.gz':pack}
        class Provider:
            def file(self, agent, path):
                self_agent = 'agent_original'
                if agent != self_agent or path not in files:
                    raise FileNotFoundError('File API paths are relative to /home')
                return files[path]
        class Ledger:
            def put(self, value, files):
                self.saved = copy.deepcopy(value), files
        ledger = Ledger()
        hosted_materials.save_sources(Provider(), ledger, state)
        self.assertEqual(state['phase'], 'sources_saved')
        self.assertIn(pack, ledger.saved[1].values())
        for path in (None, '/home', '/etc', '/home/cma/../other', '/home//cma', '/home/cma/.git'):
            with self.assertRaises(ValueError): cma.artifact_prefix(dict(state, worktree_path=path))

    def test_resuming_authoring_rejects_a_changed_provider_worktree(self):
        state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        state.update(phase='authoring', agent_id='agent_original', sandbox='sbx_wks_internal',
                     worktree_path='/home/cma/workspace/.worktrees/another-job')
        with self.assertRaisesRegex(ValueError, 'worktree identity'):
            cma.reconcile(FakeCMA(), state, lambda s: None, 'wks_external')
        self.assertEqual(state['phase'], 'authoring')

    def test_completed_original_job_can_bind_its_worktree_without_recreating(self):
        state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
        state.update(phase='sources_ready', agent_id='agent_original', sandbox='sbx_wks_internal')
        provider = FakeCMA()
        cma.reconcile(provider, state, lambda s: None, 'wks_external')
        self.assertEqual(provider.posts, 0)
        self.assertEqual(state['phase'], 'sources_ready')
        self.assertEqual(state['worktree_path'], '/home/cma/workspace/.worktrees/wt-186')

    def test_import_rejects_template_change_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root/'repo'; run = root/'run'; repo.mkdir(); run.mkdir()
            serial.command(['git','init','-q',str(repo)])
            (repo/'paper.json').write_text('original')
            (repo/'tools').mkdir(); (repo/'tools/build.py').write_text('original build')
            serial.command(['git','add','.'], repo)
            files = {'paper.json':b'original', 'tools/build.py':b'changed build'}
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w:gz') as archive:
                for name, content in files.items():
                    info = tarfile.TarInfo(name); info.size=len(content); info.mode=0o644
                    archive.addfile(info, io.BytesIO(content))
            content = data.getvalue()
            handoff = {'schema':'mathesis.cma-content-handoff.v1', 'identity':envelope()['identity'],
                       'inventory':{n:{'sha256':hashlib.sha256(d).hexdigest(),'mode':'100644'} for n,d in files.items()},
                       'archive_sha256':hashlib.sha256(content).hexdigest(), 'result':{'status':'prepared'}}
            class Files:
                def file(self, agent, path): return json.dumps(handoff).encode() if path.endswith('.json') else content
            state = cma.intent(envelope(), 'b'*40, 'e'*40, 'wks_internal', 'collection')
            state.update(phase='sources_ready',agent_id='agent',worktree_path='/home/cma/workspace/.worktrees/wt-186')
            with self.assertRaisesRegex(ValueError, 'managed template'): cma.import_sources(Files(), state, repo, run)
            self.assertEqual((repo/'tools/build.py').read_text(), 'original build')
