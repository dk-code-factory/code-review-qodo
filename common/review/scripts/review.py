#!/usr/bin/env python3
"""Scope a Qodo review and retain each asynchronous lifecycle call."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def run(args, cwd):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise ValueError('\n'.join(part.strip() for part in (result.stdout, result.stderr) if part.strip()) or str(args))
    return result.stdout.rstrip('\n')


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def context_file(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or not isinstance(value.get('summary'), str) or not value['summary'].strip():
        raise ValueError('Context must contain a nonempty summary string.')
    if not isinstance(value.get('decisions', []), list) or any(
        not isinstance(item, str) for item in value.get('decisions', [])
    ):
        raise ValueError('Context decisions must be an array of strings.')
    if 'context_refs' in value and not isinstance(value['context_refs'], list):
        raise ValueError('Context context_refs must be an array.')
    return value


def scope(args):
    root = run(['git', 'rev-parse', '--show-toplevel'], args.repo)
    head = run(['git', 'rev-parse', 'HEAD'], root)
    dirty = bool(run(['git', 'status', '--porcelain'], root))
    if args.require_clean and dirty:
        raise ValueError('PR review requires a clean checkout at the pinned head.')
    if args.target:
        run(['git', 'check-ref-format', 'refs/heads/' + args.target], root)
        if args.remote not in run(['git', 'remote'], root).splitlines():
            raise ValueError('Choose an existing remote for the target repository.')
        run(['git', 'fetch', '--no-tags', args.remote, 'refs/heads/' + args.target], root)
        target_sha = run(['git', 'rev-parse', 'FETCH_HEAD^{commit}'], root)
        base = run(['git', 'merge-base', head, target_sha], root)
    else:
        target_sha = None
        base = run(['git', 'rev-parse', '--verify', '--end-of-options', args.base + '^{commit}'], root)
    tracked = run(['git', 'diff', '--name-only', '-z', base, '--'], root)
    untracked = run(['git', 'ls-files', '--others', '--exclude-standard', '-z'], root)
    paths = sorted(set(filter(None, (tracked + untracked).split('\0'))))
    return {'repo': root, 'head': head, 'base': base, 'target_sha': target_sha,
            'target': args.target, 'remote': args.remote if args.target else None,
            'dirty': dirty, 'paths': paths, 'path_count': len(paths)}


def executable(name):
    found = shutil.which(name)
    if not found and name == 'qodo':
        found = str(Path(os.environ.get('QODO_HOME', str(Path.home() / '.qodo'))) / 'bin/qodo')
    if not found or not os.access(found, os.X_OK):
        raise ValueError('Qodo executable not found: ' + name)
    return str(Path(found).resolve())


def preflight(qodo, repo):
    version = run([qodo, '--version'], repo)
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version)
    if not match or tuple(map(int, match.groups())) < (1, 6, 0):
        raise ValueError('This workflow requires Qodo CLI >= 1.6.0; found ' + version)
    help_text = run([qodo, 'review', '--help'], repo)
    for flag in ('--async', '--retry-submission', '--context-file'):
        if flag not in help_text:
            raise ValueError('Qodo CLI lacks ' + flag)
    run([qodo, 'read', 'whoami', '--json'], repo)


def invoke(directory, state, command, action):
    number = len(state.setdefault('calls', [])) + 1
    stdout_path = directory / f'{number:03d}-{action}.stdout.json'
    stderr_path = directory / f'{number:03d}-{action}.stderr.txt'
    call = {'command': command, 'stdout': str(stdout_path), 'stderr': str(stderr_path)}
    state['calls'].append(call)
    state['phase'] = action + '-in-flight'
    save(directory / 'state.json', state)
    # Files are opened before the subprocess starts, so interrupted calls retain evidence.
    with stdout_path.open('w', encoding='utf-8') as out, stderr_path.open('w', encoding='utf-8') as err:
        result = subprocess.run(command, cwd=state['repo'], stdout=out, stderr=err)
    stdout = stdout_path.read_text(encoding='utf-8')
    stderr = stderr_path.read_text(encoding='utf-8')
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    call['exit_code'] = result.returncode
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    code = result.returncode
    state['phase'] = 'failed'
    recovery = re.search(r'--retry-submission\s+(qodo-cli-review-[A-Za-z0-9-]+)', stdout + '\n' + stderr)
    if recovery:
        state['submission_id'] = recovery.group(1)
    if isinstance(payload, dict) and not payload.get('error'):
        operation = payload.get('operation_id')
        if code == 0 and isinstance(payload.get('findings'), list):
            state['phase'] = 'collected'
            state['result'] = str(stdout_path)
        elif code == 0 and action != 'status' and isinstance(operation, str) and operation.strip():
            state['operation_id'] = operation
            state['phase'] = 'running'
        elif code == 2 and action == 'status':
            state['phase'] = 'running'
        elif code == 0:
            code = 1
        for key in ('retry_after', 'poll_interval', 'ttl'):
            if key in payload:
                state[key] = payload[key]
    elif code in (0, 2):
        code = 1
    if state['phase'] == 'failed' and code == 2:
        code = 1
    save(directory / 'state.json', state)
    print(f"State: {directory / 'state.json'} ({state['phase']})", file=sys.stderr)
    return code if code in (0, 2) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    start = commands.add_parser('start', help='Fetch and scope a new review, then submit once.')
    start.add_argument('--repo', default='.')
    start.add_argument('--qodo', default='qodo')
    base = start.add_mutually_exclusive_group(required=True)
    base.add_argument('--target', help='Target branch name; fetch and use its merge base.')
    base.add_argument('--base', help='Exact comparison ref or SHA; no fetch or merge-base conversion.')
    start.add_argument('--remote', default='origin')
    start.add_argument('--context-file', required=True)
    start.add_argument('--state-dir', help='New directory outside the repository; defaults to a temporary directory.')
    start.add_argument('--require-clean', action='store_true')
    start.add_argument('--dry-run', action='store_true')
    start.add_argument('--full', action='store_true')
    depth = start.add_mutually_exclusive_group()
    depth.add_argument('--fast', action='store_true')
    depth.add_argument('--deep', action='store_true')
    for action in ('status', 'recover'):
        child = commands.add_parser(action)
        child.add_argument('--state-dir', required=True)
    args = parser.parse_args(argv)
    if args.action == 'start':
        context = context_file(args.context_file)
        state = scope(args)
        print(json.dumps(state, indent=2), file=sys.stderr)
        if args.dry_run:
            return 0
        directory = Path(args.state_dir).resolve() if args.state_dir else None
        if directory and (directory == Path(state['repo']) or Path(state['repo']) in directory.parents):
            raise ValueError('Keep review artifacts outside the reviewed repository.')
        if directory and directory.exists():
            raise ValueError('State directory already exists; use status/recover for an existing run.')
        qodo = executable(args.qodo)
        preflight(qodo, state['repo'])
        if directory:
            directory.mkdir(mode=0o700, parents=True)
        else:
            directory = Path(tempfile.mkdtemp(prefix='qodo-review-')).resolve()
        state['qodo'] = qodo
        save(directory / 'context.json', context)
        command = [qodo, 'review', '--base', state['base'], '--context-file', str(directory / 'context.json'), '--async', '--json']
        command.extend('--' + flag for flag in ('full', 'fast', 'deep') if getattr(args, flag))
    else:
        directory = Path(args.state_dir).resolve()
        state = json.loads((directory / 'state.json').read_text(encoding='utf-8'))
        if state.get('phase') == 'collected':
            print(Path(state['result']).read_text(encoding='utf-8'), end='')
            return 0
        if state.get('phase', '').endswith('-in-flight'):
            raise ValueError('Previous call was interrupted or is active. Inspect retained stdout/stderr before resuming directly with Qodo.')
        if args.action == 'status':
            if not state.get('operation_id'):
                raise ValueError('No accepted operation ID; inspect the submission error and recovery ID.')
            command = [state['qodo'], 'review', 'status', state['operation_id'], '--json']
        else:
            if state.get('operation_id'):
                raise ValueError('An operation was accepted; use status, not submission recovery.')
            if not state.get('submission_id'):
                raise ValueError('No retained submission recovery ID; inspect the original error.')
            preflight(state['qodo'], state['repo'])
            command = [state['qodo'], 'review', '--async', '--json', '--retry-submission', state['submission_id']]
    return invoke(directory, state, command, args.action)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
