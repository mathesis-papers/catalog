import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cma_content as cma
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
        return {'status':'ready', 'sandbox':{'id':'sbx_wks_internal'}, 'first_message':{'state':'completed'}}


class AdapterTests(unittest.TestCase):
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
            state.update(phase='sources_ready',agent_id='agent')
            with self.assertRaisesRegex(ValueError, 'managed template'): cma.import_sources(Files(), state, repo, run)
            self.assertEqual((repo/'tools/build.py').read_text(), 'original build')
