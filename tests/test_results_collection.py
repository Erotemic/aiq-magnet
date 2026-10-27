"""One card, named parameter variants, and a portable combined readout."""
import json
import shutil

import pytest
import yaml

from magnet.results import (
    export_result_bundle,
    load_result_bundle,
    render_result,
    verify_result_bundle,
)
from magnet.results_collection import combine_result_bundles


def make_result(root, name, score, status='VERIFIED'):
    root.mkdir()
    key = 'summary_' + name
    artifact = root / 'measurement.json'
    artifact.write_text(json.dumps({'score': score}))
    metadata = {'kind': 'metric', 'display': True, 'display_name': 'Score',
                'define_metric': {'objective': 'maximize', 'aggregation_strategy': {'type': 'mean'}}}
    card = {'name': 'same_question', 'title': 'Does the predictor transfer?',
            'description': 'Shared scientific scope.',
            'claim': {'python': "raise RuntimeError('do not execute while combining')"},
            'symbols': {'metrics.summary.score': {'metadata': metadata}},
            'kwdagger': {'pipeline': 'not_installed:factory', 'result_node': 'summary',
                        'matrix': {'predict.model': [name]}}}
    (root / 'card.yaml').write_text(yaml.safe_dump(card))
    (root / 'symbol_metadata.json').write_text(json.dumps({'metrics.summary.score': metadata}))
    (root / 'verdict.json').write_text(json.dumps({'result': status, 'claims': [key],
                                                 'claim_aggregation_strategy': {'type': 'all'}}))
    cellpath = root / 'results' / key / 'verdict.json'
    cellpath.parent.mkdir(parents=True)
    cellpath.write_text(json.dumps({'status': status, 'cell': key, 'artifact': str(artifact),
                                   'symbols': {'metrics.summary.score': score},
                                   'evidence': {'metrics.summary.score': score}}))
    return root


def test_named_variants_have_one_card_and_report_after_sources_are_removed(tmp_path):
    baseline = make_result(tmp_path / 'baseline', 'small', .8)
    (baseline / 'symbol_metadata.json').unlink()  # Older native records still declare metadata in the card.
    expanded = make_result(tmp_path / 'expanded', 'large', .9)
    output = combine_result_bundles({'Baseline': baseline, 'Expanded': expanded},
                                   tmp_path / 'combined', notes='The gain persists with more models.')
    shutil.rmtree(baseline)
    shutil.rmtree(expanded)
    stored = load_result_bundle(output)
    assert stored.verdict['result'] == 'VERIFIED'
    assert stored.verdict['metrics']['Score'] == pytest.approx(.85)
    assert len(stored.cells) == 2
    assert stored.variants['Expanded']['kwdagger']['matrix']['predict.model'] == ['large']
    report = render_result(stored)
    assert '| Baseline | VERIFIED | 0.8 |' in report
    assert '| Expanded | VERIFIED | 0.9 |' in report
    assert 'The gain persists with more models.' in report
    assert '## Claim' not in report
    assert '## Claim' in render_result(stored, details=True)
    assert len(list(output.rglob('report.md'))) == 1
    assert verify_result_bundle(output)['valid']
    for cell in stored.cells.values():
        assert stored.artifact_path(cell['artifact']).is_file()
    again = export_result_bundle(output, tmp_path / 'again', supporting='requested')
    assert render_result(load_result_bundle(again)) == report
    assert verify_result_bundle(again)['valid']


def test_falsified_variant_is_preserved_in_aggregate(tmp_path):
    a = make_result(tmp_path / 'a', 'a', .8)
    b = make_result(tmp_path / 'b', 'b', .2, 'FALSIFIED')
    output = combine_result_bundles({'A': a, 'B': b}, tmp_path / 'out')
    assert load_result_bundle(output).verdict['result'] == 'FALSIFIED'


def test_adding_a_variant_preserves_existing_labels_and_overrides(tmp_path):
    a = make_result(tmp_path / 'a', 'a', .8)
    b = make_result(tmp_path / 'b', 'b', .9)
    combined = combine_result_bundles({'A': a, 'B': b}, tmp_path / 'combined')
    c = make_result(tmp_path / 'c', 'c', 1.0)
    result = combine_result_bundles({'Previous': combined, 'C': c}, tmp_path / 'next')
    stored = load_result_bundle(result)
    assert set(stored.variants) == {'A', 'B', 'C'}
    assert stored.variants['B']['kwdagger']['matrix']['predict.model'] == ['b']
    assert stored.verdict['metrics']['Score'] == pytest.approx(.9)


def test_different_claims_cannot_be_presented_as_variants_of_one_card(tmp_path):
    a = make_result(tmp_path / 'a', 'a', .8)
    b = make_result(tmp_path / 'b', 'b', .9)
    path = b / 'card.yaml'
    card = yaml.safe_load(path.read_text())
    card['claim'] = {'python': 'assert False'}
    path.write_text(yaml.safe_dump(card))
    with pytest.raises(ValueError, match='incompatible card field'):
        combine_result_bundles({'A': a, 'B': b}, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_cli_combines_and_shows_the_whole_variant_table(tmp_path, capsys):
    from magnet.cli.main import MagnetCLI
    a = make_result(tmp_path / 'a', 'a', .8)
    b = make_result(tmp_path / 'b', 'b', .9)
    sources = tmp_path / 'variants.yaml'
    sources.write_text(yaml.safe_dump({'A': str(a), 'B': str(b)}))
    output = tmp_path / 'combined'
    assert MagnetCLI.main(argv=['results', 'combine', str(sources), str(output)]) == 0
    assert MagnetCLI.main(argv=['results', 'show', str(output)]) == 0
    text = capsys.readouterr().out
    assert '| A | VERIFIED | 0.8 |' in text
    assert '| B | VERIFIED | 0.9 |' in text
