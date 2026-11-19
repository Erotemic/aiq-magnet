"""Recipe -> independent measured symbols -> portable evaluation -> scoped analysis."""
import json
from importlib.resources import files
from pathlib import Path
import shutil
import sys

import pytest
import yaml

from magnet.evaluation_new import NewEvaluationRecipe
from magnet.results import export_result_bundle, load_evaluation_card, render_result, verify_result_bundle


SCRIPT = '''
import json, pathlib, sys
args = dict(a.lstrip('-').split('=', 1) for a in sys.argv[1:])
output = pathlib.Path(args['out'])
output.parent.mkdir(parents=True, exist_ok=True)
seed = int(args['seed'])
if args['mode'] == 'assessment':
    metrics = {'score': seed / 10}
else:
    table = [{'task': 'toy', 'model': 'a', 'score': seed / 10, 'seconds': None},
             {'task': 'toy', 'model': 'b', 'score': seed / 5, 'seconds': seed}]
    detail = output.with_suffix('.jsonl')
    detail.write_text(''.join(json.dumps(r) + '\\n' for r in table))
    metrics = {'table': table, 'details': str(detail), 'empty': [], 'flag': True, 'unknown': None}
output.write_text(json.dumps({'result': {'metrics': metrics}}))
'''


@pytest.fixture
def recipe_file(tmp_path):
    script = tmp_path / 'emit.py'
    script.write_text(SCRIPT)
    nodes = {name: {'executable': f'{sys.executable} {script}',
                    'algo_params': {'mode': mode, 'seed': 1},
                    'out_paths': {'out': 'result.json'}, 'primary_out_key': 'out'}
             for name, mode in [('assess', 'assessment'), ('observe', 'observation')]}
    card = {'name': 'measurement_demo', 'title': 'Measured quality and cost',
            'description': 'Synthetic plumbing fixture.', 'version': '1',
            'organizations': ['Example'], 'submitter': {'name': 'T', 'email': 't@example.com'},
            'tags': [], 'links': [], 'claim': {'python': 'assert assess.score < 1'},
            'kwdagger': {'pipeline': {'nodes': nodes}, 'result_node': 'assess',
                         'readout_nodes': ['observe'],
                         'matrix': {'assess.seed': [1, 2], 'observe.seed': [1, 2]}},
            'symbols': {'metrics.assess.score': {'metadata': {'display_name': 'Quality'}},
                        'metrics.observe.table': {'metadata': {'display_name': 'Quality and cost',
                            'columns': {'model': {'kind': 'model'}, 'seconds': {'unit': 's'},
                                        'unobserved': {'unit': 's'}}}},
                        'metrics.observe.details': {'metadata': {'format': 'jsonl', 'display': False,
                            'columns': {'model': {'kind': 'model'}, 'seconds': {'unit': 's'}}}},
                        **{f'metrics.observe.{name}': {'metadata': {'display_name': name}}
                           for name in ('flag', 'empty', 'unknown', 'absent')}}}
    path = tmp_path / 'recipe.yaml'
    path.write_text(yaml.safe_dump(card, sort_keys=False))
    return path


def run_recipe(path, root, *, params=None, readout_only=False):
    recipe = NewEvaluationRecipe(path, root)
    if params:
        recipe.apply_params(params)
    result = recipe.evaluate(backend='serial', print_commands=False, readout_only=readout_only)
    return result, load_evaluation_card(recipe.output_path / recipe._run_hash)


def test_independent_readout_symbols_tables_and_unknown_values(recipe_file, tmp_path):
    result, card = run_recipe(recipe_file, tmp_path / 'runs')
    assert result.result == 'VERIFIED'  # Existing assertion route remains compatible.
    assert card.evaluation['state'] == 'measured'
    assert len(card.cells) == 2
    assert len(card.evaluation['sources']['observe']['rows']) == 2
    for cell in card.cells.values():
        assert not any(k.startswith('metrics.observe.') for k in cell['symbols'])
    for row in card.evaluation['sources']['observe']['rows']:
        assert row['symbols']['metrics.observe.flag'] is True
        assert row['symbols']['metrics.observe.empty'] == []
        assert row['symbols']['metrics.observe.unknown'] is None
        assert row['missing_symbols'] == ['metrics.observe.absent']
    catalog = card.symbol_catalog()
    assert catalog['metrics.observe.table']['metadata']['columns']['seconds']['unit'] == 's'
    assert catalog['metrics.observe.unknown']['null'] == 2
    assert catalog['metrics.observe.unknown']['missing'] == 0
    assert catalog['metrics.observe.absent']['missing'] == 2
    table = card.table(node='observe', expand='metrics.observe.table', where={'model': 'b'},
                       columns=['model', 'score', 'seconds'])
    assert table.sort_values('score').to_dict('records') == [{'model': 'b', 'score': .2, 'seconds': 1},
                                       {'model': 'b', 'score': .4, 'seconds': 2}]
    assert 'Quality and cost' in render_result(card)
    assert 'seconds (s)' in render_result(card)
    assert 'Measurement state: **measured**' in render_result(card)
    assert 'VERIFIED' not in render_result(card)
    assert 'verdict' not in json.loads(render_result(card, format='json'))
    assert 'VERIFIED' in render_result(card, legacy_claims=True)


def test_measurement_only_recipe_requires_no_boolean_claim(recipe_file, tmp_path):
    data = yaml.safe_load(recipe_file.read_text())
    data.pop('claim')
    data['kwdagger'].pop('result_node')
    recipe_file.write_text(yaml.safe_dump(data))
    result, card = run_recipe(recipe_file, tmp_path / 'runs')
    assert result.result == 'MEASURED' and result.cell_results == []
    assert card.evaluation['state'] == 'measured'
    assert len(card.table(node='observe', expand='metrics.observe.table')) == 4
    assert '## Claim' not in render_result(card)


def test_readout_scope_and_cached_identity_are_independent_of_claim_rows(recipe_file, tmp_path):
    _, before = run_recipe(recipe_file, tmp_path / 'runs')
    artifacts = list((tmp_path / 'runs' / '_kwdagger').glob('*/*/result.json'))
    times = {p: p.stat().st_mtime_ns for p in artifacts}
    data = yaml.safe_load(recipe_file.read_text())
    data['evidence'] = {'scope': 'requested'}
    recipe_file.write_text(yaml.safe_dump(data))
    _, after = run_recipe(recipe_file, tmp_path / 'runs', params={
        'matrix': {'assess.seed': [2], 'observe.seed': [1]}}, readout_only=True)
    assert len(after.cells) == 1
    rows = after.table(node='observe', expand='metrics.observe.table')
    assert sorted(rows['score']) == [.1, .2]
    assert len(after.evaluation['sources']['observe']['rows']) == 1
    assert times == {p: p.stat().st_mtime_ns for p in artifacts}


def test_portable_evaluation_and_file_tables_need_no_original_pipeline(recipe_file, tmp_path):
    _, native = run_recipe(recipe_file, tmp_path / 'runs')
    output = export_result_bundle(native.root, tmp_path / 'portable', supporting='terminal')
    shutil.rmtree(tmp_path / 'runs')
    recipe_file.unlink()
    (tmp_path / 'emit.py').unlink()
    card = load_evaluation_card(output)
    assert verify_result_bundle(output)['valid']
    assert len(card.table(node='observe', expand='metrics.observe.details')) == 4
    assert len(card.table(node='observe', expand='metrics.observe.table')) == 4
    assert card.symbol_catalog()['metrics.observe.details']['metadata']['format'] == 'jsonl'
    assert set(card.table(node='observe')['_record_id']) == {native.evaluation['record_id']}


def test_native_table_cli_preserves_selection_and_missing_denominators(recipe_file, tmp_path, capsys):
    from magnet.cli.main import MagnetCLI
    _, card = run_recipe(recipe_file, tmp_path / 'runs')
    capsys.readouterr()
    assert MagnetCLI.main(argv=['results', 'table', str(card.root), '--node=observe',
                                '--expand=metrics.observe.table', '--group_by=model',
                                '--aggregate={score: mean, seconds: mean, unobserved: mean}']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['selection']['group_by'] == ['model']
    a, b = output['rows']
    assert a['model'] == 'a' and a['score.mean'] == pytest.approx(.15)
    assert a['seconds.mean'] is None and a['seconds.missing'] == 2
    assert a['unobserved.mean'] is None and a['unobserved.missing'] == 2
    assert b['seconds.mean'] == 1.5 and b['seconds.observed'] == 2
    assert MagnetCLI.main(argv=['results', 'symbols', str(card.root)]) == 0
    assert 'metrics.observe.table' in json.loads(capsys.readouterr().out)
    empty = card.table(node='observe', expand='metrics.observe.table', where={'model': 'missing'})
    assert empty.empty and 'seconds' in empty.columns


def test_variants_preserve_separate_measurement_rows(recipe_file, tmp_path):
    from magnet.results_collection import combine_result_bundles
    _, a = run_recipe(recipe_file, tmp_path / 'a', params={
        'matrix': {'assess.seed': [1], 'observe.seed': [1]}})
    _, b = run_recipe(recipe_file, tmp_path / 'b', params={
        'matrix': {'assess.seed': [2], 'observe.seed': [2]}})
    output = combine_result_bundles({'small': a.root, 'large': b.root}, tmp_path / 'combined')
    card = load_evaluation_card(output)
    assert verify_result_bundle(output)['valid']
    rows = card.table(node='observe', expand='metrics.observe.details', where={'_variant': 'large'})
    assert sorted(rows['score']) == [.2, .4]
    assert set(rows['_source_record']) == {str(b.root)}
    assert set(rows['_source_record_id']) == {b.evaluation['record_id']}


def test_missing_published_evaluation_is_not_a_valid_export(recipe_file, tmp_path):
    _, card = run_recipe(recipe_file, tmp_path / 'runs')
    (card.root / 'evaluation.json').unlink()
    assert not verify_result_bundle(card.root)['valid']


def test_packaged_measurement_example_and_relative_output_paths(tmp_path, monkeypatch):
    data = yaml.safe_load((files('magnet') / 'examples/measurements/card.yaml').read_text())
    data['kwdagger']['pipeline']['nodes']['measure']['executable'] = (
        f'{sys.executable} -m magnet.examples.measurements.experiment')
    path = tmp_path / 'example.yaml'
    path.write_text(yaml.safe_dump(data))
    monkeypatch.chdir(tmp_path)
    _, card = run_recipe(path, 'out')
    assert card.evaluation['state'] == 'measured'
    assert len(card.table(node='measure', expand='metrics.measure.quality')) == 6
    assert len(card.table(node='measure', expand='metrics.measure.answers_file',
                          columns=['question_id', 'scores.strong_signal'])) == 600
    assert all(Path(r['artifact']).is_absolute() for r in card.evaluation['sources']['measure']['rows'])
    artifacts = list((tmp_path / 'out' / '_kwdagger').glob('*/*/measurements.json'))
    times = {p: p.stat().st_mtime_ns for p in artifacts}
    data['symbols']['metrics.measure.quality']['metadata']['description'] = 'A revised interpretation.'
    path.write_text(yaml.safe_dump(data))
    run_recipe(path, 'out')
    assert times == {p: p.stat().st_mtime_ns for p in artifacts}


def test_historical_resolved_symbols_remain_readable_as_a_table(recipe_file, tmp_path):
    _, card = run_recipe(recipe_file, tmp_path / 'runs')
    (card.root / 'evaluation.json').unlink()
    verdict = json.loads((card.root / 'verdict.json').read_text())
    verdict.pop('measurements')
    (card.root / 'verdict.json').write_text(json.dumps(verdict))
    historical = load_evaluation_card(card.root)
    assert len(historical.table(node='assess')) == 2
    assert historical.symbol_catalog()['metrics.assess.score']['observations'] == 2


def test_declared_inherited_columns_remain_published_without_joining_sources():
    from magnet.evaluation_new import _fill_declared_symbols
    from magnet.measurements import materialize_readout
    symbols = {'resolved_params.predict.model': {'metadata': {'kind': 'model'}},
               'metrics.unrelated.value': {'metadata': {'unit': 's'}}}
    result = materialize_readout('summary', [{'key': 'summary1', 'artifact': None,
        'row': {'resolved_params.predict.model': 'model-a'}}], symbols, bind=_fill_declared_symbols)
    assert result['rows'][0]['symbols'] == {'resolved_params.predict.model': 'model-a'}
