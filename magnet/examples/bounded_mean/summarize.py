#!/usr/bin/env python3
"""
Pool every sample mean into the card's one terminal artifact.

The gather edge hands this node a *manifest* -- one sample path per line --
rather than the paths themselves, so the command line stays a fixed size
however wide the fan-out is.
"""
import json

import kwconf


class SummarizeConfig(kwconf.Config):
    sample_fpaths: str = kwconf.Value(
        None, help='gather manifest: one sample path per line', tags=['in_path']
    )
    out_fpath: str = kwconf.Value(
        'summary.json', help='where to write the summary', tags=['out_path', 'primary']
    )


def main(argv=None, **kwargs):
    config = SummarizeConfig.cli(argv=argv, data=kwargs, strict=True)
    with open(config['sample_fpaths']) as file:
        manifest = [line.strip() for line in file if line.strip()]

    samples = []
    for fpath in manifest:
        with open(fpath) as file:
            samples.append(json.load(file))
    means = [s['mean'] for s in samples]
    payload = {
        'pooled_mean': sum(means) / len(means),
        'num_samples': len(means),
        'draws_per_sample': samples[0]['size'],
    }
    with open(config['out_fpath'], 'w') as file:
        json.dump(payload, file, indent=2)


__cli__ = SummarizeConfig

if __name__ == '__main__':
    main()
