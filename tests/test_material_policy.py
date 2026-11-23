"""Exercise the production Python adapter against the actual pinned Rust binary."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import material_policy as policy


class MaterialPolicy(unittest.TestCase):
    def test_admission_rejects_unknown_phases_and_does_not_count_publication_as_authoring(self):
        self.assertEqual(policy.admission([{'phase': 'authoring'}, {'phase': 'publication_pending'}], 6)['slots'], 5)
        for phase in ('future_provider_status', '', None):
            with self.assertRaises(ValueError):
                policy.admission([{'phase': phase}], 6)
        for capacity in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                policy.admission([], capacity)

    def test_refused_transition_leaves_the_callers_checkpoint_untouched(self):
        state = {'phase': 'needs_attention', 'blocked_stage': 'provisioning', 'agent_id': 'original',
                 'source_archive_sha256': 'a'*64, 'private_receipt': {'opaque': [1, 2]}}
        original = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'exact source'):
            policy.transition('resume_build', state, recovery={'id': 'repair', 'source_archive_sha256': 'a'*64,
                              'toolchain_image': 'pinned'}, toolchain_image='pinned', observed_at='2026-11-23T09:00:00Z')
        self.assertEqual(state, original)

    def test_missing_binary_has_no_python_fallback(self):
        with patch.dict(os.environ, {'MATHESIS_CATALOG_BINARY': ''}), patch.object(policy.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'build the pinned'):
                policy.admission([], 6)

    def test_cli_rejects_schema_typos_and_trailing_json_without_a_decision(self):
        binary = os.environ['MATHESIS_CATALOG_BINARY']
        request = {'schema': policy.SCHEMA, 'action': 'admission', 'capacity': 6, 'jobs': []}
        for changes in ({'schema': 'wrong'}, {'capactiy': 6}, {'action': 'admit_everything'}):
            result = subprocess.run([binary, 'material-plan'], input=json.dumps({**request, **changes}),
                                    text=True, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
        result = subprocess.run([binary, 'material-plan'], input=json.dumps(request)+' {}',
                                text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
