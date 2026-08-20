"""
Per-node endpoint leasing.

The property under test is not "the string looks right" -- it is that the
DAG, not a wrapper script, decides which model is held while which job runs.
"""

import kwdagger
import pytest

from magnet.leasing import (
    INSIDE_LEASE_ENVVAR,
    LEASING_ENVVAR,
    LeasedProcessNode,
    leasing_is_enabled,
)


class Infer(LeasedProcessNode):
    name = 'infer'
    executable = 'python -m pkg.infer'
    endpoint_params = ('model_id', 'extractor_model_id')
    algo_params = {'model_id': None, 'extractor_model_id': None}


class Analyse(LeasedProcessNode):
    """A node that touches no model: it must never hold one."""

    name = 'analyse'
    executable = 'python -m pkg.analyse'
    algo_params = {'metric': None}


@pytest.fixture(autouse=True)
def _leasing_on(monkeypatch):
    monkeypatch.delenv(INSIDE_LEASE_ENVVAR, raising=False)
    monkeypatch.setenv(LEASING_ENVVAR, '1')


def _node(cls, config):
    node = cls()
    node.configure(config)
    return node


def _prefix(command):
    """The lease wrapper only, without the wrapped command."""
    return command.split(' -- ', 1)[0]


def test_the_node_leases_the_models_it_names():
    node = _node(Infer, {'model_id': 'mock/tiny-1b',
                         'extractor_model_id': 'mock/extractor-70b'})
    command = node.command
    assert command.startswith('infer-stack run ')
    # ONE --endpoint carrying every name. `infer-stack run` takes a single
    # comma-separated string, so a repeated flag would silently keep only the
    # last model and leave the rest unleased.
    assert _prefix(command).count('--endpoint') == 1
    assert '--endpoint mock/tiny-1b,mock/extractor-70b' in command
    # The original command survives intact after the `--`.
    assert 'python -m pkg.infer' in command.split(' -- ', 1)[1]
    assert '--model_id=mock/tiny-1b' in command


def test_a_model_free_node_holds_nothing():
    node = _node(Analyse, {'metric': 'auroc'})
    assert node.command.startswith('python -m pkg.analyse')
    assert 'infer-stack' not in node.command


def test_an_unset_endpoint_param_does_not_become_a_lease():
    node = _node(Infer, {'model_id': 'mock/tiny-1b',
                         'extractor_model_id': None})
    prefix = _prefix(node.command)
    assert '--endpoint mock/tiny-1b ' in prefix
    assert 'None' not in prefix


def test_the_same_model_twice_is_named_once():
    node = _node(Infer, {'model_id': 'm', 'extractor_model_id': 'm'})
    assert '--endpoint m ' in _prefix(node.command)


def test_two_instances_lease_different_models():
    """The whole point: the alias comes from the instance, not the class."""
    a = _node(Infer, {'model_id': 'mock/tiny-1b', 'extractor_model_id': None})
    b = _node(Infer, {'model_id': 'mock/frontier-b', 'extractor_model_id': None})
    assert '--endpoint mock/tiny-1b' in a.command
    assert '--endpoint mock/frontier-b' in b.command
    assert 'frontier' not in a.command


def test_the_lease_waits_rather_than_failing_when_busy():
    # A DAG routinely schedules more jobs than the box has GPUs; treating
    # that as an error would make --jobs > n_gpus unusable.
    command = _node(Infer, {'model_id': 'm', 'extractor_model_id': None}).command
    assert '--queue' in command
    assert '--timeout' in command
    assert '--ttl' in command


def test_leasing_is_off_inside_an_outer_lease(monkeypatch):
    monkeypatch.delenv(LEASING_ENVVAR, raising=False)
    monkeypatch.setenv(INSIDE_LEASE_ENVVAR, 'lease-abc123')
    assert not leasing_is_enabled()
    node = _node(Infer, {'model_id': 'm', 'extractor_model_id': None})
    assert node.command.startswith('python -m pkg.infer')


def test_explicit_opt_out(monkeypatch):
    # e.g. a run against OpenRouter, which infer-stack does not manage.
    monkeypatch.delenv(INSIDE_LEASE_ENVVAR, raising=False)
    monkeypatch.setenv(LEASING_ENVVAR, '0')
    assert not leasing_is_enabled()


def test_leasing_is_off_unless_asked_for(monkeypatch):
    """A card pointed at an unmanaged server must keep working untouched."""
    monkeypatch.delenv(INSIDE_LEASE_ENVVAR, raising=False)
    monkeypatch.delenv(LEASING_ENVVAR, raising=False)
    assert not leasing_is_enabled()
    node = _node(Infer, {'model_id': 'm', 'extractor_model_id': None})
    assert node.command.startswith('python -m pkg.infer')


def test_it_is_still_an_ordinary_kwdagger_node():
    node = _node(Infer, {'model_id': 'm', 'extractor_model_id': 'e'})
    assert isinstance(node, kwdagger.ProcessNode)
    # The lease must not leak into identity: two runs of the same work under
    # different lease settings are the same work.
    assert 'infer-stack' not in str(node.algo_id)
    assert 'infer-stack' not in str(node.process_id)


# --- The Slurm GPU allow-list ------------------------------------------------
#
# These assert on what a SHELL does with the rendered string, not on how the
# string is spelled. The property is that the flag is absent off Slurm and
# carries the job's own indices under it, and only a shell can demonstrate
# that: the value does not exist on the host that renders the command.

_STUB = '#!/bin/sh\nprintf "%s\\n" "$@"\n'


@pytest.fixture(scope='module')
def _stub_bin(tmp_path_factory):
    """A stand-in for `infer-stack` that reports its argv and runs nothing."""
    bindir = tmp_path_factory.mktemp('bin')
    stub = bindir / 'infer-stack'
    stub.write_text(_STUB)
    stub.chmod(0o755)
    return bindir


def _argv(command, _stub_bin, **slurm):
    """The argument list a shell actually hands `infer-stack run`."""
    import os
    import subprocess
    env = dict(os.environ, PATH=f'{_stub_bin}:{os.environ["PATH"]}')
    for name in ('SLURM_JOB_GPUS', 'SLURM_STEP_GPUS', 'CUDA_VISIBLE_DEVICES'):
        env.pop(name, None)
    env.update({k: v for k, v in slurm.items() if v is not None})
    proc = subprocess.run(['bash', '-c', command], env=env,
                          capture_output=True, text=True, check=True)
    return proc.stdout.split('\n')[:-1]


def _allowed_gpus(argv):
    """The value infer-stack would receive, or None if it never sees the flag."""
    for i, arg in enumerate(argv):
        if arg.startswith('--allowed_gpus='):
            return arg.split('=', 1)[1]
        if arg == '--allowed_gpus':
            return argv[i + 1]
    return None


@pytest.fixture
def _leased_command(monkeypatch):
    """A rendered command, with the render-time environment made hostile.

    Set here, these must not reach the string: the DAG is built on the submit
    host, where whatever Slurm variables the shell happens to hold describe
    some other allocation, or none.
    """
    monkeypatch.setenv('SLURM_JOB_GPUS', '7')
    monkeypatch.setenv('SLURM_STEP_GPUS', '7')
    node = _node(Infer, {'model_id': 'm', 'extractor_model_id': None})
    command = node.command
    assert '7' not in _prefix(command)
    return command


def test_no_allocation_no_allow_list(_leased_command, _stub_bin):
    """Under tmux there is no allocation to name, and the flag must vanish
    rather than arrive empty: `--allowed_gpus ''` is something infer-stack
    would have to interpret, where an absent flag is unambiguous."""
    argv = _argv(_leased_command, _stub_bin)
    assert _allowed_gpus(argv) is None
    assert not [a for a in argv if 'allowed_gpus' in a]
    assert '--endpoint' in argv


def test_the_job_gets_only_the_gpus_it_was_allocated(_leased_command, _stub_bin):
    argv = _argv(_leased_command, _stub_bin, SLURM_JOB_GPUS='0,1')
    assert _allowed_gpus(argv) == '0,1'
    # infer-stack parses each element with int(); anything else is a crash.
    assert [int(p) for p in _allowed_gpus(argv).split(',')] == [0, 1]


def test_a_step_allocation_counts_too(_leased_command, _stub_bin):
    """Not theoretical: measured under `srun` on aiq-gpu, SLURM_STEP_GPUS was
    set and SLURM_JOB_GPUS was not."""
    argv = _argv(_leased_command, _stub_bin, SLURM_STEP_GPUS='2,3')
    assert _allowed_gpus(argv) == '2,3'


def test_the_job_allocation_wins_over_the_step(_leased_command, _stub_bin):
    argv = _argv(_leased_command, _stub_bin,
                 SLURM_JOB_GPUS='0,1', SLURM_STEP_GPUS='2,3')
    assert _allowed_gpus(argv) == '0,1'
    # One flag, not two: a second occurrence would silently override.
    assert len([a for a in argv if 'allowed_gpus' in a]) == 1


def test_gpu_uuids_are_never_what_gets_passed(_leased_command, _stub_bin):
    """CUDA_VISIBLE_DEVICES may hold UUIDs, which int() cannot parse."""
    argv = _argv(_leased_command, _stub_bin,
                 CUDA_VISIBLE_DEVICES='GPU-4d888104-dead-beef-0000-000000000000')
    assert _allowed_gpus(argv) is None
    assert 'GPU-4d888104' not in ' '.join(argv)


def test_the_allow_list_can_be_switched_off(monkeypatch, _stub_bin):
    """For a site whose Slurm indices are not the runtime's indices."""
    from magnet.leasing import ALLOWED_GPUS_ENVVAR
    monkeypatch.setenv(ALLOWED_GPUS_ENVVAR, '0')
    command = _node(Infer, {'model_id': 'm', 'extractor_model_id': None}).command
    assert 'allowed_gpus' not in command
    assert _allowed_gpus(_argv(command, _stub_bin, SLURM_JOB_GPUS='0,1')) is None


@pytest.mark.parametrize('slurm', [{},
                                   {'SLURM_JOB_GPUS': '0,1'},
                                   {'SLURM_STEP_GPUS': '2,3'}])
def test_the_rendered_command_is_valid_shell(_leased_command, _stub_bin, slurm):
    """One rendered string has to be correct under both backends, so it is
    checked for syntax and then actually run -- under `set -u`, which is where
    a naive `$SLURM_JOB_GPUS` would abort the job instead of expanding."""
    import subprocess
    assert subprocess.run(['bash', '-n'], input=_leased_command,
                          text=True).returncode == 0
    _argv('set -u; ' + _leased_command, _stub_bin, **slurm)


def test_the_allow_list_is_expanded_at_job_time_not_render_time():
    """The constraint the whole design turns on: no allocation exists on the
    submit host, so the command can only carry shell text that resolves later.
    """
    from magnet.leasing import GPU_ALLOW_LIST_EXPANSION, slurm_gpu_allow_list
    assert slurm_gpu_allow_list() == GPU_ALLOW_LIST_EXPANSION
    assert '$' in GPU_ALLOW_LIST_EXPANSION
    assert 'CUDA_VISIBLE_DEVICES' not in GPU_ALLOW_LIST_EXPANSION


def test_the_expansion_survives_being_quoted_into_a_job_script(_leased_command,
                                                               _stub_bin,
                                                               tmp_path):
    """cmd_queue's Slurm backend ships the command as `sbatch --wrap <quoted>`.

    Those quotes are what DEFERS the expansion: the submit shell passes the
    characters through untouched into the job script, and the job's own shell
    is what resolves them, against the allocation it was actually given.
    """
    import shlex
    job = tmp_path / 'job.sh'
    submit = f"printf '%s\\n' {shlex.quote(_leased_command)} > {job}"
    _argv(f'{submit}; bash {job}', _stub_bin)  # nothing set: submit host
    assert '${SLURM_JOB_GPUS' in job.read_text()

    argv = _argv(f'{submit}; bash {job}', _stub_bin, SLURM_JOB_GPUS='0,1')
    assert _allowed_gpus(argv) == '0,1'
