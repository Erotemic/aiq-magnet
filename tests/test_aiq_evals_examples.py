"""The shipped aiq-magnet-evals recipes (integration plan M7).

Static checks run everywhere. End-to-end runs need the engine workers named in
``aiq_evals_support`` and run every recipe through ``evaluate_new``.
"""
import importlib.resources
import json
import os

import pytest
import yaml
from aiq_evals_support import (
    HELM_PYTHON,
    INSPECT_PYTHON,
    OLMO_PYTHON,
    evaluate,
    latest_run_dir,
    needs_helm,
    needs_inspect,
    needs_olmo,
    run_verdicts,
)

RECIPES = ('helm_generation', 'inspect_generation', 'inspect_agent', 'olmo_generation',
           'olmo_agent', 'mixed_engines')


def test_verified_selection_prepares_independent_measurement_nodes(tmp_path, monkeypatch):
    from aiq_evals_support import repo, require_magnet_evals
    require_magnet_evals()
    from magnet.backends.aiq_evals import pipeline
    from magnet.examples.aiq_evals.swebench import main

    def forbidden(*args, **kwargs):
        raise AssertionError('dry-run must not resolve or start serving')
    monkeypatch.setattr(pipeline, 'run_preflight', forbidden)
    build_request = pipeline.build_request_dict
    seen = set()
    def checked_request(config):
        data = build_request(config)
        options = data['task_options']
        assert len(options['instance_ids']) == 1
        assert set(options['images']) == set(options['instance_ids'])
        seen.update(options['instance_ids'])
        return data
    monkeypatch.setattr(pipeline, 'build_request_dict', checked_request)
    source = repo() / 'dev/environments'
    selection = json.loads((source / 'swebench-verified-full-selection.json').read_text())
    selection['instance_ids'] = selection['instance_ids'][:2]
    selected = tmp_path / 'selection.json'
    selected.write_text(json.dumps(selection))
    path = tmp_path / 'recipe.yaml'
    assert main(['--endpoint', 'coding', '--worker_python', '/host/python',
        '--selection', str(selected), '--image_pins', str(source / 'swebench-verified-full-images.json'),
        '--message_limit', '200', '--agent_protocol', 'minimal-edit/v1', '--lease_ttl', '30d',
        '--lease_timeout', '7200', '--timeout_seconds', '21600', '--output', str(path)]) == 0
    card = yaml.safe_load(path.read_text())
    assert 'claim' not in card
    assert card['kwdagger']['readout_nodes'] == ['evaluate']
    node = card['kwdagger']['pipeline']['nodes']['evaluate']
    assert node['lease_ttl'] == '30d' and node['lease_timeout'] == 7200
    assert node['perf_params']['timeout_seconds'] == 21600
    shards = card['kwdagger']['matrix']['evaluate.task_options']
    assert len(shards) == 2
    assert [s['instance_ids'][0] for s in shards] == selection['instance_ids']
    assert all(set(s['images']) == set(s['instance_ids']) for s in shards)
    assert card['kwdagger']['pipeline']['nodes']['evaluate']['algo_params'][
        'engine_options']['eval_options']['message_limit'] == 200
    evaluate(path, tmp_path / 'out', dry_run=True)
    assert seen == set(selection['instance_ids'])


@needs_inspect
def test_sharded_recipe_retains_zero_and_resumes_only_the_failed_request(tmp_path, monkeypatch):
    from aiq_evals_support import require_magnet_evals, store_attempts

    from magnet._kwdagger import KWDaggerProcessor
    require_magnet_evals()
    from magnet_evals import EvaluationRequest, ModelBinding

    from magnet.examples.aiq_evals.swebench import sharded_recipe
    from magnet.results import load_result_bundle

    module = tmp_path / 'shard_native_probe.py'
    module.write_text('''from pathlib import Path
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match
from inspect_ai.solver import generate
from magnet_evals.examples import inspect_tasks
@task
def selected(item, counts):
    path = Path(counts)
    old = path.read_text() if path.exists() else ''
    path.write_text(old + item + '\\n')
    if item == 'retry' and 'retry' not in old.splitlines():
        raise RuntimeError('synthetic infrastructure failure')
    return Task(dataset=[Sample(id=item, input='Two plus two?', target='0' if item == 'zero' else '4')],
                solver=generate(), scorer=match())
''')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    counts = tmp_path / 'calls.txt'
    requests = [EvaluationRequest(engine='inspect_ai',
        task='python:shard_native_probe:selected', task_revision='test-v1', data_revision='test-v1',
        models=(ModelBinding(role='primary', model='local', provider='aiq_example', revision='test-v1'),),
        task_options={'item': item, 'counts': str(counts)},
        engine_options={'registration_modules': ['magnet_evals.examples.inspect_tasks']})
        for item in ('ok', 'zero', 'retry')]
    card = sharded_recipe(requests, name='sharded_native', endpoint='unused',
        worker_python=INSPECT_PYTHON, select={'scorer': 'match', 'metric': 'accuracy'})
    perf = card['kwdagger']['pipeline']['nodes']['evaluate']['perf_params']
    perf.update(endpoint=None, verify_serving_provenance=False)
    path = tmp_path / 'recipe.yaml'
    path.write_text(yaml.safe_dump(card, sort_keys=False))
    out = tmp_path / 'out'
    first, _ = evaluate(path, out, print_commands=False)
    rows = KWDaggerProcessor(first.kwdagger, root_dpath=first.kwdagger_dpath).load_available_result_rows(node='evaluate')
    usable = [r['row']['metrics.evaluate.score'] for r in rows if r['row'].get('metrics.evaluate.eligible')]
    assert sorted(usable) == [0., 1.]
    initial_calls = counts.read_text().splitlines()
    second, _ = evaluate(path, out, print_commands=False)
    assert counts.read_text().splitlines() == initial_calls + ['retry']
    rows = KWDaggerProcessor(second.kwdagger, root_dpath=second.kwdagger_dpath).load_available_result_rows(node='evaluate')
    usable = [r['row']['metrics.evaluate.score'] for r in rows if r['row'].get('metrics.evaluate.eligible')]
    assert sorted(usable) == [0., 1., 1.]
    bundle = load_result_bundle(second.output_path / second._run_hash)
    values = bundle.table(node='evaluate')['metrics.evaluate.score'].tolist()
    assert sorted(values) == [0., 1., 1.]
    assert len(store_attempts(out)) == 4


def recipe_path(name):
    return importlib.resources.files('magnet') / 'examples' / 'aiq_evals' / f'{name}.yaml'


def test_recipes_ship_with_the_package():
    folder = importlib.resources.files('magnet') / 'examples' / 'aiq_evals'
    shipped = {p.name for p in folder.iterdir() if p.name.endswith(('.yaml', '.md'))}
    assert shipped == {f'{name}.yaml' for name in RECIPES} | {'README.md', 'SWEBENCH.md'}


@pytest.mark.parametrize('kind', ['verified', 'pro', 'harbor'])
def test_coding_smoke_recipe_prepares_and_compiles_without_resolution(tmp_path, monkeypatch, kind):
    from aiq_evals_support import repo, require_magnet_evals

    require_magnet_evals()
    from magnet.backends.aiq_evals import pipeline
    from magnet.examples.aiq_evals.swebench import main

    def forbidden(*args, **kwargs):
        raise AssertionError('dry-run must not resolve engines or acquire a lease')

    monkeypatch.setattr(pipeline, 'run_preflight', forbidden)
    path = tmp_path / 'recipe.yaml'
    pins = tmp_path / 'pins.json'
    from magnet_evals.benchmarks.swe_bench_pro import SMOKE_TASK

    pins.write_text(json.dumps({SMOKE_TASK: 'sha256:' + 'a' * 64}))
    assert main(['--kind', kind, '--endpoint', 'coding', '--worker_python', '/container-only/python',
                 '--evals_root', str(repo()), '--output', str(path), '--task_path', str(tmp_path / 'task'),
                 '--pro_root', str(tmp_path / 'pro'), '--image_pins', str(pins)]) == 0
    _, card = evaluate(path, tmp_path / 'out', dry_run=True)
    assert card.result == 'NOT_EVALUATED'
    config = yaml.safe_load(path.read_text())['kwdagger']['pipeline']['nodes']['evaluate']
    assert config['perf_params']['verify_serving_provenance']
    assert config['algo_params']['engine'] == ('inspect_ai' if kind == 'verified' else 'harbor')


@pytest.mark.parametrize('name', RECIPES)
def test_recipes_use_installed_example_modules(name):
    text = recipe_path(name).read_text()
    assert 'tests.' not in text  # no aiq-magnet-evals source checkout needed
    card = yaml.safe_load(text)
    assert [link['url'] for link in card['links']] == ['https://github.com/Erotemic/aiq-magnet-evals']
    assert card['kwdagger']['result_node'] == 'evaluate'


@pytest.mark.parametrize('name', RECIPES)
def test_recipes_compile_in_a_dry_run_without_engines(name, tmp_path, monkeypatch):
    from magnet.backends.aiq_evals import pipeline

    def forbidden(*args, **kwargs):
        raise AssertionError('a dry run must not resolve')

    monkeypatch.setattr(pipeline, 'run_preflight', forbidden)
    _, card = evaluate(recipe_path(name), tmp_path / 'out', dry_run=True)
    assert card.result == 'NOT_EVALUATED'


def _run(name, tmp_path, workers):
    params = {'matrix': {f'{node}.worker_python': python for node, python in workers.items()}}
    return _run_with(name, tmp_path, params)


def _run_with(name, tmp_path, params):
    out = tmp_path / 'out'
    _, card = evaluate(recipe_path(name), out, params=params)
    verdicts = run_verdicts(latest_run_dir(out))
    return card, verdicts


@needs_helm
def test_helm_generation_recipe(tmp_path):
    card, verdicts = _run('helm_generation', tmp_path, {'evaluate': HELM_PYTHON})
    assert card.result == 'VERIFIED' and len(verdicts) == 1


@needs_inspect
@pytest.mark.parametrize('name', ['inspect_generation', 'inspect_agent'])
def test_inspect_recipes(name, tmp_path):
    card, verdicts = _run(name, tmp_path, {'evaluate': INSPECT_PYTHON})
    assert card.result == 'VERIFIED', verdicts
    assert verdicts[0]['symbols']['metrics.evaluate.score'] == 1.0


@needs_olmo
def test_olmo_generation_recipe(tmp_path):
    card, verdicts = _run('olmo_generation', tmp_path, {'evaluate': OLMO_PYTHON})
    assert card.result == 'VERIFIED', verdicts


@needs_olmo
def test_olmo_agent_recipe_against_the_example_endpoint(tmp_path, monkeypatch):
    from magnet_evals.examples.chat_server import chat_server

    card_data = yaml.safe_load(recipe_path('olmo_agent').read_text())
    models = card_data['kwdagger']['pipeline']['nodes']['evaluate']['algo_params']['models']
    monkeypatch.setenv('OPENAI_API_KEY', 'example-local-key')
    with chat_server() as port:
        models[0]['provider_options']['base_url'] = f'http://127.0.0.1:{port}/v1'
        params = {
            'pipeline': {'nodes': {'evaluate': {'algo_params': {'models': models}}}},
            'matrix': {'evaluate.worker_python': OLMO_PYTHON},
        }
        card, verdicts = _run_with('olmo_agent', tmp_path, params)
    assert card.result == 'VERIFIED', verdicts
    for path in (tmp_path / 'out').rglob('*.json'):
        assert 'example-local-key' not in path.read_text(errors='ignore'), path


@needs_helm
@needs_inspect
def test_mixed_engine_comparison_recipe(tmp_path):
    card, verdicts = _run('mixed_engines', tmp_path, {'helm': HELM_PYTHON, 'inspect': INSPECT_PYTHON})
    assert card.result == 'VERIFIED', verdicts
    (verdict,) = verdicts
    symbols = verdict['symbols']
    assert symbols['metrics.evaluate.comparable'] is True
    # Two engines, two distinct measurement identities: no cross-engine collision.
    evidence = verdict['evidence']
    identities = {evidence[f'metrics.evaluate.{side}.measurement_identity'] for side in ('left', 'right')}
    engines = {evidence[f'metrics.evaluate.{side}.engine'] for side in ('left', 'right')}
    assert len(identities) == 2 and engines == {'helm', 'inspect_ai'}
    comparison = next((tmp_path / 'out' / '_kwdagger' / 'evaluate').glob('*/comparison.json'))
    assert json.loads(comparison.read_text())['mapping'].startswith('both are exact-match')
