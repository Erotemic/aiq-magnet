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

from magnet.theory import approximates, assumes, satisfies

#: Bounded by construction, which is what discharges `hlo`/`hhi`/`hbdd`.
POPULATION = tuple(range(101))
POPULATION_MEAN = sum(POPULATION) / len(POPULATION)


class SampleConfig(kwconf.Config):
    seed: int = kwconf.Value(0, help='RNG seed; one job per value')
    size: int = kwconf.Value(64, help='draws per job')
    out_fpath: str = kwconf.Value(
        'sample.json', help='where to write the sample mean', tags=['out_path', 'primary']
    )


# `mean_mem_Icc` is discharged outright: the draws come from a bounded range
# and the size is positive, which is everything the statement asks for.
@satisfies(
    'MagnetExample.BoundedMean.mean_mem_Icc::hlo',
    informal='draws come from range(101), so every one is at least 0',
)
@satisfies(
    'MagnetExample.BoundedMean.mean_mem_Icc::hhi',
    informal='draws come from range(101), so every one is at most 100',
)
@satisfies(
    'MagnetExample.BoundedMean.mean_mem_Icc::hn',
    informal='the card runs a positive number of draws per job',
)
# The concentration statement is where the experiment actually departs.
@satisfies(
    'MagnetExample.BoundedMean.abs_sampleMean_sub_mean_le::hbdd',
    informal='the same bounded range',
)
@assumes(
    'MagnetExample.BoundedMean.abs_sampleMean_sub_mean_le::hiid',
    'high',
    informal='random.Random is a PRNG; nothing here tests independence',
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
