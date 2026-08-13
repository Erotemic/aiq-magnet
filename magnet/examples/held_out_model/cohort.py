"""
The fixture the held-out-model card runs against: a cohort and a question pool.

Everything here is derived from one seed, so the card is deterministic and the
test can assert exact numbers. The endpoint is
:class:`infer_stack.mockserver.server.MockServer`, the same deterministic
OpenAI-compatible server the TA1 dry runs rehearse against -- so the nodes
exercise a real HTTP client, real request shapes and real scoring, without a
GPU, an API key or a network.

Two properties of that simulator are what make the card's claim meaningful
rather than vacuous:

*Difficulty is shared across models.* A question that is hard is hard for
everyone, so model scores are correlated and a cohort-level estimate has
something to learn. With independent errors the leave-one-model-out prediction
would be measuring a true null.

*Responses are deterministic at temperature 0.* Whether a model answers a
question correctly is a fixed function of (seed, model, question). The only
sampling left is which questions land in the calibration half and which in the
evaluation half, which is exactly the randomness the concentration bound in
``theory/HeldOutModel.lean`` is about.

Warning:
    Numbers from this card describe the simulator. They say nothing about any
    real model, and must never be staged as an evaluation result.
"""
import hashlib
import os

from magnet.theory import satisfies

#: Seed for the simulator, the question pool and the split. One knob.
SEED = 'magnet-held-out-model'

#: Ability is the simulator's competence knob, and it is *not* the accuracy
#: that comes out -- shared per-question difficulty pulls the realized scores
#: down. Spread wide enough that a cohort estimate has to track the model
#: rather than regress everyone to one number.
COHORT = {
    'mock/strong': 0.85,
    'mock/middling': 0.62,
    'mock/weak': 0.38,
}

#: Questions in the pool. Sized so the evaluation half is large enough for the
#: Hoeffding half-width to come in under the 5% the BAA asks for; see
#: :func:`certified_halfwidth`.
POOL_SIZE = 1600

#: Base URL of an already-running endpoint. Unset => each node stands up its
#: own in-process mock. infer-stack's ``run`` exports the same variable name
#: pair, so a leased endpoint needs no other wiring.
BASE_URL_ENVVAR = 'MAGNET_MOCK_BASE_URL'
API_KEY_ENVVAR = 'MAGNET_MOCK_API_KEY'


def question_ids():
    """
    Every question id in the pool, in a fixed order.

    Returns:
        list[str]
    """
    return [f'q{i:05d}' for i in range(POOL_SIZE)]


def question_text(qid):
    """
    The prompt for one question.

    The id is embedded verbatim because the simulator identifies which latent
    question a prompt is about by substring match, so the marker has to survive
    into the prompt.

    Args:
        qid (str): a question id.

    Returns:
        str
    """
    return f'[{qid}] Which of the four options is correct? Answer with the option id.'


def gold_answer(qid):
    """
    The answer key entry for one question.

    Args:
        qid (str): a question id.

    Returns:
        str
    """
    return f'ans-{qid}'


def split_of(qid):
    """
    Which half a question falls in: ``'cal'`` or ``'eval'``.

    Hashed rather than sliced by index so the halves are interleaved through
    the pool. A contiguous split would put every low-id question on one side,
    and since ids also seed the per-question difficulty, the two halves would
    differ systematically -- the estimator would then be correcting for an
    artifact of the split rather than for real difficulty drift.

    Args:
        qid (str): a question id.

    Returns:
        str
    """
    digest = hashlib.blake2b(qid.encode('utf-8'), digest_size=8).hexdigest()
    return 'cal' if int(digest, 16) % 2 == 0 else 'eval'


def shard_of(qid, num_shards):
    """
    Which shard answers a question.

    Args:
        qid (str): a question id.
        num_shards (int): how many shards the pool is cut into.

    Returns:
        int
    """
    return int(qid[1:]) % num_shards


def mock_config(qids=None):
    """
    The ``MockServer`` config for this cohort and pool.

    Args:
        qids (Sequence[str] | None): register only these questions. Whether a
            model answers a question correctly is a hash of
            ``(seed, model, question id)`` and difficulty is a hash of
            ``(seed, question id)``, so narrowing the registered set changes no
            answer -- it only shortens the prompt-to-question match the server
            does per request. Defaults to the whole pool.

    Returns:
        dict
    """
    qids = list(question_ids() if qids is None else qids)
    return {
        'seed': SEED,
        'models': {name: {'ability': ability} for name, ability in COHORT.items()},
        'questions': {qid: question_text(qid) for qid in qids},
        'answer_key': {qid: gold_answer(qid) for qid in qids},
    }


def endpoint(qids=None):
    """
    A context manager yielding the base URL to ask questions of.

    Reuses an endpoint named by :data:`BASE_URL_ENVVAR` when one is set --
    which is how a leased or containerized run would supply it -- and
    otherwise stands up an in-process mock for the life of the node.

    Args:
        qids (Sequence[str] | None): questions to register, when standing up
            our own server. See :func:`mock_config`.

    Returns:
        contextlib.AbstractContextManager[str]
    """
    import contextlib

    configured = os.environ.get(BASE_URL_ENVVAR, '').strip()
    if configured:
        return contextlib.nullcontext(configured.rstrip('/'))

    from infer_stack.mockserver.server import MockServer

    @contextlib.contextmanager
    def _own_server():
        with MockServer(mock_config(qids), port=0) as server:
            yield server.url.rstrip('/')

    return _own_server()


@satisfies(
    'MagnetExample.HeldOutModel.abs_heldOutError_le::hn',
    informal='the half-width is computed at the realized evaluation count, and '
             'a zero count would not survive the division',
)
@satisfies(
    'MagnetExample.HeldOutModel.abs_heldOutError_le::hdelta',
    informal='delta is a confidence level in (0, 1); the card fixes it at 0.05',
)
def certified_halfwidth(num_questions, delta=0.05):
    """
    The Hoeffding half-width for a mean of ``num_questions`` bounded scores.

    This is the *limit* side of the BAA's "within 5% error or limits for 95%
    success": the largest deviation the theory rules out at confidence
    ``1 - delta``. It is computed here, in Python, from the statement in
    ``theory/HeldOutModel.lean`` -- the Lean file is where the inequality is
    written down and where its hypotheses are named.

    Args:
        num_questions (int): size of the evaluation half.
        delta (float): failure probability the bound is allowed.

    Returns:
        float

    Example:
        >>> from magnet.examples.held_out_model.cohort import certified_halfwidth
        >>> # The pool is sized so the evaluation half certifies 5%.
        >>> assert certified_halfwidth(813) < 0.05
        >>> assert certified_halfwidth(100) > 0.05
    """
    import math

    return math.sqrt(math.log(2.0 / delta) / (2.0 * num_questions))
