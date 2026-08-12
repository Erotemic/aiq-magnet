"""
The audit put through its paces against the shipped example.

Each of these covers a failure the machinery exists to catch, and each is
checked against `magnet/examples/bounded_mean` rather than a fixture, so a
change to the example that breaks the story fails here.
"""
import json
from importlib.resources import files

import pytest
import ubelt as ub

from magnet.theory import load
from magnet.theory.audit import audit
from magnet.theory.static import check_sites, extract_source, lint


@pytest.fixture(scope='module')
def example():
    return str(files('magnet') / 'examples' / 'bounded_mean')


@pytest.fixture(scope='module')
def index_fpath():
    return str(files('magnet') / 'examples' / 'bounded_mean' / 'theory' / 'index.yaml')


def test_the_example_audits_clean(example, index_fpath):
    report = audit(sources=[example], indexes=[index_fpath])
    assert report.issues == []
    assert len(report.basis.edges) == 5


def test_a_sorry_statement_is_reported_as_unproved(index_fpath):
    formalization = load(index_fpath)
    proved = formalization['MagnetExample.BoundedMean.mean_mem_Icc']
    unproved = formalization['MagnetExample.BoundedMean.abs_sampleMean_sub_mean_le']

    assert proved.extra_axioms == ()
    # A statement is unproved because of the axioms its proof used, not because
    # someone remembered to mark it.
    assert unproved.extra_axioms == ('sorryAx',)
    assert str(unproved.proof) == 'sorry'


def test_a_reference_to_a_nonexistent_binder_is_a_lint_error(index_fpath):
    ledger = extract_source(
        "from magnet.theory import assumes\n"
        "assumes('MagnetExample.BoundedMean.mean_mem_Icc::hnope')\n",
        filename='edges.py',
    )
    issues = lint(ledger, [load(index_fpath)])
    assert [i.kind for i in issues] == ['unknown-binder']


def test_a_reference_to_a_nonexistent_statement_is_a_lint_error(index_fpath):
    ledger = extract_source(
        "from magnet.theory import grounds\n"
        "grounds('MagnetExample.BoundedMean.no_such_theorem')\n",
        filename='edges.py',
    )
    issues = lint(ledger, [load(index_fpath)])
    assert [i.kind for i in issues] == ['unknown-declaration']


def test_an_anchor_catches_a_line_that_moved(tmp_path):
    # The failure the anchor exists for: an edge declared away from its code
    # site, whose line silently stops being the line.
    pkg = tmp_path / 'pkg'
    pkg.mkdir()
    (pkg / 'predictor.py').write_text('import numpy\n\nscore = LinearRegression()\n')
    ledger = extract_source(
        "from magnet.theory import substitutes\n"
        "substitutes('P::h', site='pkg.predictor:3', anchor='LinearRegression')\n",
        filename='edges.py',
    )
    assert check_sites(ledger, {'pkg': str(tmp_path)}) == []

    # Someone adds an import above it.
    (pkg / 'predictor.py').write_text(
        'import numpy\nimport pandas\n\nscore = LinearRegression()\n'
    )
    (issue,) = check_sites(ledger, {'pkg': str(tmp_path)})
    assert issue.kind == 'anchor-mismatch'


def test_a_site_without_an_anchor_is_reported_not_passed(tmp_path):
    pkg = tmp_path / 'pkg'
    pkg.mkdir()
    (pkg / 'predictor.py').write_text('a = 1\nb = 2\n')
    ledger = extract_source(
        "from magnet.theory import assumes\nassumes('P::h', site='pkg.predictor:2')\n",
        filename='edges.py',
    )
    (issue,) = check_sites(ledger, {'pkg': str(tmp_path)})
    assert issue.kind == 'unanchored-site'


def test_the_shim_is_audited_without_importing_it(tmp_path, index_fpath):
    # A team vendors the shim and annotates their own code. MAGNET reads their
    # source; nothing of theirs is imported, and the shim is a no-op for them.
    from magnet.theory import shim

    team = tmp_path / 'team'
    team.mkdir()
    shim._install(str(team / 'magnet_theory.py'))
    (team / 'predictor.py').write_text(
        'from magnet_theory import assumes, satisfies\n'
        '\n'
        '@satisfies("MagnetExample.BoundedMean.mean_mem_Icc::hlo",\n'
        '           informal="scores are clipped to [0, 1]")\n'
        'def predict(x):\n'
        '    return x\n'
        '\n'
        'assumes("MagnetExample.BoundedMean.abs_sampleMean_sub_mean_le::hiid",\n'
        '        severity="high")\n'
    )

    report = audit(sources=[str(team)], indexes=[index_fpath])
    assert report.issues == []
    relations = sorted(str(e.relation) for e in report.basis.edges)
    assert relations == ['assumes', 'satisfies']


def test_the_ledger_round_trips_into_a_card_basis(example, index_fpath, tmp_path):
    from magnet.theory.cards import basis_from_card

    report = audit(sources=[example], indexes=[index_fpath])
    ledger = ub.Path(tmp_path) / 'ledger.json'
    ledger.write_text(json.dumps(report.to_dict(), default=str))

    card = {
        'theory': {
            'formalizations': [index_fpath],
            'ledger': str(ledger),
            'grounds': [{'declaration': 'MagnetExample.BoundedMean.mean_mem_Icc'}],
        }
    }
    basis = basis_from_card(card)

    # Only the edges bearing on the declaration the card grounds on.
    assert {e.ref.declaration for e in basis.edges} == {
        'MagnetExample.BoundedMean.mean_mem_Icc'
    }
    assert basis.coverage().is_complete is False  # `xs` is structural, still a binder
