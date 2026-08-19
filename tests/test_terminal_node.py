"""
Tests for the ``kwdagger.terminal_node`` declaration.

A card that declares a terminal node is stating that node produces its result.
MAGNET reads each configured instance's artifact instead of globbing the run
tree, and evaluates the claim once per instance -- one cell of the card.
"""

import json

import pytest
import ubelt as ub

from magnet.evaluation import KWDaggerProcessor


def test_terminal_node_is_kept_out_of_the_scheduled_spec():
    # kwdagger does not know about terminal_node; passing it through would
    # be rejected by the schedule config.
    processor = KWDaggerProcessor(
        {
            'pipeline': 'some.module.some_pipeline()',
            'matrix': {'a.b': [1, 2]},
            'terminal_node': 'summary',
        },
        root_dpath=ub.Path('.'),
    )
    assert processor.terminal_node == 'summary'
    assert 'terminal_node' not in processor.spec
    assert set(processor.spec) == {'pipeline', 'matrix'}


def test_absent_terminal_node_keeps_legacy_behaviour():
    processor = KWDaggerProcessor(
        {'pipeline': 'some.module.some_pipeline()', 'matrix': {}},
        root_dpath=ub.Path('.'),
    )
    assert processor.terminal_node is None


def test_collect_terminal_results_requires_a_declaration():
    processor = KWDaggerProcessor(
        {'pipeline': 'some.module.some_pipeline()', 'matrix': {}},
        root_dpath=ub.Path('.'),
    )
    with pytest.raises(ValueError, match='terminal_node'):
        processor.collect_terminal_results()


class _FakeNode:
    def __init__(self, name, dpath, config=None):
        self.name = name
        self.final_node_dpath = ub.Path(dpath)
        self.out_paths = {'o': 'out.json'}
        self.primary_out_key = 'o'
        self.config = config or {}


class _FakeDag:
    def __init__(self, nodes):
        self.nodes = nodes


def _processor_with_dag(nodes, root_dpath, terminal_node='summary'):
    processor = KWDaggerProcessor(
        {
            'pipeline': 'some.module.some_pipeline()',
            'matrix': {},
            'terminal_node': terminal_node,
        },
        root_dpath=root_dpath,
    )
    processor.dag = _FakeDag(nodes)
    return processor


def _fresh(name):
    dpath = ub.Path.appdir(f'magnet/tests/{name}')
    ub.delete(dpath)
    return dpath.ensuredir()


def _write(dpath, payload):
    artifact = ub.Path(dpath).ensuredir() / 'out.json'
    artifact.write_text(json.dumps(payload))
    return artifact


def test_results_are_qualified_by_node_name():
    dpath = _fresh('terminal_ok')
    artifact = _write(dpath / 'summary' / 'abc', {'mae': 0.03, '_hidden': 1})

    processor = _processor_with_dag(
        {'summary_id_abc': _FakeNode('summary', dpath / 'summary' / 'abc')},
        root_dpath=dpath,
    )
    cells = processor.collect_terminal_results()

    assert len(cells) == 1
    # Qualified, so two nodes cannot collide in a claim's namespace.
    assert cells[0]['results'] == {'metrics.summary.mae': 0.03}
    assert cells[0]['coords'] == {}
    assert cells[0]['artifact'] == str(artifact)


def test_a_fanned_out_terminal_node_yields_one_cell_each():
    # Two configured instances is a gather with group_by: each is one cell of
    # the card, consumed independently.
    dpath = _fresh('terminal_fanout')
    _write(dpath / 'summary' / 'a', {'mae': 0.01})
    _write(dpath / 'summary' / 'b', {'mae': 0.02})

    processor = _processor_with_dag(
        {
            'summary_id_a': _FakeNode(
                'summary', dpath / 'summary' / 'a',
                {'dataset': 'one', 'workers': 4}),
            'summary_id_b': _FakeNode(
                'summary', dpath / 'summary' / 'b',
                {'dataset': 'two', 'workers': 4}),
        },
        root_dpath=dpath,
    )
    cells = processor.collect_terminal_results()

    assert len(cells) == 2
    # Only the parameter that varies is a coordinate; `workers` is shared and
    # so is not part of what distinguishes a cell.
    assert sorted(cell['coords']['dataset'] for cell in cells) == ['one', 'two']
    assert all(set(cell['coords']) == {'dataset'} for cell in cells)


def test_unknown_terminal_node_names_the_available_ones():
    dpath = _fresh('terminal_unknown')
    processor = _processor_with_dag(
        {'other_id_abc': _FakeNode('other', dpath / 'other' / 'abc')},
        root_dpath=dpath,
        terminal_node='summary',
    )
    with pytest.raises(ValueError, match="available: \\['other'\\]"):
        processor.collect_terminal_results()


def test_missing_artifact_points_at_the_run_directory():
    dpath = _fresh('terminal_missing')
    processor = _processor_with_dag(
        {'summary_id_abc': _FakeNode('summary', dpath / 'summary' / 'abc')},
        root_dpath=dpath,
    )
    with pytest.raises(RuntimeError, match='produced no'):
        processor.collect_terminal_results()
