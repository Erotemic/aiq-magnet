"""Run a CPU client only while its Slurm serving allocation and lease live.

Descriptors are private execution records, not a second lease authority.
Slurm owns physical resources; the infer-stack ledger owns endpoint lifetime.
This module never acquires a lease or changes scheduler/serving state.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import stat
import subprocess
import time


def load_descriptor(path):
    path = Path(path)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError('serving descriptor must be private and owned by the client user')
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1 or data.get('kind') != 'slurm-serving-pool':
        raise RuntimeError('unsupported serving pool descriptor')
    if data.get('host') != socket.gethostname().split('.')[0]:
        raise RuntimeError('serving pool belongs to a different host')
    if not str(data.get('owner_job_id', '')).isdigit():
        raise RuntimeError('invalid pool owner job ID')
    data['_directory'] = str(path.resolve().parent)
    return data


def verify_owner(data, endpoints):
    if not set(endpoints) <= set(data['endpoints']):
        raise RuntimeError('requested endpoint is absent from the serving pool')
    env = {k: v for k, v in os.environ.items() if not k.startswith('SQUEUE_')}
    result = subprocess.run(
        ['squeue', '--all', '--local', '--noheader', '--jobs=' + str(data['owner_job_id']),
         '--format=%i|%T|%U'], capture_output=True, text=True, timeout=10, check=True, env=env,
    )
    expected = f"{data['owner_job_id']}|RUNNING|{os.getuid()}"
    if result.stdout.strip() != expected:
        raise RuntimeError('serving allocation is no longer running for this user')
    with sqlite3.connect(Path(data['ledger']).resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as db:
        row = db.execute('SELECT state, expires_at FROM leases WHERE id=?', (data['lease_id'],)).fetchone()
        leased = {r[0] for r in db.execute('SELECT endpoint FROM claims WHERE lease_id=?', (data['lease_id'],))}
    if row is None or row[0] != 'active' or (row[1] is not None and row[1] <= time.time()):
        raise RuntimeError('serving lease is no longer active')
    if not set(endpoints) <= leased:
        raise RuntimeError('requested endpoint is not protected by the owner lease')


def acquire_slot(data, endpoints):
    """Bound active clients across CPU jobs; kernel locks release on crashes."""
    limit = data.get('max_clients', 2)
    if type(limit) is not int or not 1 <= limit <= 128:
        raise RuntimeError('invalid pool client limit')
    while True:
        verify_owner(data, endpoints)
        for index in range(limit):
            path = Path(data['_directory']) / f'client-slot-{index}.lock'
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                os.close(descriptor)
        time.sleep(1)


def stop_child(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def run_client(data, endpoints, command, interval=2):
    verify_owner(data, endpoints)
    if os.environ.get('SLURM_JOB_GPUS') or os.environ.get('SLURM_STEP_GPUS'):
        raise RuntimeError('serving-pool clients must not reserve additional GPUs')
    slot = acquire_slot(data, endpoints)
    # Never expose the owner's GPU slice to a CPU client. The descriptor's
    # environment is allowlisted so it cannot inject arbitrary process state.
    allowed = {'OPENAI_BASE_URL', 'OPENAI_API_KEY', 'INFER_STACK_LEASE_ID', 'INFER_STACK_MODELS'}
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='')
    env.update({k: v for k, v in data['environment'].items() if k in allowed})
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    def cancel(sig, frame):
        raise SystemExit(128 + sig)
    proc = None
    try:
        for sig in previous:
            signal.signal(sig, cancel)
        proc = subprocess.Popen(command, env=env)
        while True:
            try:
                return proc.wait(timeout=interval)
            except subprocess.TimeoutExpired:
                verify_owner(data, endpoints)
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        if proc is not None:
            stop_child(proc)
        os.close(slot)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--descriptor', required=True)
    parser.add_argument('--endpoints', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a child command is required after --')
    try:
        return run_client(load_descriptor(args.descriptor), args.endpoints.split(','), command)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'[serving-pool] client stopped: {exc}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
