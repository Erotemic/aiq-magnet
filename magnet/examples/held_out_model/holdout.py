#!/usr/bin/env python3
"""
Leave one model out, predict its accuracy on questions it was not scored on.

The card's result artifact. For each model in turn: hide its evaluation
half, predict that accuracy from its calibration half plus what the *rest* of
the cohort did on both halves, then compare against the truth.

This is the BAA's Phase-1 experimental design -- leave-k-out over the cohort,
"given an evaluation result, accurately predict performance on non-evaluated
scenarios" -- and both of its metrics come out of it. The point estimate is
scored against the 5% figure; the Hoeffding half-width is the limit the theory
certifies at this sample size.
"""
import json

import kwconf

from magnet.examples.held_out_model import cohort
from magnet.theory import assumes


class HoldoutConfig(kwconf.Config):
    score_fpaths: str = kwconf.Value(
        None, help='gather manifest: one per-model score path per line',
        tags=['in_path'])
    tolerance: float = kwconf.Value(
        0.05, help='the BAA Phase-1 figure the point estimate is scored against')
    delta: float = kwconf.Value(
        0.05, help='failure probability the certified limit is allowed')
    out_fpath: str = kwconf.Value(
        'holdout.json', help='the result artifact the card reads',
        tags=['out_path', 'primary'])


# The estimator carries the held-out model's calibration accuracy across to
# the evaluation half, rescaled by how much easier or harder that half was for
# everyone else. That transfer is licensed only if the held-out model is
# exchangeable with the cohort it is predicted from. Three simulated models
# sharing one difficulty function are exchangeable by construction; real
# architectures are the open question, and this is the edge that says so.
@assumes(
    'MagnetExample.HeldOutModel.abs_heldOutError_le::hexch',
    'high',
    informal='the cohort is three simulated models over one shared difficulty '
             'function; nothing here bears on exchangeability across real '
             'architectures',
)
def predict_held_out(held_out, accuracies):
    """
    Predict one model's evaluation accuracy without looking at it.

    Args:
        held_out (str): the model being predicted.
        accuracies (dict[str, dict[str, float]]): model -> half -> accuracy.

    Returns:
        float

    Example:
        >>> from magnet.examples.held_out_model.holdout import predict_held_out
        >>> acc = {'a': {'cal': 0.8, 'eval': 0.8},
        ...        'b': {'cal': 0.5, 'eval': 0.4},
        ...        'c': {'cal': 0.5, 'eval': 0.4}}
        >>> # The rest of the cohort lost a fifth going across; so does 'a'.
        >>> assert abs(predict_held_out('a', acc) - 0.64) < 1e-9
    """
    others = [m for m in accuracies if m != held_out]
    if not others:
        raise ValueError('leave-one-out needs at least two models in the cohort')
    rest_cal = sum(accuracies[m]['cal'] for m in others) / len(others)
    rest_eval = sum(accuracies[m]['eval'] for m in others) / len(others)
    if rest_cal <= 0.0:
        raise ValueError('the rest of the cohort scored zero on calibration')
    return accuracies[held_out]['cal'] * (rest_eval / rest_cal)


def main(argv=None, **kwargs):
    config = HoldoutConfig.cli(argv=argv, data=kwargs, strict=True)
    with open(config['score_fpaths']) as file:
        manifest = [line.strip() for line in file if line.strip()]

    per_model = {}
    for fpath in manifest:
        with open(fpath) as file:
            record = json.load(file)
        per_model[record['model_id']] = record

    accuracies = {m: r['accuracy'] for m, r in per_model.items()}
    eval_sizes = {m: r['num_questions_per_half']['eval'] for m, r in per_model.items()}

    predictions = []
    for held_out in sorted(accuracies):
        predicted = predict_held_out(held_out, accuracies)
        actual = accuracies[held_out]['eval']
        predictions.append({
            'model_id': held_out,
            'predicted': predicted,
            'actual': actual,
            'abs_error': abs(predicted - actual),
            'within_tolerance': abs(predicted - actual) <= config['tolerance'],
        })

    smallest_eval = min(eval_sizes.values())
    halfwidth = cohort.certified_halfwidth(smallest_eval, delta=config['delta'])
    payload = {
        'num_models': len(accuracies),
        'tolerance': config['tolerance'],
        'delta': config['delta'],
        'predictions': predictions,
        'max_abs_error': max(p['abs_error'] for p in predictions),
        'num_within_tolerance': sum(p['within_tolerance'] for p in predictions),
        # The TA1 side: what the bound in HeldOutModel.lean rules out at this
        # sample size, and whether that is already tight enough to certify the
        # figure the point estimates are being scored against.
        'certified_halfwidth': halfwidth,
        'certifies_tolerance': halfwidth <= config['tolerance'],
        'eval_questions_per_model': eval_sizes,
    }
    with open(config['out_fpath'], 'w') as file:
        json.dump(payload, file, indent=2)


__cli__ = HoldoutConfig

if __name__ == '__main__':
    main()
