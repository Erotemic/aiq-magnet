"""Borrowing has a live owner, bounded concurrency, and no new GPU reservation."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

from magnet import serving_pool as pool
from magnet.leasing import LeaseSettings
from magnet.process_node import MagnetProcessNode
from kwdagger import Pipeline


def descriptor(tmp_path):
    ledger = tmp_path / 'ledger.db'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE leases (id TEXT, state TEXT, expires_at REAL)')
        db.execute('CREATE TABLE claims (lease_id TEXT, endpoint TEXT)')
        db.execute("INSERT INTO leases VALUES ('fixture', 'active', NULL)")
        db.execute("INSERT INTO claims VALUES ('fixture', 'endpoint')")
    data = {'schema_version': 1, 'kind': 'slurm-serving-pool', 'host': socket.gethostname().split('.')[0],
            'owner_job_id': '123', 'lease_id': 'fixture', 'ledger': str(ledger), 'endpoints': ['endpoint'],
            'max_clients': 1, 'environment': {'OPENAI_BASE_URL': 'http://fixture',
                                            'INFER_STACK_LEASE_ID': 'fixture', 'UNSAFE_KEY': 'injected'}}
    path = tmp_path / 'pool.json'
    path.write_text(json.dumps(data)); path.chmod(0o600)
    return path, pool.load_descriptor(path)


def test_descriptor_privacy_and_owner_lease_claims(tmp_path, monkeypatch):
    path, data = descriptor(tmp_path)
    monkeypatch.setattr(pool.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0,
                        f'123|RUNNING|{os.getuid()}\n', ''))
    pool.verify_owner(data, ['endpoint'])
    with pytest.raises(RuntimeError, match='absent'):
        pool.verify_owner(data, ['other'])
    with sqlite3.connect(data['ledger']) as db:
        db.execute('DELETE FROM claims')
    with pytest.raises(RuntimeError, match='protected'):
        pool.verify_owner(data, ['endpoint'])
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match='private'):
        pool.load_descriptor(path)


@pytest.mark.parametrize('state,expiry', [('released', None), ('active', 0)])
def test_released_or_expired_lease_stops_borrowing(tmp_path, monkeypatch, state, expiry):
    _, data = descriptor(tmp_path)
    monkeypatch.setattr(pool.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0,
                        f'123|RUNNING|{os.getuid()}\n', ''))
    with sqlite3.connect(data['ledger']) as db:
        db.execute('UPDATE leases SET state=?, expires_at=?', (state, expiry))
    with pytest.raises(RuntimeError, match='active'):
        pool.verify_owner(data, ['endpoint'])


def test_pool_is_operational_and_preserves_node_identity(tmp_path, monkeypatch):
    class Client(MagnetProcessNode):
        endpoint_params = ('model_id',)
    n = Client(name='client', executable='python -c pass', algo_params={'model_id': 'endpoint'},
               out_paths={'result': 'result.json'})
    p = Pipeline([n])
    before = p.compile_configurations([{}], root_dpath=tmp_path)
    n.apply_lease_settings(LeaseSettings(pool_descriptor='/private/pool.json'))
    after = p.compile_configurations([{}], root_dpath=tmp_path)
    assert set(before.nodes) == set(after.nodes)
    command = next(iter(after.nodes.values())).command
    assert 'magnet.serving_pool' in command and 'infer-stack run' not in command


def test_cpu_client_environment_and_real_child_cleanup_on_owner_loss(tmp_path, monkeypatch):
    _, data = descriptor(tmp_path)
    for k in ('SLURM_JOB_GPUS', 'SLURM_STEP_GPUS', 'UNSAFE_KEY'):
        monkeypatch.delenv(k, raising=False)
    calls = 0
    def verify(*args):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise RuntimeError('owner lost')
    monkeypatch.setattr(pool, 'verify_owner', verify)
    procs = []
    original = subprocess.Popen
    def popen(*a, **kw):
        procs.append(original(*a, **kw)); return procs[-1]
    monkeypatch.setattr(pool.subprocess, 'Popen', popen)
    script = "import time; from pathlib import Path; time.sleep(1); Path('should-not-finish').touch()"
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='owner lost'):
        pool.run_client(data, ['endpoint'], [sys.executable, '-c', script], interval=0.01)
    assert time.monotonic() - start < 1
    assert procs and procs[0].poll() is not None
    assert not Path('should-not-finish').exists()


def test_gpu_reserving_borrower_is_rejected_before_start(tmp_path, monkeypatch):
    _, data = descriptor(tmp_path)
    monkeypatch.setattr(pool, 'verify_owner', lambda *a: None)
    monkeypatch.setenv('SLURM_JOB_GPUS', '0')
    with pytest.raises(RuntimeError, match='additional GPUs'):
        pool.run_client(data, ['endpoint'], ['never-run'])


def test_client_receives_only_endpoint_environment_and_no_gpu_slice(tmp_path, monkeypatch):
    _, data = descriptor(tmp_path)
    monkeypatch.setattr(pool, 'verify_owner', lambda *a: None)
    for name in ('SLURM_JOB_GPUS', 'SLURM_STEP_GPUS', 'UNSAFE_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0,1')
    output = tmp_path / 'environment.json'
    code = "import os,json; from pathlib import Path; Path(" + repr(str(output)) + ").write_text(json.dumps({k:os.environ.get(k) for k in ['CUDA_VISIBLE_DEVICES','INFER_STACK_LEASE_ID','UNSAFE_KEY']}))"
    assert pool.run_client(data, ['endpoint'], [sys.executable, '-c', code]) == 0
    assert json.loads(output.read_text()) == {'CUDA_VISIBLE_DEVICES': '', 'INFER_STACK_LEASE_ID': 'fixture', 'UNSAFE_KEY': None}


def test_kernel_slot_lock_limits_clients_and_releases_on_close(tmp_path, monkeypatch):
    _, data = descriptor(tmp_path)
    monkeypatch.setattr(pool, 'verify_owner', lambda *a: None)
    first = pool.acquire_slot(data, ['endpoint'])
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        os.close(first)
    monkeypatch.setattr(pool.time, 'sleep', sleep)
    second = pool.acquire_slot(data, ['endpoint'])
    assert sleeps == [1]
    os.close(second)
