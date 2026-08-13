#!/usr/bin/env python3
"""
Ask one model one shard of the pool, and score each answer against the key.

One job per (model_id, shard_index). This is the only node that touches an
endpoint; everything downstream is arithmetic over what it writes.
"""
import json
import urllib.error
import urllib.request

import kwconf

from magnet.examples.held_out_model import cohort
from magnet.theory import assumes, satisfies


class AnswerConfig(kwconf.Config):
    model_id: str = kwconf.Value(
        'mock/strong', help='which cohort model answers this shard')
    shard_index: int = kwconf.Value(0, help='which shard of the pool')
    num_shards: int = kwconf.Value(2, help='how many shards the pool is cut into')
    out_fpath: str = kwconf.Value(
        'answers.json', help='per-question scores for this shard',
        tags=['out_path', 'primary'])


# Scoring is exact match against the answer key, so a score is 0 or 1 and
# nothing else -- which is the whole of what the boundedness hypotheses ask
# for. Both statements need it, and it is discharged the same way for both.
@satisfies(
    'MagnetExample.HeldOutModel.accuracy_mem_Icc::hscore',
    informal='exact match against the answer key, so every score is 0 or 1',
)
@satisfies(
    'MagnetExample.HeldOutModel.abs_heldOutError_le::hbdd',
    informal='the same 0/1 scoring',
)
# The concentration bound treats the evaluation half as an independent draw
# from the pool. The questions are generated independently, but they are also
# *scored by one simulator whose difficulty is shared across models*, and
# nothing here establishes that the resulting scores are independent.
@assumes(
    'MagnetExample.HeldOutModel.abs_heldOutError_le::hiid',
    'high',
    informal='questions are generated independently, but per-question '
             'difficulty is shared and nothing here tests independence of scores',
)
def score_one(base_url, model_id, qid, api_key=None):
    """
    Ask one question and return 1.0 if the answer matches the key.

    Args:
        base_url (str): OpenAI-compatible endpoint, up to and including ``/v1``
            being appended here.
        model_id (str): the model to ask.
        qid (str): the question id.
        api_key (str | None): bearer token, when the endpoint wants one.

    Returns:
        float: 1.0 or 0.0.
    """
    payload = {
        'model': model_id,
        'temperature': 0.0,
        'messages': [{'role': 'user', 'content': cohort.question_text(qid)}],
    }
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    request = urllib.request.Request(
        base_url + '/v1/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
    )
    with urllib.request.urlopen(request) as response:
        body = json.loads(response.read())
    text = body['choices'][0]['message']['content'].strip()
    return float(text == cohort.gold_answer(qid))


def main(argv=None, **kwargs):
    import os

    config = AnswerConfig.cli(argv=argv, data=kwargs, strict=True)
    api_key = os.environ.get(cohort.API_KEY_ENVVAR) or None

    mine = [qid for qid in cohort.question_ids()
            if cohort.shard_of(qid, config['num_shards']) == config['shard_index']]

    scores = {}
    with cohort.endpoint(mine) as base_url:
        for qid in mine:
            scores[qid] = score_one(base_url, config['model_id'], qid, api_key)

    payload = {
        'model_id': config['model_id'],
        'shard_index': config['shard_index'],
        'scores': scores,
    }
    with open(config['out_fpath'], 'w') as file:
        json.dump(payload, file, indent=2)


__cli__ = AnswerConfig

if __name__ == '__main__':
    main()
