import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/read-request.cjs'


class RequestTests(unittest.TestCase):
    def read(self, request=None, **variables):
        with tempfile.TemporaryDirectory(prefix='yohaku-request-') as directory:
            output = Path(directory) / 'output'
            if request is not None:
                (Path(directory) / 'update-request.json').write_text(json.dumps(request))
            env = dict(os.environ, GITHUB_OUTPUT=str(output), **variables)
            result = subprocess.run(['node', str(SCRIPT)], cwd=directory, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    universal_newlines=True)
            return result.returncode, output.read_text() if output.exists() else ''

    def test_initial_request_cannot_deploy(self):
        code, output = self.read({'operation': 'none', 'source_ref': 'main', 'force': False}, EVENT_NAME='push')
        self.assertEqual(code, 0)
        self.assertIn('operation=none\n', output)

    def test_manual_dispatch(self):
        code, output = self.read(EVENT_NAME='workflow_dispatch', OPERATION='update', SOURCE_REF='v6.6.7', FORCE='true')
        self.assertEqual(code, 0)
        self.assertIn('source_ref=v6.6.7\nforce=true\n', output)

    def test_repository_dispatch_check(self):
        code, output = self.read(EVENT_NAME='repository_dispatch', OPERATION='check', SOURCE_REF='main', FORCE='false')
        self.assertEqual(code, 0)
        self.assertIn('operation=check\n', output)

    def test_rollback_request(self):
        code, output = self.read({'operation': 'rollback', 'source_ref': 'main', 'force': False}, EVENT_NAME='push')
        self.assertEqual(code, 0)
        self.assertIn('operation=rollback\n', output)

    def test_unknown_operation_is_rejected(self):
        code, output = self.read({'operation': 'unexpected', 'source_ref': 'main', 'force': False}, EVENT_NAME='push')
        self.assertNotEqual(code, 0)
        self.assertEqual(output, '')

    def test_newline_cannot_inject_outputs(self):
        code, output = self.read({'operation': 'update', 'source_ref': 'main\noperation=rollback', 'force': False}, EVENT_NAME='push')
        self.assertNotEqual(code, 0)
        self.assertEqual(output, '')

    def test_string_force_is_rejected(self):
        code, output = self.read({'operation': 'update', 'source_ref': 'main', 'force': 'false'}, EVENT_NAME='push')
        self.assertNotEqual(code, 0)
        self.assertEqual(output, '')
