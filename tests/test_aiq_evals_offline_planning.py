"""The real scheduling path must work with no target evaluation environment."""
import importlib
import subprocess

import pytest

from magnet._kwdagger import KWDaggerProcessor
from magnet.containers import ContainerSettings
from magnet.leasing import LeaseSettings


@pytest.mark.parametrize('containerized', [False, True])
def test_evaluation_schedule_is_offline_even_when_not_a_dry_run(
    tmp_path, monkeypatch, containerized
):
    from cmd_queue.cli_boilerplate import CmdQueueConfigMixin

    from magnet.backends.aiq_evals import pipeline, serving

    def forbidden(*args, **kwargs):
        raise AssertionError('planning must not execute target work or inspect serving state')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(pipeline, 'run_preflight', forbidden)
    monkeypatch.setattr(serving, 'catalog_provenance', forbidden)
    import_module = importlib.import_module

    def without_native_runtime(name, package=None):
        if name.split('.')[0] in {'magnet_evals', 'inspect_ai', 'inspect_evals', 'helm', 'torch'}:
            raise AssertionError('native runtimes are unavailable on the planning machine')
        return import_module(name, package)

    monkeypatch.setattr(importlib, 'import_module', without_native_runtime)
    executions = []

    def stop_at_execution_boundary(self, queue, **kwargs):
        # Backend execution is explicitly requested by dry_run=False. Stop
        # there, after real planning, instead of actually launching jobs.
        executions.append(queue)
        queue.write()

    monkeypatch.setattr(CmdQueueConfigMixin, 'run_queue', stop_at_execution_boundary)
    config = {
        'readout_nodes': ['evaluate'],
        'pipeline': {'nodes': {'evaluate': {
            'class': 'magnet.backends.aiq_evals.EvaluationNode',
            'algo_params': {
                'engine': 'inspect_ai', 'task': 'python:missing_native_package:task',
                'models': [{'role': 'primary', 'model': 'remote-alias', 'provider': 'openai'}],
            },
            'perf_params': {'worker_python': '/target-only/native/bin/python',
                            'endpoint': 'remote-alias'},
        }}},
        'matrix': {'evaluate.task_options': [{'item': 1}, {'item': 2}]},
    }
    processor = KWDaggerProcessor(config, tmp_path / 'out')
    processor.schedule(
        backend='tmux', tmux_workers=2, dry_run=False,
        lease_settings=LeaseSettings(enabled=True),
        container_settings=ContainerSettings(image='target-only/native:latest')
            if containerized else ContainerSettings(),
    )
    assert executions == [processor.queue]
    assert len(processor.request_dag.nodes) == 2
    for node in processor.request_dag.nodes.values():
        assert node.final_algo_config['measurement_identity'] is None
        assert node.final_algo_config['endpoint_provenance'] is None
        assert '/target-only/native/bin/python' in node.command
        assert 'runtime_request_file' in node.command
        assert 'infer-stack run' in node.command
    processor.queue.write()
