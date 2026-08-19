"""
Per-cell result nodes, and evidence recorded beside the verdict.
"""

import json

import pytest

from magnet.evaluation import EvaluationCard, KWDaggerProcessor


CELLS = [
    {
        'coords': {'dataset': 'alpha'},
        'results': {'metrics.summarize.mae': 0.1},
        'artifact': '/nowhere/alpha/out.json',
    },
    {
        'coords': {'dataset': 'beta'},
        'results': {'metrics.summarize.mae': 0.9},
        'artifact': '/nowhere/beta/out.json',
    },
]


@pytest.fixture
def two_cells(monkeypatch):
    monkeypatch.setattr(
        KWDaggerProcessor, 'collect_result_cells', lambda self: CELLS
    )


def _run(tmp_path, card_text):
    card_fpath = tmp_path / 'card.yaml'
    card_fpath.write_text(card_text)
    card = EvaluationCard(card_fpath, tmp_path / 'results', validate='off')
    status = card.evaluate()
    run_dpath = next((tmp_path / 'results').glob('*'))
    return status, run_dpath


PER_CELL_CARD = """
claim:
  python: |
    assert metrics.summarize.mae < 0.5, f"{dataset}: {metrics.summarize.mae}"
kwdagger:
  pipeline: 'example.pipeline()'
  result_node: summarize
"""


def test_each_cell_gets_its_own_verdict(tmp_path, two_cells):
    status, run_dpath = _run(tmp_path, PER_CELL_CARD)

    verdicts = sorted((run_dpath / 'results').glob('*/verdict.json'))
    assert len(verdicts) == 2, 'one verdict directory per cell'

    by_dataset = {}
    for fpath in verdicts:
        record = json.loads(fpath.read_text())
        by_dataset[record['symbols']['dataset']] = record

    # The coordinate is a symbol, which is what separates the two hashes.
    assert set(by_dataset) == {'alpha', 'beta'}
    assert by_dataset['alpha']['status'] == 'VERIFIED'
    assert by_dataset['beta']['status'] == 'FALSIFIED'

    # Qualified results are reachable from the claim but stay out of the
    # symbols that identify the cell.
    assert by_dataset['alpha']['consumed'] == ['metrics.summarize.mae']
    assert 'metrics.summarize.mae' not in by_dataset['alpha']['symbols']

    # 'all' is the default sufficiency, and one cell falsified.
    assert status == 'FALSIFIED'


EVIDENCE_ONLY_CARD = """
evidence:
  - measures: metrics.summarize.mae
    relation: {lt: 0.5}
    supports: Example.someLemma
    relaxes: "measured on two datasets only"
kwdagger:
  pipeline: 'example.pipeline()'
  result_node: summarize
"""


def test_evidence_decides_a_card_with_no_claim(tmp_path, two_cells):
    status, run_dpath = _run(tmp_path, EVIDENCE_ONLY_CARD)

    assert status == 'FALSIFIED'

    evidence = json.loads((run_dpath / 'evidence.json').read_text())
    assert evidence['result'] == status
    assert evidence['sufficiency'] == {'type': 'all'}

    held = {
        cell['symbols']['dataset']: cell['evidence'][0]
        for cell in evidence['cells']
    }
    assert held['alpha']['held'] is True
    assert held['beta']['held'] is False
    assert held['alpha']['value'] == 0.1
    # What the measurement is evidence for is carried through unread.
    assert held['alpha']['supports'] == 'Example.someLemma'
    assert held['beta']['relaxes'] == 'measured on two datasets only'

    # The verdict stays readable on its own, beside the evidence.
    verdict = json.loads((run_dpath / 'verdict.json').read_text())
    assert verdict['result'] == status


ANY_SUFFICIENCY_CARD = EVIDENCE_ONLY_CARD + """
claim_aggregation_strategy:
  type: any
"""


def test_sufficiency_reuses_the_aggregation_strategy(tmp_path, two_cells):
    status, run_dpath = _run(tmp_path, ANY_SUFFICIENCY_CARD)

    # Same evidence, different definition of sufficient.
    assert status == 'VERIFIED'
    evidence = json.loads((run_dpath / 'evidence.json').read_text())
    assert evidence['sufficiency'] == {'type': 'any'}


MISSING_MEASURE_CARD = """
evidence:
  - measures: metrics.summarize.absent
    relation: {lt: 0.5}
kwdagger:
  pipeline: 'example.pipeline()'
  result_node: summarize
"""


def test_an_unmeasurable_item_is_inconclusive_and_says_why(tmp_path, two_cells):
    status, run_dpath = _run(tmp_path, MISSING_MEASURE_CARD)

    assert status == 'INCONCLUSIVE'
    evidence = json.loads((run_dpath / 'evidence.json').read_text())
    record = evidence['cells'][0]['evidence'][0]
    assert record['held'] is None
    assert 'available: mae' in record['error']
