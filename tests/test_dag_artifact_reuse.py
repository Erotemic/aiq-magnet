"""
A card change must not recompute the cells it did not change.

kwdagger identifies a node by hashing its own configuration, so an unchanged
node keeps its id when a different part of the card changes. Rooting the DAG
under a hash of the WHOLE card threw that away: adding one model to a 13-model
cohort moved all 48 unchanged shards to a new path, ``test -e <artifact>``
missed on every one, and two hours of unchanged work was recomputed.
"""
import shutil
from importlib.resources import files

import pytest
import ubelt as ub
import yaml

from magnet.evaluation import EvaluationCard


def _card_with_seeds(tmp_path, seeds):
    """The shipped bounded-mean card, with its seed sweep replaced."""
    card = yaml.safe_load((files('magnet') / 'cards' / 'bounded_mean.yaml').read_text())
    card['kwdagger']['matrix']['sample.seed'] = list(seeds)
    card.pop('theory', None)  # needs a ledger; irrelevant to artifact reuse
    fpath = ub.Path(tmp_path) / 'card.yaml'
    fpath.write_text(yaml.safe_dump(card, sort_keys=False))
    return fpath


def _artifacts(root):
    """relative path -> mtime for every NODE artifact under the DAG root.

    Skips ``_``-prefixed directories: ``_kwdagger_schedule`` is the scheduler's
    own bookkeeping and is rewritten every run by design, so it says nothing
    about whether a node was recomputed.
    """
    root = ub.Path(root)
    return {
        p.relative_to(root): p.stat().st_mtime
        for p in root.glob('**/*.json')
        if p.is_file() and not any(
            part.startswith('_') for part in p.relative_to(root).parts)
    }


@pytest.mark.skipif(shutil.which('python') is None,
                    reason='kwdagger nodes shell out to `python`')
def test_adding_a_cell_reuses_the_cells_that_did_not_change(tmp_path):
    out = ub.Path(tmp_path) / 'runs'

    card = EvaluationCard(_card_with_seeds(tmp_path, [1, 2]), output_path=out)
    card.evaluate()
    dag_root = card.dag_root_dpath
    assert dag_root.name == '_kwdagger'
    before = _artifacts(dag_root)
    assert before, 'the first run produced no artifacts'

    # Same two seeds plus a third: the first two cells are configured
    # identically, so their node ids do not move.
    card2 = EvaluationCard(_card_with_seeds(tmp_path, [1, 2, 3]), output_path=out)
    card2.evaluate()

    # A different card version gets its own provenance directory ...
    run_dirs = [p.name for p in sorted(out.iterdir())
                if p.is_dir() and not p.name.startswith('_')]
    assert len(run_dirs) == 2, run_dirs

    # ... but shares the DAG root, and leaves the earlier artifacts untouched.
    assert card2.dag_root_dpath == dag_root
    after = _artifacts(dag_root)
    for rel, mtime in before.items():
        assert rel in after, f'{rel} was orphaned by the card change'
        assert after[rel] == mtime, f'{rel} was recomputed but did not change'
    assert len(after) > len(before), 'the third seed produced nothing'


@pytest.mark.skipif(shutil.which('python') is None,
                    reason='kwdagger nodes shell out to `python`')
def test_a_changed_cell_is_not_reused(tmp_path):
    """The other half: reuse must not survive a change that matters."""
    out = ub.Path(tmp_path) / 'runs'

    card = EvaluationCard(_card_with_seeds(tmp_path, [1]), output_path=out)
    card.evaluate()
    before = set(_artifacts(card.dag_root_dpath))

    # A different seed is a different computation, so a different node id.
    card2 = EvaluationCard(_card_with_seeds(tmp_path, [7]), output_path=out)
    card2.evaluate()
    after = set(_artifacts(card2.dag_root_dpath))

    assert after - before, 'a changed cell reused a stale artifact'
