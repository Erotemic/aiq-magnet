"""
End to end, on nothing but MAGNET: a kwdagger DAG whose result reports what it
is standing on.

Slower than a unit test because it schedules and runs real jobs, which is the
point -- the parts this covers (gather fan-in, result_node, the audit, the
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

    # The theory block's paths are relative to the card. Point them at the
    # installed index and the ledger generated below.
    index = str(files('magnet') / 'examples' / 'bounded_mean' / 'theory' / 'index.yaml')
    card['theory']['formalizations'] = [index]

    report = audit(
        sources=[str(files('magnet') / 'examples' / 'bounded_mean')],
        indexes=[index],
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

    # The DAG really ran: five sample jobs fanned into one result artifact.
    cells = json.loads((run_dpath / 'result_cells.json').read_text())
    assert len(cells) == 1, 'group_by=[] gathers everything into one cell'
    results = cells[0]['results']
    assert results['metrics.summarize.num_samples'] == 5
    assert results['metrics.summarize.draws_per_sample'] == 256

    # And the result says what it is standing on.
    theory = json.loads((run_dpath / 'theory.json').read_text())
    statements = {s['theorem'].split('.')[-1]: s for s in theory['coverage']['statements']}
    assert set(statements) == {'mean_mem_Icc', 'abs_sampleMean_sub_mean_le'}

    # The proved statement is fully discharged: bounded draws, positive size.
    proved = statements['mean_mem_Icc']
    assert proved['proof'] == 'proved'
    assert sorted(e['hypothesis'].split('::')[-1] for e in proved['discharged']) == [
        'hhi', 'hlo', 'hn'
    ]

    # The one still `sorry` is where the experiment departs, and the card is
    # standing on it either way.
    unproved = statements['abs_sampleMean_sub_mean_le']
    assert unproved['proof'] == 'sorry'
    assert unproved['extra_axioms'] == ['sorryAx']
    assert [e['hypothesis'].split('::')[-1] for e in unproved['gaps']] == ['hiid']
    assert sorted(h['name'] for h in unproved['unaccounted']) == ['hn', 'hrange']
    assert not theory['coverage']['complete']

    # The files the runner has always written are still there.
    assert (run_dpath / 'verdict.json').exists()
    assert (run_dpath / 'card.yaml').exists()


def test_the_card_names_theorems_and_no_hypotheses():
    # Every relation in the report comes from an annotation at the code that
    # does it. If a binder name ever appears in the card, the split has leaked.
    card = yaml.safe_load((files('magnet') / 'cards' / 'bounded_mean.yaml').read_text())
    block = json.dumps(card['theory'])

    assert [g['declaration'].split('.')[-1] for g in card['theory']['grounds']] == [
        'mean_mem_Icc',
        'abs_sampleMean_sub_mean_le',
    ]
    assert '::' not in block
    for binder in ('hlo', 'hhi', 'hiid', 'hbdd', 'hrange'):
        assert binder not in block
