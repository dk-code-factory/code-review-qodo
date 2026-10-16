"""Exercise real Git scope and the Qodo subprocess lifecycle without remote reviews."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / 'scripts' / 'review.py'

FAKE_QODO = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

scenario_path = Path(os.environ['QODO_TEST_SCENARIO'])
scenario = json.loads(scenario_path.read_text())
args = sys.argv[1:]
record = {'args': args, 'cwd': os.getcwd()}
if '--context-file' in args:
    record['context'] = json.loads(Path(args[args.index('--context-file') + 1]).read_text())
scenario.setdefault('calls', []).append(record)
if args == ['--version']:
    output, code = scenario.get('version', '1.6.0'), 0
elif args == ['review', '--help']:
    output, code = '--async --retry-submission --context-file', 0
elif args == ['read', 'whoami', '--json']:
    output, code = scenario.get('auth', ['{"user":"test"}', 0])
    if code:
        print('trace test-trace-id', file=sys.stderr)
else:
    response = scenario['responses'].pop(0)
    output, code = response['output'], response['code']
scenario_path.write_text(json.dumps(scenario))
print(output if isinstance(output, str) else json.dumps(output))
sys.exit(code)
'''


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='qodo-skill-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / 'remote'
        self.repo = self.root / 'checkout'
        self.remote.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Skill Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Skill Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.git(self.remote, 'init', '-b', 'scylla-4.x')
        self.commit(self.remote, 'old.txt', 'old\n')
        self.old = self.git(self.remote, 'rev-parse', 'HEAD')
        self.git(self.root, 'clone', str(self.remote), str(self.repo))
        self.commit(self.remote, 'shared.txt', 'shared\n')
        self.common = self.git(self.remote, 'rev-parse', 'HEAD')
        self.git(self.repo, 'fetch', 'origin')
        self.git(self.repo, 'checkout', '-b', 'feature', 'origin/scylla-4.x')
        self.commit(self.repo, 'feature.txt', 'feature\n')
        self.commit(self.remote, 'target-only.txt', 'target-only\n')
        self.target_tip = self.git(self.remote, 'rev-parse', 'HEAD')
        self.context = self.root / 'context.json'
        self.context.write_text(json.dumps({'summary': 'Add feature behavior.',
                                           'decisions': ['Keep existing behavior for old callers.']}))
        self.state = self.root / 'run'
        self.fake = self.root / 'qodo'
        self.fake.write_text(FAKE_QODO)
        self.fake.chmod(0o700)
        self.scenario = self.root / 'scenario.json'
        self.env['QODO_TEST_SCENARIO'] = str(self.scenario)
        self.configure([])

    def git(self, cwd, *args):
        result = subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=cwd,
                                env=self.env, text=True, capture_output=True, check=True)
        return result.stdout.strip()

    def commit(self, repo, name, content):
        (repo / name).write_text(content)
        self.git(repo, 'add', name)
        self.git(repo, 'commit', '-m', 'Add ' + name)

    def configure(self, responses, **extra):
        self.scenario.write_text(json.dumps({'responses': responses, **extra}))

    def call(self, *args):
        return subprocess.run([sys.executable, str(HELPER), *args], cwd=self.repo,
                              env=self.env, text=True, capture_output=True)

    def start(self, *args, exact=None):
        selection = ['--base', exact] if exact else ['--target', 'scylla-4.x']
        return self.call('start', *selection, '--context-file', str(self.context),
                         '--qodo', str(self.fake), '--state-dir', str(self.state), *args)

    def read_state(self):
        return json.loads((self.state / 'state.json').read_text())

    def review_calls(self):
        return [c for c in json.loads(self.scenario.read_text()).get('calls', [])
                if c['args'][0] == 'review' and '--help' not in c['args']]

    def test_stale_branch_and_newer_target_do_not_expand_scope(self):
        result = self.start('--dry-run', '--require-clean')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stderr)
        self.assertEqual(plan['base'], self.common)
        self.assertEqual(plan['target_sha'], self.target_tip)
        self.assertEqual(plan['paths'], ['feature.txt'])
        self.assertEqual(self.git(self.repo, 'rev-parse', 'scylla-4.x'), self.old)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.review_calls(), [])

    def test_exact_base_is_not_converted_to_merge_base(self):
        result = self.start('--dry-run', exact='scylla-4.x')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stderr)
        self.assertEqual(plan['base'], self.old)
        self.assertEqual(plan['paths'], ['feature.txt', 'shared.txt'])

    def test_local_scope_includes_untracked_and_unstaged_files(self):
        (self.repo / 'old.txt').write_text('local change\n')
        (self.repo / 'new with space.txt').write_text('new\n')
        result = self.start('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stderr)['paths'],
                         ['feature.txt', 'new with space.txt', 'old.txt'])
        result = self.start('--require-clean')
        self.assertEqual(result.returncode, 1)
        self.assertIn('clean checkout', result.stderr)
        self.assertEqual(self.review_calls(), [])

    def test_failed_fetch_stops_without_submission(self):
        result = self.call('start', '--target', 'missing-branch', '--context-file', str(self.context),
                           '--qodo', str(self.fake))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.review_calls(), [])

    def test_auth_error_survives_trace_stderr(self):
        self.configure([], auth=[json.dumps({'error': {'code': 'invalid_token', 'message': 'Sign in.'}}), 1])
        result = self.start()
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid_token', result.stderr)
        self.assertIn('trace test-trace-id', result.stderr)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.review_calls(), [])

    def test_old_version_stops_before_identity_probe(self):
        self.configure([], version='1.5.0')
        result = self.start()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(json.loads(self.scenario.read_text())['calls']), 1)

    def test_submit_poll_throttle_collect_and_cached_result(self):
        findings = {'findings': [{'id': 'F1', 'title': 'Potential defect'}],
                    'meta': {'coverage': {'complete': True}}}
        self.configure([
            {'code': 0, 'output': {'operation_id': 'op-123', 'poll_interval': 3}},
            {'code': 2, 'output': {'status': 'running', 'operation_id': 'op-123'}},
            {'code': 2, 'output': {'status': 'throttled', 'retry_after': 7}},
            {'code': 0, 'output': findings},
        ])
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()['phase'], 'running')
        for expected in (2, 2, 0):
            result = self.call('status', '--state-dir', str(self.state))
            self.assertEqual(result.returncode, expected, result.stderr)
        self.assertEqual(json.loads(result.stdout), findings)
        calls = self.review_calls()
        self.assertEqual(calls[0]['args'][calls[0]['args'].index('--base') + 1], self.common)
        self.assertEqual(calls[0]['context'], json.loads(self.context.read_text()))
        self.assertTrue(all(c['args'] == ['review', 'status', 'op-123', '--json'] for c in calls[1:]))
        result = self.call('status', '--state-dir', str(self.state))
        self.assertEqual(json.loads(result.stdout), findings)
        self.assertEqual(len(self.review_calls()), 4)

    def test_recover_retains_request_and_handles_completed_replay(self):
        self.configure([
            {'code': 1, 'output': {'error': {'message': 'Uncertain. qodo review --async --retry-submission qodo-cli-review-abc-123'}}},
            {'code': 0, 'output': {'findings': [], 'meta': {'coverage': {'complete': False}}}},
        ])
        result = self.start('--deep', '--full')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.read_state()['submission_id'], 'qodo-cli-review-abc-123')
        (self.repo / 'feature.txt').write_text('Changed after submission\n')
        self.context.write_text('{"summary":"Changed context","decisions":[]}')
        result = self.call('recover', '--state-dir', str(self.state))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.review_calls()[1]['args'],
                         ['review', '--async', '--json', '--retry-submission', 'qodo-cli-review-abc-123'])
        state = self.read_state()
        self.assertEqual(state['phase'], 'collected')
        self.assertNotIn('operation_id', state)
        self.assertFalse(json.loads(Path(state['result']).read_text())['meta']['coverage']['complete'])

    def test_missing_receipt_is_failure_not_empty_review(self):
        for output in ({}, {'trace': 'not-an-operation'}, 'not-json', {'error': {'message': 'Failure'}}):
            with self.subTest(output=output):
                self.state = self.root / ('run-' + str(len(list(self.root.glob('run-*')))))
                self.configure([{'code': 0, 'output': output}])
                self.assertEqual(self.start().returncode, 1)
                self.assertEqual(self.read_state()['phase'], 'failed')

    def test_existing_run_and_accepted_operation_are_not_resubmitted(self):
        self.configure([{'code': 0, 'output': {'operation_id': 'op-123'}}])
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(self.start().returncode, 1)
        result = self.call('recover', '--state-dir', str(self.state))
        self.assertEqual(result.returncode, 1)
        self.assertIn('use status', result.stderr)
        self.assertEqual(len(self.review_calls()), 1)

    def test_expired_operation_keeps_handle_and_error(self):
        self.configure([
            {'code': 0, 'output': {'operation_id': 'op-123'}},
            {'code': 1, 'output': {'error': {'message': 'Expired or missing review'}}},
        ])
        self.assertEqual(self.start().returncode, 0)
        result = self.call('status', '--state-dir', str(self.state))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Expired', result.stdout)
        self.assertEqual(self.read_state()['operation_id'], 'op-123')
        self.assertEqual(len(self.review_calls()), 2)

    def test_interrupted_call_requires_receipt_inspection(self):
        self.configure([{'code': 0, 'output': {'operation_id': 'op-123'}}])
        self.assertEqual(self.start().returncode, 0)
        state = self.read_state()
        state['phase'] = 'status-in-flight'
        (self.state / 'state.json').write_text(json.dumps(state))
        result = self.call('status', '--state-dir', str(self.state))
        self.assertEqual(result.returncode, 1)
        self.assertIn('Inspect retained stdout/stderr', result.stderr)
        self.assertEqual(len(self.review_calls()), 1)

    def test_artifacts_cannot_be_written_inside_review_tree(self):
        self.state = self.repo / 'review-artifacts'
        result = self.start()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.review_calls(), [])

    def test_invalid_context_stops_before_qodo(self):
        self.context.write_text('{"summary":"Intent", "decisions":"not an array"}')
        result = self.start()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.review_calls(), [])


if __name__ == '__main__':
    unittest.main()
