"""
End to end, on nothing but MAGNET: a kwdagger DAG whose result reports what it
is standing on.

Slower than a unit test because it schedules and runs real jobs, which is the
point -- the parts this covers (gather fan-in, terminal_node, the audit, the
ledger, theory.json) have unit tests that all pass while the chain is broken.
"""
import json
import shutil
from importlib.resources import files

import pytest
import ubelt as ub
import yaml


@pytest.fixture
def prepared(tmp_path):
    """The card, plus the ledger its theory block names, in one directory."""
    from magnet.theory.audit import audit

    card_dpath = tmp_path / 'cards'
    card_dpath.mkdir()
    source = files('magnet') / 'cards' / 'bounded_mean.yaml'
    card = yaml.safe_load(source.read_text())

    # The theory block's paths are relative to the card. Point the
    # formalization at the installed hygiene library and the ledger at the one
    # generated below.
    hygiene = str(files('magnet') / 'theory' / 'data' / 'hygiene.yaml')
    card['theory']['formalizations'] = [hygiene]

    report = audit(
        sources=[str(files('magnet') / 'examples' / 'bounded_mean')],
        indexes=[hygiene],
    )
    (card_dpath / 'theory-ledger.json').write_text(
        json.dumps(report.to_dict(), indent=2, default=str)
    )

    card_fpath = card_dpath / 'bounded_mean.yaml'
    card_fpath.write_text(yaml.safe_dump(card, sort_keys=False))
    return card_fpath


@pytest.mark.skipif(shutil.which('python') is None, reason='kwdagger nodes shell out to `python`')
def test_the_dag_runs_and_reports_what_it_assumes(prepared, tmp_path, monkeypatch):
    from magnet.evaluation import EvaluationCard

    # serial, not tmux: CI has no terminal to attach a monitor to.
    monkeypatch.setenv('MAGNET_QUEUE_BACKEND', 'serial')

    card = EvaluationCard(prepared, tmp_path / 'runs')
    assert card.evaluate() == 'VERIFIED'

    run_dpath = ub.Path(next(iter((tmp_path / 'runs').iterdir())))

    # The DAG really ran: five sample jobs fanned into one terminal artifact.
    terminal = json.loads((run_dpath / 'terminal_result.json').read_text())
    assert terminal['num_samples'] == 5
    assert terminal['draws_per_sample'] == 256

    # And the result says what it is standing on. One hypothesis discharged,
    # one relaxed, one nobody addressed -- the shape the machinery exists for.
    theory = json.loads((run_dpath / 'theory.json').read_text())
    (statement,) = theory['coverage']['statements']
    assert statement['theorem'] == 'Hygiene.Concentration.mean_within_tolerance'
    assert [e['hypothesis'].split('::')[-1] for e in statement['discharged']] == ['hbdd']
    assert [e['hypothesis'].split('::')[-1] for e in statement['gaps']] == ['hn']
    assert [h['name'] for h in statement['unaccounted']] == ['hiid']
    assert not theory['coverage']['complete']

    # The files the runner has always written are still there.
    assert (run_dpath / 'verdict.json').exists()
    assert (run_dpath / 'card.yaml').exists()


def test_the_edges_live_in_the_node_code_not_the_card():
    # The card names one theorem and no hypotheses; every relation below comes
    # from an annotation at the code that does it.
    card = yaml.safe_load((files('magnet') / 'cards' / 'bounded_mean.yaml').read_text())
    declared = card['theory']['grounds']

    assert len(declared) == 1
    assert 'hbdd' not in json.dumps(card['theory'])
    assert 'hn' not in [g.get('declaration') for g in declared]
