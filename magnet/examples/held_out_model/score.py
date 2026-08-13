#!/usr/bin/env python3
"""
Fan one model's shards back in and split its scores into the two halves.

One job per model. The calibration half is what the held-out model is allowed
to be observed on; the evaluation half is the "non-evaluated scenario" the
prediction has to reach.
"""
import json

import kwconf

from magnet.examples.held_out_model import cohort
from magnet.theory import satisfies


class ScoreConfig(kwconf.Config):
    model_id: str = kwconf.Value(
        'mock/strong', help='the model these shards belong to')
    shard_fpaths: str = kwconf.Value(
        None, help='gather manifest: one shard path per line', tags=['in_path'])
    out_fpath: str = kwconf.Value(
        'model_score.json', help='per-half accuracy for this model',
        tags=['out_path', 'primary'])


# Both halves come out of one pool by a hash of the question id, so neither is
# drawn from a different distribution than the other -- which is what the
# statement means by the two halves being comparable. The pool is fixed and
# non-empty, so the counts are positive.
@satisfies(
    'MagnetExample.HeldOutModel.accuracy_mem_Icc::hn',
    informal='the pool is non-empty and every question lands in exactly one half',
)
@satisfies(
    'MagnetExample.HeldOutModel.abs_heldOutError_le::hsplit',
    informal='both halves are hashed out of the same pool, so they share a distribution',
)
def split_scores(scores):
    """
    Partition per-question scores into the calibration and evaluation halves.

    Args:
        scores (dict[str, float]): question id -> 0.0 or 1.0.

    Returns:
        dict[str, list[float]]: keyed ``'cal'`` and ``'eval'``.

    Example:
        >>> from magnet.examples.held_out_model.score import split_scores
        >>> from magnet.examples.held_out_model import cohort
        >>> scores = {qid: 1.0 for qid in cohort.question_ids()[:20]}
        >>> halves = split_scores(scores)
        >>> assert len(halves['cal']) + len(halves['eval']) == 20
    """
    halves = {'cal': [], 'eval': []}
    for qid, value in scores.items():
        halves[cohort.split_of(qid)].append(value)
    return halves


def main(argv=None, **kwargs):
    config = ScoreConfig.cli(argv=argv, data=kwargs, strict=True)
    with open(config['shard_fpaths']) as file:
        manifest = [line.strip() for line in file if line.strip()]

    scores = {}
    for fpath in manifest:
        with open(fpath) as file:
            scores.update(json.load(file)['scores'])

    halves = split_scores(scores)
    payload = {
        'model_id': config['model_id'],
        'num_questions': len(scores),
        'accuracy': {half: sum(v) / len(v) for half, v in halves.items()},
        'num_questions_per_half': {half: len(v) for half, v in halves.items()},
    }
    with open(config['out_fpath'], 'w') as file:
        json.dump(payload, file, indent=2)


__cli__ = ScoreConfig

if __name__ == '__main__':
    main()
