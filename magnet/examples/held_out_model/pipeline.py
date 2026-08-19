"""
The held-out-model DAG: two levels of gather over a model cohort.

    answer[model_id, shard_index]     one endpoint job per model per shard
        |  gather group_by=[model_id] order_by=[shard_index]
    score[model_id]                   one model's halves
        |  gather group_by=[] order_by=[model_id]
    holdout                           the result node the card reads

Two levels rather than one because the cohort fan-in is the part that matters:
a missing model would silently shrink the leave-one-out estimate, so the
cohort edge requires every one.
"""
import kwdagger

from magnet.examples.held_out_model.answer import AnswerConfig
from magnet.examples.held_out_model.holdout import HoldoutConfig
from magnet.examples.held_out_model.score import ScoreConfig


class Answer(kwdagger.ProcessNode):
    name = 'answer'
    executable = 'python -m magnet.examples.held_out_model.answer'
    params = AnswerConfig

    def load_result(self, node_dpath):
        pass


class Score(kwdagger.ProcessNode):
    name = 'score'
    executable = 'python -m magnet.examples.held_out_model.score'
    params = ScoreConfig

    def load_result(self, node_dpath):
        pass


class Holdout(kwdagger.ProcessNode):
    name = 'holdout'
    executable = 'python -m magnet.examples.held_out_model.holdout'
    params = HoldoutConfig

    def load_result(self, node_dpath):
        pass


def held_out_model_pipeline():
    """
    Build the pipeline.

    Returns:
        kwdagger.Pipeline

    Example:
        >>> from magnet.examples.held_out_model.pipeline import held_out_model_pipeline
        >>> dag = held_out_model_pipeline()
        >>> assert len(dag.gather_connections) == 2
    """
    nodes = {'answer': Answer(), 'score': Score(), 'holdout': Holdout()}

    # A model's shards fan in. Ordering by shard index keeps the merged score
    # set independent of what the scheduler happened to finish first.
    nodes['answer'].outputs['out_fpath'].connect(
        nodes['score'].inputs['shard_fpaths'],
        gather=kwdagger.GatherSpec(
            group_by=['model_id'], order_by=['shard_index'], require='all_success'),
    )

    # The cohort fans in.
    nodes['score'].outputs['out_fpath'].connect(
        nodes['holdout'].inputs['score_fpaths'],
        gather=kwdagger.GatherSpec(
            group_by=[], order_by=['model_id'], require='all_success'),
    )

    dag = kwdagger.Pipeline(list(nodes.values()))
    dag.build_nx_graphs()
    return dag
