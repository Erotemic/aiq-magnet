#!/usr/bin/env python3
"""
Draw from a bounded population and write the sample mean.

One job per seed. Nothing here reads the network or a dataset: the point is a
DAG that is cheap enough to run in CI while having the same shape as a real
card -- fan out, gather, one terminal artifact.
"""
import json
import random

import kwconf

from magnet.theory import approximates, satisfies

#: Bounded by construction, which is the one hypothesis this example
#: discharges rather than merely assuming.
POPULATION = tuple(range(101))
POPULATION_MEAN = sum(POPULATION) / len(POPULATION)


class SampleConfig(kwconf.Config):
    seed: int = kwconf.Value(0, help='RNG seed; one job per value')
    size: int = kwconf.Value(64, help='draws per job')
    out_fpath: str = kwconf.Value(
        'sample.json', help='where to write the sample mean', tags=['out_path', 'primary']
    )


@satisfies(
    'Hygiene.Concentration.mean_within_tolerance::hbdd',
    informal='the population is the bounded integer range [0, 100]',
)
@approximates(
    'Hygiene.Concentration.mean_within_tolerance::hn',
    'medium',
    informal='a fixed sample size, where the statement is asymptotic in n',
)
def draw(seed: int, size: int) -> float:
    rng = random.Random(seed)
    return sum(rng.choice(POPULATION) for _ in range(size)) / size


def main(argv=None, **kwargs):
    config = SampleConfig.cli(argv=argv, data=kwargs, strict=True)
    payload = {
        'seed': config['seed'],
        'size': config['size'],
        'mean': draw(config['seed'], config['size']),
    }
    with open(config['out_fpath'], 'w') as file:
        json.dump(payload, file, indent=2)


__cli__ = SampleConfig

if __name__ == '__main__':
    main()
