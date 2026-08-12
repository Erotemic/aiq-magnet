"""
The bounded-mean DAG: fan out over seeds, gather into one artifact.

    sample[seed]      one job per seed
        | gather group_by=[] order_by=[seed]
    summarize         the terminal node the card reads
"""
import kwdagger

from magnet.examples.bounded_mean.sample import SampleConfig
from magnet.examples.bounded_mean.summarize import SummarizeConfig


class Sample(kwdagger.ProcessNode):
    name = 'sample'
    executable = 'python -m magnet.examples.bounded_mean.sample'
    params = SampleConfig

    def load_result(self, node_dpath):
        pass


class Summarize(kwdagger.ProcessNode):
    name = 'summarize'
    executable = 'python -m magnet.examples.bounded_mean.summarize'
    params = SummarizeConfig

    def load_result(self, node_dpath):
        pass


def bounded_mean_pipeline():
    nodes = {'sample': Sample(), 'summarize': Summarize()}
    nodes['sample'].outputs['out_fpath'].connect(
        nodes['summarize'].inputs['sample_fpaths'],
        gather=kwdagger.GatherSpec(group_by=[], order_by=['seed'], require='all_success'),
    )
    return kwdagger.Pipeline(list(nodes.values()))
