import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import deploy_pages as pages


class PagesDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.environment = {'GITHUB_REPOSITORY':'mathesis-papers/example','GITHUB_SHA':'a'*40,
                            'GITHUB_RUN_ID':'12','ARTIFACT_ID':'34','ARTIFACT_NAME':'paper-12-1'}
        self.artifact = {'id':34,'name':'paper-12-1','expired':False,
                         'workflow_run':{'id':12,'head_sha':'a'*40}}
        self.calls, self.output = [], []
        self.statuses = ['deployment_in_progress','succeed']

    def api(self, method, path, body=None):
        self.calls.append((method,path,body))
        if path.endswith('/actions/artifacts/34'):
            return copy.deepcopy(self.artifact)
        if method == 'POST':
            return {'id':'original-deployment','pages_url':'https://papers.trureturing.org/example/'}
        self.assertTrue(path.endswith('/pages/deployments/original-deployment'))
        return {'status':self.statuses.pop(0)}

    def deploy(self, api=None):
        return pages.deploy(self.environment,api=api or self.api,oidc=lambda _: 'private-oidc',
                            pause=lambda _:None,emit=self.output.append)

    def test_exact_uploaded_id_bypasses_runtime_listing_and_polls_the_original_deployment(self):
        result = self.deploy()
        self.assertEqual(result['deployment_id'],'original-deployment')
        posts = [c for c in self.calls if c[0]=='POST']
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][2],{'artifact_id':34,'pages_build_version':'a'*40,'oidc_token':'private-oidc'})
        self.assertNotIn('private-oidc',''.join(self.output))
        self.assertFalse(any('ListArtifacts' in c[1] for c in self.calls))

    def test_foreign_or_expired_artifact_cannot_start_a_deployment(self):
        for field,value in [('id',35),('name','other'),('expired',True),
                            ('workflow_run',{'id':13,'head_sha':'a'*40}),
                            ('workflow_run',{'id':12,'head_sha':'b'*40})]:
            with self.subTest(field=field):
                original = copy.deepcopy(self.artifact)
                self.artifact[field] = value
                self.calls.clear()
                with self.assertRaisesRegex(ValueError,'another run or source'):
                    self.deploy()
                self.assertFalse(any(c[0]=='POST' for c in self.calls))
                self.artifact = original

    def test_uncertain_creation_is_not_retried_and_retains_the_run_and_artifact_identity(self):
        def api(method,path,body=None):
            if method == 'POST':
                self.calls.append((method,path,body))
                raise TimeoutError('uncertain response')
            return self.api(method,path,body)
        with self.assertRaises(TimeoutError):
            self.deploy(api)
        self.assertEqual(sum(c[0]=='POST' for c in self.calls),1)
        self.assertIn('"artifact_id": "34"',''.join(self.output))

    def test_terminal_failure_keeps_original_deployment_handle(self):
        self.statuses = ['deployment_content_failed']
        with self.assertRaisesRegex(ValueError,'original-deployment'):
            self.deploy()
        self.assertEqual(sum(c[0]=='POST' for c in self.calls),1)
        self.assertIn('"deployment_id": "original-deployment"',''.join(self.output))

    def test_temporary_provider_status_is_observed_without_another_deployment(self):
        self.statuses = ['deployment_attempt_error','succeed']
        self.assertEqual(self.deploy()['deployment_id'],'original-deployment')
        self.assertEqual(sum(c[0]=='POST' for c in self.calls),1)
