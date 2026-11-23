import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hosted_materials as hosted
import promote_content as promote
import serial_content as serial


class Ledger:
    def __init__(self, jobs=()): self.rows=copy.deepcopy(list(jobs)); self.writes=[]
    def put(self,state,files=None): self.writes.append(copy.deepcopy(state))
    def jobs(self): return self.rows


class HostedTests(unittest.TestCase):
    def test_verified_container_symlink_is_local_output_without_changing_template(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo, toolchain = root/'paper', root/'toolchain'
            repo.mkdir()
            (toolchain/'node_modules').mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo/'.gitignore').write_text('/node_modules/\n')
            (repo/'node_modules').symlink_to(toolchain/'node_modules', target_is_directory=True)
            def ignored():
                return subprocess.run(['git', 'check-ignore', '-q', 'node_modules'], cwd=repo).returncode == 0
            self.assertFalse(ignored())
            hosted.configure_container_links(repo, toolchain)
            self.assertTrue(ignored())
            exclude = repo/'.git/info/exclude'
            first = exclude.read_bytes()
            hosted.configure_container_links(repo, toolchain)
            self.assertEqual(exclude.read_bytes(), first)
            self.assertEqual((repo/'.gitignore').read_text(), '/node_modules/\n')
            subprocess.run(['git', 'add', '-f', 'node_modules'], cwd=repo, check=True)
            with self.assertRaisesRegex(ValueError, 'tracked paper content'):
                hosted.configure_container_links(repo, toolchain)

    def test_unexpected_dependency_link_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            (repo/'node_modules').symlink_to(repo, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'unexpected container'):
                hosted.configure_container_links(repo)
            self.assertFalse((repo/'.git/info/exclude').exists())

    def test_existing_workspace_configuration_does_not_reserve_an_external_machine(self):
        self.assertEqual(hosted.material_workspaces({'CMA_EXTERNAL_WORKSPACE':'existing'}),
                         {'collection':'existing','external':'existing'})
        self.assertEqual(hosted.material_workspaces({'CMA_COLLECTION_WORKSPACE':'own'}),
                         {'collection':'own','external':'own'})
        self.assertIsNone(hosted.isolation_workspace({}, {'collection':'same','external':'same'}, 'collection'))
        with self.assertRaisesRegex(ValueError,'existing Mathesis'):
            hosted.material_workspaces({})

    def test_validated_operator_handoff_is_reused_without_another_worker_or_old_request_drafts(self):
        pack=b'checked source including actual canonical request links'
        handoff=b'checked source receipt'
        key='a'*64;prefix='validated/'+key+'/approved/'
        manifest=json.dumps({'key':key,'files':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
                                             for name,data in [('sources.tar.gz',pack),('handoff.json',handoff)]}}).encode()
        state={'phase':'sources_saved','key':key,'agent_id':'deleted-original-agent',
               'validated_handoff':{'prefix':prefix,'manifest_sha256':hashlib.sha256(manifest).hexdigest()}}
        files={prefix+'archive-manifest.json':manifest,prefix+'sources.tar.gz':pack,prefix+'handoff.json':handoff}
        class SavedLedger:
            def get_bytes(self,path):return files[path]
        def restore(provider,job,repo,run,**options):
            self.assertTrue(options['approved_operator_handoff'])
            self.assertEqual(job['phase'],'sources_ready')
            self.assertEqual(provider.file(job['agent_id'],'original/handoff.json'),handoff)
            return provider.file(job['agent_id'],'original/sources.tar.gz')
        with patch.object(hosted.cma_content,'import_sources',side_effect=restore):
            self.assertEqual(hosted.restore_sources(SavedLedger(),state,Path('repo'),Path('run')),pack)
            files[prefix+'sources.tar.gz']=b'changed checked source'
            with self.assertRaisesRegex(ValueError,'artifact differs'):
                hosted.restore_sources(SavedLedger(),state,Path('repo'),Path('run'))

    def test_cleanup_persists_and_reuses_its_key_then_observes_the_original_deletion(self):
        state={'key':'a'*64,'agent_id':'original-agent','source_archive_sha256':'b'*64}
        ledger=Ledger()
        class Provider:
            def __init__(self):self.deletes=[];self.lose_receipt=True;self.deleted=False;self.stopped=False;self.stops=[]
            def call(self,method,path,body=None,key=None,missing=False):
                if method=='POST':
                    assert path.endswith('/stop') and body=={}
                    assert key and ledger.writes[-1]['cleanup_stop_key']==key
                    self.stops.append(key);self.stopped=True
                    return {}
                if method=='DELETE':
                    assert self.stopped
                    self.deletes.append(key)
                    assert key and ledger.writes[-1]['cleanup_idempotency_key']==key
                    if self.lose_receipt:
                        self.lose_receipt=False
                        raise TimeoutError('uncertain deletion receipt')
                    return {'id':'original-cleanup-operation'}
                return {'status':'deleted' if self.deleted else 'stopped' if self.stopped else 'ready'}
        provider=Provider()
        with self.assertRaises(TimeoutError):hosted.cleanup(provider,ledger,state)
        hosted.cleanup(provider,ledger,state)
        self.assertEqual(provider.deletes,[state['cleanup_idempotency_key']]*2)
        self.assertEqual(state['cleanup_receipt_id'],'original-cleanup-operation')
        self.assertEqual(provider.stops,[state['cleanup_stop_key']])
        hosted.cleanup(provider,ledger,state)
        self.assertEqual(len(provider.deletes),2)
        provider.deleted=True
        hosted.cleanup(provider,ledger,state)
        self.assertTrue(state['cleanup_complete'])

    def test_cleanup_waits_for_the_acknowledged_stop_before_deleting(self):
        state={'key':'a'*64,'agent_id':'original-agent','source_archive_sha256':'b'*64}
        ledger=Ledger()
        class Provider:
            def __init__(self):self.status='ready';self.stops=0;self.deletes=0
            def call(self,method,path,body=None,key=None,missing=False):
                if method=='POST':self.stops+=1;return {}
                if method=='DELETE':
                    assert self.status=='stopped'
                    self.deletes+=1;self.status='deleted';return {}
                return {'status':self.status}
        provider=Provider()
        hosted.cleanup(provider,ledger,state)
        hosted.cleanup(provider,ledger,state)
        self.assertEqual((provider.stops,provider.deletes),(1,0))
        provider.status='stopped'
        hosted.cleanup(provider,ledger,state)
        self.assertEqual((provider.stops,provider.deletes),(1,1))
        self.assertTrue(state['cleanup_complete'])

    def test_cleanup_never_deletes_the_only_copy_of_work(self):
        class Provider:
            def call(self,*args,**kwargs):raise AssertionError('source was not preserved')
        with self.assertRaisesRegex(ValueError,'only copy'):
            hosted.cleanup(Provider(),Ledger(),{'key':'a'*64,'agent_id':'original-agent'})

    def test_private_ledger_required(self):
        class API:
            def repo(self,name): return {'private':False}
        with self.assertRaisesRegex(ValueError,'private'): hosted.Ledger(API(),'production-state')

    def test_external_input_requires_public_archive_consent(self):
        work={'publication':'public','license':'CC-BY-4.0','work':'WP-2026-0001','record':'WP-2026-0001','current_version':2}
        detail={'summary':{'archive_consent':False},'publication':{'version':2},'archive':{'source':{}}}
        def fetch(url,limit):
            if 'collected-papers' in url: raise urllib.error.HTTPError(url,404,'',{},None)
            return json.dumps(detail).encode()
        with self.assertRaisesRegex(ValueError,'archive-eligible'): hosted.public_detail('https://mathesis-omega-research.apps.chrono-ai.fun',work,fetch)
        detail['summary']['archive_consent']=True
        self.assertEqual(hosted.public_detail('https://mathesis-omega-research.apps.chrono-ai.fun',work,fetch)[0],'external')

    def test_publication_uncertain_receipt_resolves_original_exact_tree(self):
        entries={'paper.json':{'sha':'a'*40,'mode':'100644'}}
        class API:
            def state(self,name): return ('main','b'*40,'c'*40,entries)
        ledger=Ledger();ledger.api=API()
        state={'identity':{'paper':{'repository':'mathesis-papers/example'}},'paper_commit':'d'*40,
               'publication_intent':{'source_tree':entries,'manifest':{'work':'example'}}}
        self.assertTrue(hosted.recover_publication(ledger,state))
        self.assertEqual(state['published_commit'],'b'*40)
        self.assertEqual(state['phase'],'publication_pending')
        entries['human.md']={'sha':'e'*40,'mode':'100644'}
        # Copy the immutable intention before testing an unrelated new edit.
        state['publication_intent']['source_tree']={'paper.json':{'sha':'a'*40,'mode':'100644'}}
        with self.assertRaisesRegex(ValueError,'advanced'): hosted.recover_publication(ledger,state)

    def test_canonical_readback_keeps_missing_film_pending_and_accepts_no_visualization(self):
        base='https://papers.trureturing.org/example/'
        manifest={'schema':'mathesis.materials.v1','work':'example','version':1,'site_url':base,
                  'reading':{'en':base+'en/','zh':base+'zh/'},'slides':[
                      {'language':lang,'pdf_url':base+lang+'.pdf','notes_url':base+lang+'-notes.pdf'} for lang in ('en','zh')],
                  'videos':[],'visualizations':[]}
        state={'phase':'publication_pending','identity':{'template_commit':'a'*40,'paper':{'repository':'mathesis-papers/example'}},
               'expected_manifest':manifest,'published_commit':'b'*40,'visualization':{'decision':'not_useful','reason':'A short argument is clearer in prose.'}}
        ledger=Ledger()
        def fetch(url,limit):
            if '/actions/runs?' in url:return json.dumps({'workflow_runs':[{'id':123,'path':'.github/workflows/pages.yml','head_sha':'b'*40,'status':'completed','conclusion':'success'}]}).encode()
            return json.dumps(manifest).encode() if url.endswith('.json') else b'<html>reading</html>'
        def wrong_head(url,limit):
            if '/actions/runs?' in url:return json.dumps({'workflow_runs':[{'id':1,'path':'.github/workflows/pages.yml','head_sha':'c'*40,'status':'completed','conclusion':'success'}]}).encode()
            raise AssertionError('Do not bless matching URLs before the checked commit is deployed')
        self.assertFalse(hosted.readback(ledger,state,wrong_head,lambda url,n:b'%PDF-'))
        self.assertEqual(state['phase'],'publication_pending')
        self.assertTrue(hosted.readback(ledger,state,fetch,lambda url,n:b'%PDF-'))
        self.assertEqual(state['products']['video'],'pending')
        self.assertEqual(state['products']['visualization'],'not_useful')
        self.assertEqual(state['products']['story'],'published')

    def test_new_versions_preserve_old_exposition_and_original_doi(self):
        def entry(data): return (data,'100644')
        oldpaper={'work':{'work':'note','current_version':1,'repository_name':'10.5281-zenodo.123','doi':'10.5281/zenodo.123'}}
        newpaper={'work':dict(oldpaper['work'],current_version=2,doi='10.5281/zenodo.456')}
        original={'paper.json':entry(json.dumps(oldpaper).encode()),'.mathesis/generated.json':entry(json.dumps({'sha256':{'paper.json':'unused'}}).encode()),
                  'books/note/en/README.md':entry(b'Prior explanation'), 'tools/build.py':entry(b'original build'),
                  'versions/v1/paper.pdf':entry(b'%PDF-original'), 'human.txt':entry(b'human addition')}
        original['template-manifest.json']=entry(json.dumps({'files':{'tools/build.py':{'sha256':hashlib.sha256(b'original build').hexdigest(),'mode':'100644'}}}).encode())
        authored={'paper.json':entry(json.dumps(newpaper).encode()),'.mathesis/generated.json':entry(b'{}'), 'books/note/en/README.md':entry(b'New explanation'), 'tools/build.py':entry(b'new pinned build')}
        tree=promote.advance_version_tree(original,authored,'note',2,'a'*40)
        self.assertEqual(tree['versions/v1/paper.pdf'],original['versions/v1/paper.pdf'])
        self.assertEqual(tree['versions/v1/materials/books/note/en/README.md'][0],b'Prior explanation')
        self.assertEqual(json.loads(tree['versions/v1/materials/paper.json'][0])['work']['doi'],'10.5281/zenodo.123')
        self.assertEqual(tree['human.txt'][0],b'human addition')
        original['tools/build.py']=entry(b'human edit')
        with self.assertRaisesRegex(ValueError,'human-edited'): promote.advance_version_tree(original,authored,'note',2,'a'*40)


class RecoveryTests(unittest.TestCase):
    def test_timeout_and_browser_failure_remain_in_the_private_job(self):
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)
            serial.write(run/'checkpoint.json',{'state':'rebuilding_products',
                         'active_check':{'name':'Inspect rendered pages','started_at':'2026-11-23T00:00:00Z'}})
            serial.write(run/'qa/browser.json',{'failure':{'name':'en/index.html fonts','message':'timed out'}})
            error=subprocess.TimeoutExpired(['node','paper_site_qa.cjs'],900)
            hosted.record_failed_check(run,'host_qa',error)
            ledger=Ledger();state={'key':'a'*64}
            hosted.record_build_failure(ledger,state,run)
            self.assertEqual(state['last_failure']['timeout_seconds'],900)
            self.assertIn('qa/browser.json',state['latest_checks']['sha256'])
            checkpoint=serial.read(run/'checkpoint.json');checkpoint.pop('last_failure')
            serial.write(run/'checkpoint.json',checkpoint)
            hosted.record_build_failure(ledger,state,run)
            self.assertEqual(state['last_failure']['type'],'InterruptedCheck')
            self.assertEqual(state['last_failure']['stage'],'Inspect rendered pages')

    def test_build_failure_names_the_exact_command_and_keeps_bounded_output(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            serial.write(run/'envelope.json', {'repository_path':str(run/'paper')})
            serial.write(run/'result.json', {'visualization':{'decision':'not_useful'}})
            (run/'operator-checks.log').write_text('x'*10000+'\nMathesis: layout violations: node_modules\n')
            command = ['python', 'paper.py', 'check', '--complete']
            def fail(*args, **kwargs):
                with hosted.serial.host_stage(run, 'Paper layout check', command):
                    raise subprocess.CalledProcessError(1, command)
            with patch.object(hosted.serial, 'validate_run', side_effect=fail):
                with self.assertRaises(subprocess.CalledProcessError): hosted.build(run)
            checkpoint = serial.read(run/'checkpoint.json')
            failure = checkpoint['last_failure']
            self.assertEqual(checkpoint['active_check']['name'], 'Paper layout check')
            self.assertEqual(failure['stage'], 'host_qa')
            self.assertEqual(failure['command'], command)
            self.assertEqual(failure['exit_code'], 1)
            self.assertIn('layout violations: node_modules', failure['output_tail'])
            self.assertLessEqual(len(failure['output_tail'].encode()), 6000)
            ledger = Ledger(); state = {'key':'a'*64}
            hosted.record_build_failure(ledger, state, run)
            self.assertEqual(ledger.writes[-1]['last_failure'], failure)

    def test_publication_failure_retains_the_successful_build_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            run=Path(folder)
            serial.write(run/'checkpoint.json', {'state':'local_checks_passed','source_sha256':'a'*64})
            hosted.record_failed_check(run, 'publication', ValueError('paper repository advanced'))
            checkpoint=serial.read(run/'checkpoint.json')
            self.assertEqual(checkpoint['state'], 'local_checks_passed')
            self.assertEqual(checkpoint['source_sha256'], 'a'*64)
            self.assertEqual(checkpoint['last_failure']['reason'], 'paper repository advanced')
            self.assertEqual(checkpoint['last_failure']['stage'], 'publication')

    def test_repeated_build_failures_preserve_old_checks_and_save_current_failure(self):
        class API:
            def __init__(self): self.files = {}
            def repo(self, name): return {'private': True}
            def state(self, name):
                return (None, None, None, {p: {'sha': hosted.publish.sha(b)} for p, b in self.files.items()})
            def sync(self, name, files, *args): self.files.update(files)
        api = API()
        ledger = hosted.Ledger(api, 'production-state')
        state = {'key': 'a'*64, 'phase': 'sources_saved'}
        ledger.get(state['key'])
        legacy = 'checks/' + state['key'] + '/operator-checks.log'
        api.files[legacy] = b'original failure'
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            (run/'operator-checks.log').write_bytes(b'first retry')
            hosted.record_build_failure(ledger, state, run)
            first = copy.deepcopy(state['latest_checks'])
            (run/'operator-checks.log').write_bytes(b'second retry')
            hosted.record_build_failure(ledger, state, run)
            second = state['latest_checks']
            self.assertNotEqual(first['prefix'], second['prefix'])
            self.assertEqual(api.files[first['prefix']+'operator-checks.log'], b'first retry')
            self.assertEqual(api.files[second['prefix']+'operator-checks.log'], b'second retry')
            before = dict(api.files)
            hosted.record_build_failure(ledger, state, run)
            self.assertEqual(api.files, before)
            self.assertEqual(api.files[legacy], b'original failure')
            saved = json.loads(api.files['jobs/'+state['key']+'.json'])
            self.assertEqual(saved['phase'], 'needs_attention')
            self.assertEqual(saved['latest_checks'], second)

    def test_saved_build_recovery_is_exact_and_does_not_restart_a_failed_build_forever(self):
        state = {'key': 'a'*64, 'phase': 'needs_attention', 'blocked_stage': 'host_build_or_publication',
                 'source_archive_sha256': 'b'*64, 'agent_id': 'original-agent'}
        recovery = {'id': 'matching-toolchain-v1', 'source_archive_sha256': 'b'*64, 'toolchain_image': 'matching-image'}
        config = {'saved_build_recovery': {state['key']: recovery}, 'toolchain_image': 'matching-image'}
        ledger = Ledger()
        hosted.resume_saved_build(state, config, ledger)
        self.assertEqual(state['phase'], 'sources_saved')
        self.assertEqual(state['agent_id'], 'original-agent')
        state.update(phase='needs_attention', blocked_stage='host_build_or_publication')
        hosted.resume_saved_build(state, config, ledger)
        self.assertEqual(state['phase'], 'needs_attention')
        self.assertEqual(len(state['build_recovery_history']), 1)
        state.pop('build_recovery_history')
        state['source_archive_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'exact source'):
            hosted.resume_saved_build(state, config, ledger)

    def test_parked_failures_are_visible_instead_of_reported_up_to_date(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ledger = Ledger([{'key':'original', 'phase':'needs_attention'}])
            with patch.object(hosted, 'candidate_works', return_value=[]):
                result = hosted.reconcile(root, root/'work', {}, ledger, object(), {}, None)
            self.assertEqual(result['state'], 'needs_attention')
            self.assertEqual(result['needs_attention'], ['original'])
            self.assertEqual(result['phases'], {'needs_attention': 1})

    def test_provider_error_observation_retains_status_without_a_new_task(self):
        ledger = Ledger()
        state = {'key':'original', 'phase':'intent'}
        error = hosted.cma_content.ProviderError('POST', '/api/v1/agents', 422, 'invalid_request', 'name')
        hosted.wait_for(state, 'provider', error, ledger)
        self.assertEqual(state['phase'], 'intent')
        self.assertEqual(state['last_error']['status'], 422)
        self.assertEqual(state['last_error']['field'], 'name')
        self.assertEqual(ledger.writes[-1], state)

    def test_two_codex_tasks_share_one_machine_and_do_not_duplicate_on_the_next_tick(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            serial.write(root/'works.json',{'works':[]})
            works=[{'work':name,'current_version':1,'repository_name':name} for name in ('one','two','three')]
            class StateLedger(Ledger):
                def get(self,key):return next((s for s in self.rows if s['key']==key),None)
            ledger=StateLedger()
            class Provider:
                def __init__(self):self.posts=[]
                def facts(self,workspace):return 'one-existing-sandbox'
                def call(self,method,path,body=None,key=None):
                    if method=='POST':
                        self.posts.append(copy.deepcopy(body))
                        return {'id':'original-operation-'+str(len(self.posts))}
                    return {'status':'provisioning'}
            def prepare(root,workspace,work,config,**kwargs):
                identity={'paper':{'id':work['work'],'version':1,'repository':'mathesis-papers/'+work['work'],'title':work['work']},
                          'source_hashes':{'paper.json':'b'*64},'template_commit':'c'*40,'skill_sha256':'d'*64,
                          'allow_request_registration':True}
                envelope={'schema':'mathesis.paper-production-envelope.v1','identity':identity,'input_projection_path':'projection',
                          'do_not_rereview':True,'publication_authorized':False}
                return root/'repo',root/'run',envelope,'e'*40
            provider=Provider();config={'api_base':'public-source','execution_mode':'shared','authoring_concurrency':2}
            with patch.object(hosted,'candidate_works',return_value=works),patch.object(hosted,'public_detail',return_value=('collection',{})),\
                 patch.object(hosted,'prepare',side_effect=prepare),patch.object(hosted.cma_content,'projection_packet',return_value={}),\
                 patch.object(hosted.serial,'command',return_value='f'*40):
                first=hosted.reconcile(root,root/'work',config,ledger,provider,{'collection':'same-workspace','external':'same-workspace'},None)
                second=hosted.reconcile(root,root/'work',config,ledger,provider,{'collection':'same-workspace','external':'same-workspace'},None)
            self.assertEqual(first['admitted'],['one','two'])
            self.assertEqual(second['state'],'waiting_for_original_job')
            self.assertEqual(len(provider.posts),2)
            self.assertEqual({p['workspace_id'] for p in provider.posts},{'same-workspace'})

    def test_original_shared_job_prevents_admitting_the_next_paper(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,value in [('works.json',{'works':[]}),('repositories.json',{}),('publisher.json',{}),('production.json',{})]:
                serial.write(root/name,value)
            job={'schema':'mathesis.cma-content.v1','key':'original','phase':'provisioning', 'lane':'collection',
                 'lifecycle_id':'original-operation'}
            ledger=Ledger([job])
            class Provider:
                def call(self,method,path,*args,**kwargs):
                    if method!='GET' or 'original-operation' not in path:raise AssertionError('Do not admit another job')
                    return {'status':'provisioning'}
            with patch.object(hosted,'candidate_works',side_effect=AssertionError('Do not advance while the original runs')):
                result=hosted.reconcile(root,root/'work',{'execution_mode':'serial_shared'},ledger,Provider(),
                                       {'collection':'same','external':'same'},None)
            self.assertEqual(result['state'],'waiting_for_original_job')

    def test_template_update_reuses_source_and_original_remote_identity(self):
        state={'key':'immutable-key','phase':'published','identity':{'template_commit':'a'*40},
               'source_archive_sha256':'c'*64,'published_commit':'b'*40,'agent_id':'original-agent',
               'publication_intent':{'source_tree':{}},'expected_manifest':{'version':1}}
        self.assertTrue(hosted.plan_template_adaptation(state,'d'*40))
        self.assertEqual(state['key'],'immutable-key')
        self.assertEqual(state['agent_id'],'original-agent')
        self.assertEqual(state['identity']['template_commit'],'a'*40)
        self.assertEqual(state['template_adaptation']['template_commit'],'d'*40)
        self.assertEqual(state['publication_base'],'b'*40)
        self.assertNotIn('publication_intent',state)
        self.assertEqual(state['publication_history'][0]['published_commit'],'b'*40)
        self.assertEqual(state['phase'],'sources_saved')

    def test_failed_internal_provider_does_not_stop_external_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,value in [('works.json',{'works':[]}),('repositories.json',{}),('publisher.json',{}),('production.json',{})]:
                serial.write(root/name,value)
            jobs=[{'schema':'mathesis.cma-content.v1','key':lane,'phase':'intent','lane':lane,
                   'request':{'workspace_id':'wks_'+lane},'idempotency_key':lane} for lane in ('collection','external')]
            ledger=Ledger(jobs)
            class Provider:
                def __init__(self):self.called=[]
                def facts(self,workspace):return 'sbx_'+workspace
                def call(self,method,path,body=None,key=None):
                    self.called.append((method,path,key))
                    if method=='POST':
                        if key=='collection':raise TimeoutError('internal unavailable')
                        return {'id':'external_operation'}
                    if 'agent-lifecycle' in path:return {'status':'provisioning'}
                    raise AssertionError(path)
            provider=Provider()
            hosted.reconcile(root,root/'work',{'template_commit':'a'*40},ledger,provider,
                             {'collection':'wks_collection','external':'wks_external'},None)
            external=next(s for s in ledger.rows if s['lane']=='external')
            self.assertEqual(external['lifecycle_id'],'external_operation')
            self.assertEqual(external['phase'],'provisioning')
            self.assertEqual(ledger.rows[0]['phase'],'intent')
