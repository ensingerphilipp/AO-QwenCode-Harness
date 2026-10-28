#!/usr/bin/env python3
"""Operator-triggered, fail-closed AO project orchestrator replacement."""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

FIX = 'e65fa0b27c3a3ec6e6a81fa451ab630adcdff572'
PROJECT = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$')
SOURCE = Path.home() / 'src/agent-orchestrator'
DB = Path.home() / '.ao/data/ao.db'


def run(*argv, timeout=20):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f'{" ".join(argv[:3])} failed: {(p.stderr or p.stdout).strip()[:400]}')
    return p.stdout


def load_args(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink() or p.stat().st_size > 4096:
        raise ValueError('skill argument file is missing, symlinked, or oversized')
    raw = p.read_text()
    # Qwen's private argument file is a shell-style argument line; a single
    # project id is deliberately the entire accepted grammar.
    project = raw.strip()
    if not PROJECT.fullmatch(project):
        raise ValueError('expected exactly one AO project ID')
    return project


def revision(binary):
    metadata = run('go', 'version', '-m', str(binary))
    match = re.search(r'^\s*build\s+vcs\.revision=([0-9a-f]{40})$', metadata, re.M)
    if not match:
        raise RuntimeError('AO binary has no trustworthy embedded Git revision')
    return match.group(1)


def gate_fixed_daemon():
    status = json.loads(run('ao', 'status', '--json'))
    if status.get('state') != 'ready' or status.get('health') != 'ok':
        raise RuntimeError('AO daemon is not healthy')
    daemon = Path(status.get('executablePath') or '')
    if not daemon.is_file():
        raise RuntimeError('cannot inspect the running AO daemon binary')
    daemon_rev = revision(daemon)
    cli_rev = revision(Path.home() / '.local/state/ao-qwen-code-harness/ao-cli/ao')
    if daemon_rev != cli_rev:
        raise RuntimeError('running daemon and AO CLI use different revisions; finish the update and restart AO')
    if not (SOURCE / '.git').exists():
        raise RuntimeError('AO source checkout is unavailable for build ancestry verification')
    run('git', '-C', str(SOURCE), 'cat-file', '-e', daemon_rev + '^{commit}')
    p = subprocess.run(['git', '-C', str(SOURCE), 'merge-base', '--is-ancestor', FIX, daemon_rev], timeout=20)
    if p.returncode:
        raise RuntimeError(f'AO build {daemon_rev[:12]} does not contain upstream replacement fix #7124')
    return daemon_rev


def sessions(project):
    doc = json.loads(run('ao', 'orchestrator', 'ls', '--json'))
    return {s['id']: s for s in doc['data'] if s.get('projectId') == project and s.get('role') == 'orchestrator'}


def inspect_binding(project, sid):
    uri = f'file:{DB}?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        rows = db.execute('''SELECT s.provider_conversation_id, s.is_terminated,
                   c.current_session_id, b.session_id, b.provider_conversation_id
            FROM sessions s JOIN conversations c ON c.project_id=s.project_id
              AND c.scope='project' AND c.current_session_id=s.id
            JOIN conversation_branches b ON b.id=c.active_branch_id
            WHERE s.id=? AND s.project_id=?''', (sid, project)).fetchall()
    return len(rows) == 1 and rows[0][0] and rows[0][0] == rows[0][4] and rows[0][3] == sid and rows[0][1] == 0


def replace(project):
    rev = gate_fixed_daemon()
    record = json.loads(run('ao', 'project', 'get', project, '--json')).get('project')
    if not isinstance(record, dict) or record.get('id') != project:
        raise RuntimeError('AO project lookup returned an unexpected identity')
    before = sessions(project)
    current = [s for s in before.values() if s.get('status') != 'terminated']
    if len(current) != 1 or current[0].get('status') != 'exited':
        raise RuntimeError('expected exactly one exited orchestrator; inspect project state before replacement')
    old_id = current[0]['id']
    print(f'Preflight: fixed AO build {rev[:12]}, project {project}, exited {old_id}.')
    run('ao', 'session', 'kill', old_id, '--project', project, timeout=45)
    if sessions(project).get(old_id, {}).get('status') != 'terminated':
        raise RuntimeError('old orchestrator did not become terminated; no spawn attempted')
    print('Old orchestrator terminated through AO. Spawning exactly one replacement...', flush=True)
    run('ao', 'spawn', '--project', project, '--kind', 'orchestrator', '--harness', 'qwen',
        '--mode', 'chat', '--name', 'recovered-orchestrator',
        '--prompt', 'Resume project coordination. Read the project contracts and global orchestrator rules; inspect current AO and PR state before acting. Report readiness and any unresolved work.', timeout=90)
    for _ in range(30):
        after = sessions(project)
        new = [s for sid, s in after.items() if sid not in before]
        if len(new) > 1:
            raise RuntimeError('multiple new orchestrators appeared; inspect AO state')
        if len(new) == 1 and new[0].get('status') in ('terminated', 'exited'):
            raise RuntimeError(f'new orchestrator {new[0]["id"]} exited before verification')
        if len(new) == 1 and inspect_binding(project, new[0]['id']):
            print(f'PASS: {new[0]["id"]} has a matching, session-owned active provider branch.')
            return
        time.sleep(1)
    raise RuntimeError('spawn was sent, but the new session and provider branch did not verify; inspect AO state before retrying')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--args-file', required=True)
    args = parser.parse_args()
    try:
        replace(load_args(args.args_file))
    except (ValueError, RuntimeError, OSError, sqlite3.Error, subprocess.TimeoutExpired, KeyError) as exc:
        print(f'BLOCKED: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
