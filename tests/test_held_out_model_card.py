"""
The held-out-model card: a cohort, an endpoint, a DAG, and a theory ledger.

Slower than the rest of the suite because it stands up a mock inference server
and runs ten real jobs against it. That is the point -- this is the only test
that exercises inference, two levels of gather fan-in, leave-one-out over a
model cohort, and the theory accounting in one run, and every one of those
parts has unit tests that pass while the chain is broken.

The numbers asserted below are exact. Whether a model answers a question is a
hash of (seed, model, question id), so nothing here is sampled at run time and
a changed number means changed behaviour, not noise.
"""
import json
import shutil
from importlib.resources import files

import pytest
import ubelt as ub
import yaml


def _run_dpath(runs_root):
    """The card's run directory: ``<card hash>_<timestamp>``.

    ``_kwdagger`` sits beside it and holds the DAG's node artifacts, shared
    across card versions so an unchanged node is not recomputed when an
    unrelated part of the card changes. It is not a run directory.
    """
    import ubelt as ub
    return ub.Path(next(
        p for p in sorted(ub.Path(runs_root).iterdir())
        if p.is_dir() and not p.name.startswith('_')))



@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    """The card, plus the ledger its theory block names, in one directory."""
    from magnet.theory.audit import audit

    card_dpath = tmp_path_factory.mktemp('held_out_model') / 'cards'
    card_dpath.mkdir()
    source = files('magnet') / 'cards' / 'held_out_model.yaml'
    card = yaml.safe_load(source.read_text())

    example = files('magnet') / 'examples' / 'held_out_model'
    index = str(example / 'theory' / 'index.yaml')
    card['theory']['formalizations'] = [index]

    report = audit(sources=[str(example)], indexes=[index])
    assert report.issues == [], report.issues
    (card_dpath / 'theory-ledger.json').write_text(
        json.dumps(report.to_dict(), indent=2, default=str))

    card_fpath = card_dpath / 'held_out_model.yaml'
    card_fpath.write_text(yaml.safe_dump(card, sort_keys=False))
    return card_fpath


@pytest.fixture(scope='module')
def run_dpath(prepared, tmp_path_factory, request):
    """Run the card once; the assertions below read what it wrote."""
    if shutil.which('python') is None:
        pytest.skip('kwdagger nodes shell out to `python`')
    pytest.importorskip(
        'infer_stack',
        reason='infer-stack supplies the mock endpoint the cohort answers')

    from magnet.evaluation import EvaluationCard

    out_dpath = tmp_path_factory.mktemp('runs')
    # serial, not tmux: CI has no terminal to attach a monitor to.
    monkeypatch = pytest.MonkeyPatch()
    request.addfinalizer(monkeypatch.undo)
    monkeypatch.setenv('MAGNET_QUEUE_BACKEND', 'serial')

    card = EvaluationCard(prepared, out_dpath)
    assert card.evaluate() == 'VERIFIED'
    return _run_dpath(out_dpath)



def _result_values(run_dpath, node='holdout'):
    """The single cell's results, with the node qualification stripped."""
    cells = json.loads((run_dpath / 'result_cells.json').read_text())
    assert len(cells) == 1
    prefix = f'metrics.{node}.'
    return {key[len(prefix):]: value
            for key, value in cells[0]['results'].items()}

def test_the_cohort_was_actually_asked(run_dpath):
    """Ten jobs ran against the endpoint and fanned in twice."""
    results = _result_values(run_dpath)

    assert results['num_models'] == 3
    # Every model answered the same evaluation half, which is what makes the
    # cross-model comparison meaningful in the first place.
    assert set(results['eval_questions_per_model'].values()) == {813}
    assert sorted(p['model_id'] for p in results['predictions']) == [
        'mock/middling', 'mock/strong', 'mock/weak']


def test_the_baa_phase_one_metric(run_dpath):
    """Within 5%, on at least two models. Both halves of BAA Fig. 2."""
    results = _result_values(run_dpath)
    errors = {p['model_id']: p['abs_error'] for p in results['predictions']}

    assert results['num_within_tolerance'] >= 2
    assert results['max_abs_error'] <= 0.05

    # Exact, because the fixture is deterministic. The weak model is hardest
    # to carry across: it has the least headroom under the difficulty drift
    # the estimator is correcting for.
    assert errors['mock/strong'] == pytest.approx(0.0192, abs=1e-4)
    assert errors['mock/middling'] == pytest.approx(0.0222, abs=1e-4)
    assert errors['mock/weak'] == pytest.approx(0.0321, abs=1e-4)


def test_the_certified_limit_is_reported_next_to_the_estimate(run_dpath):
    """
    The TA1 half: what the bound licenses, not what the estimates happened to do.

    At 813 evaluation questions Hoeffding gives 0.0476 -- under the 5% the
    point estimates are scored against, but not by much. Shrink the pool and
    this is what fails first, which is the honest failure: the predictions
    could still be fine while nothing at all had been guaranteed.
    """
    results = _result_values(run_dpath)

    assert results['certified_halfwidth'] == pytest.approx(0.0476, abs=1e-4)
    assert results['certifies_tolerance'] is True
    # The empirical error sits inside the certified interval, as it must if
    # the bound is to mean anything here.
    assert results['max_abs_error'] < results['certified_halfwidth']


def test_the_result_says_what_it_is_standing_on(run_dpath):
    """theory.json, beside the verdict, with the gaps named."""
    theory = json.loads((run_dpath / 'theory.json').read_text())
    statements = {s['theorem'].split('.')[-1]: s for s in theory['coverage']['statements']}
    assert set(statements) == {'accuracy_mem_Icc', 'abs_heldOutError_le'}

    proved = statements['accuracy_mem_Icc']
    assert proved['proof'] == 'proved'
    assert proved['gaps'] == []
    assert sorted(e['hypothesis'].split('::')[-1] for e in proved['discharged']) == [
        'hn', 'hscore']

    # The bound the claim's certified half-width comes from is still `sorry`,
    # and the card reports that rather than quietly treating it as proved.
    unproved = statements['abs_heldOutError_le']
    assert unproved['proof'] == 'sorry'
    assert unproved['extra_axioms'] == ['sorryAx']
    assert sorted(e['hypothesis'].split('::')[-1] for e in unproved['discharged']) == [
        'hbdd', 'hdelta', 'hn', 'hsplit']

    # Two gaps, both high, and both real: independence of the scores, and
    # exchangeability of the held-out model with the cohort. The second is the
    # one that would break first on real architectures.
    gaps = {e['hypothesis'].split('::')[-1]: e for e in unproved['gaps']}
    assert sorted(gaps) == ['hexch', 'hiid']
    assert all(g['relation'] == 'assumes' for g in gaps.values())
    assert all(g['severity'] == 'high' for g in gaps.values())

    # Coverage is incomplete because the structural binders -- the score
    # function itself -- carry no edge. Structural binders are counted, not
    # excluded, so a card cannot reach `complete` by declaring its objects
    # uninteresting.
    assert theory['coverage']['complete'] is False
    for statement in statements.values():
        assert all(h['structural'] for h in statement['unaccounted'])


def test_the_card_is_shaped_like_the_baa_asks():
    """
    Fast, no DAG: the card's own framing, checked against the BAA.

    Guards the things that would quietly stop being true. A cohort of two
    makes leave-one-out degenerate; a smaller pool stops certifying 5%; a
    hypothesis name in the card means the theory split has leaked back in.
    """
    from magnet.examples.held_out_model import cohort

    card = yaml.safe_load(
        (files('magnet') / 'cards' / 'held_out_model.yaml').read_text())

    # "met on at least two models" needs a cohort of at least three, so that
    # leaving one out still leaves something to predict from.
    assert len(cohort.COHORT) >= 3
    assert card['symbols']['min_models_within']['value'] == 2
    assert card['kwdagger']['matrix']['holdout.tolerance'] == 0.05

    # The pool has to be big enough that the bound certifies the tolerance;
    # the halves are hashed, so neither is exactly half of it.
    smallest_half = min(
        sum(cohort.split_of(q) == half for q in cohort.question_ids())
        for half in ('cal', 'eval'))
    assert cohort.certified_halfwidth(smallest_half) <= 0.05

    # Relations live at the code that does the thing, never in the card.
    block = json.dumps(card['theory'])
    assert '::' not in block
    for binder in ('hexch', 'hiid', 'hbdd', 'hsplit', 'hscore', 'hdelta'):
        assert binder not in block
