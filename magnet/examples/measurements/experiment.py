"""Measure two synthetic correctness signals; no model inference or Boolean claim."""
import argparse
import json
from pathlib import Path
import random
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--samples', type=int, default=200)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    labels = [int(rng.random() < .65) for _ in range(args.samples)]
    qualities, timings, scores = [], [], {}
    for method, noise in [('weak_signal', .8), ('strong_signal', .3)]:
        started = perf_counter()
        score = [max(0, min(1, .2 + .6 * y + rng.gauss(0, noise))) for y in labels]
        positive = [p for p, y in zip(score, labels) if y]
        negative = [p for p, y in zip(score, labels) if not y]
        auc = (sum(float(a > b) + .5 * (a == b) for a in positive for b in negative)
               / (len(positive) * len(negative))) if positive and negative else None
        qualities.append({'method': method, 'samples': len(labels), 'correct': sum(labels),
                          'auroc': auc, 'brier_score': sum((p - y) ** 2 for p, y in zip(score, labels)) / len(labels)})
        timings.append({'method': method, 'analysis_wall_seconds': perf_counter() - started})
        scores[method] = score
    args.out.parent.mkdir(parents=True, exist_ok=True)
    answers = args.out.with_suffix('.answers.jsonl')
    with answers.open('w') as stream:
        for index, label in enumerate(labels):
            stream.write(json.dumps({'question_id': f'{args.seed}:{index}', 'correct': label,
                                     'scores': {method: values[index] for method, values in scores.items()}}) + '\n')
    args.out.write_text(json.dumps({'result': {'metrics': {
        'quality': qualities, 'timing': timings, 'answers_file': str(answers.absolute())}}}))


if __name__ == '__main__':
    main()
