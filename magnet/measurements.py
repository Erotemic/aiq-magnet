"""Publish and select declared measurements independently of claim evaluation.

Each source remains its own result set. Selecting or expanding a table does
not join sources, infer scientific equivalence, or request computation.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import json
from pathlib import Path
import statistics
from typing import Any


def symbol_node(name: str) -> str | None:
    parts = name.split('.')
    if parts[:2] == ['specified', 'params'] and len(parts) > 3:
        return parts[2]
    if len(parts) > 2 and parts[0] in {
        'metrics', 'params', 'resolved_params', 'context', 'resources', 'machine',
    }:
        return parts[1]
    return None


def is_declaration(spec: Mapping) -> bool:
    return not {'value', 'sweep', 'python'} & spec.keys()


def published_sources(bundle) -> dict:
    if bundle.measurement_sets:
        return bundle.measurement_sets.get('sources', {})
    # Historical records already contain resolved symbols. Expose those bytes
    # without rerunning the claim or claiming to recover unrecorded outputs.
    node = (bundle.card.get('kwdagger') or {}).get('result_node') or 'legacy'
    rows = []
    for key, cell in bundle.cells.items():
        values = dict(cell.get('symbols') or {})
        for name in bundle.card.get('symbols') or {}:
            if name in (cell.get('evidence') or {}):
                values[name] = cell['evidence'][name]
        row = {'cell': cell.get('cell', key), 'artifact': cell.get('artifact'),
               'symbols': values, 'missing_symbols': []}
        variants = [(name, spec) for name, spec in bundle.variants.items() if key in spec.get('claims', [])]
        if variants:
            rows.extend({**row, 'variant': name, 'source_record': spec.get('source_record')}
                        for name, spec in variants)
        else:
            rows.append(row)
    return {node: {'rows': rows}} if rows else {}


def materialize_readout(node: str, evidence: list[dict], symbols: dict, *, bind) -> dict:
    reported_columns = {name for item in evidence for name in item['row']}
    declarations = {name: spec for name, spec in symbols.items()
                    if is_declaration(spec) and (symbol_node(name) in {None, node}
                                                  or name in reported_columns)}
    rows = []
    for item in evidence:
        filled, available = bind(declarations, item['row'])
        names = [name for name in declarations if symbol_node(name) == node or name in available]
        rows.append({'cell': item['key'], 'artifact': item.get('artifact'),
                     'symbols': {name: filled[name].get('value') for name in names},
                     'missing_symbols': sorted(set(names) - available)})
    return {'rows': rows}


def symbol_catalog(bundle) -> dict:
    """Definitions and availability from a saved card; no pipeline imports."""
    catalog = {}
    for name, spec in (bundle.card.get('symbols') or {}).items():
        observations = [row for source in published_sources(bundle).values()
                        for row in source['rows'] if name in row.get('symbols', {})]
        catalog[name] = {**spec, 'node': symbol_node(name),
                         'sources': [node for node, source in published_sources(bundle).items()
                                     if any(name in row.get('symbols', {}) for row in source['rows'])],
                         'observations': len(observations),
                         'missing': sum(name in row.get('missing_symbols', []) for row in observations),
                         'null': sum(row['symbols'][name] is None for row in observations)}
    return catalog


def table_artifact(item: dict, value: str) -> str:
    path = Path(value)
    if not path.is_absolute() and item.get('artifact'):
        path = Path(item['artifact']).parent / path
    return str(path)


def measurement_artifacts(bundle) -> list[dict]:
    """Primary evidence and explicitly declared file-backed tables."""
    artifacts = []
    definitions = bundle.card.get('symbols') or {}
    for node, source in published_sources(bundle).items():
        for item in source['rows']:
            paths = [item['artifact']] if item.get('artifact') else []
            for name, value in item.get('symbols', {}).items():
                metadata = (definitions.get(name) or {}).get('metadata') or {}
                if metadata.get('format') and isinstance(value, str):
                    paths.append(table_artifact(item, value))
            artifacts.extend({'source': path, 'node': node, 'process_id': item['cell']} for path in paths)
    return artifacts


def _flatten_record(record: Mapping, prefix: str = '') -> dict:
    flat = {}
    for key, value in record.items():
        name = f'{prefix}.{key}' if prefix else str(key)
        leaves = _flatten_record(value, name) if isinstance(value, Mapping) and value else {name: value}
        if flat.keys() & leaves.keys():
            raise ValueError('ambiguous nested table column names')
        flat.update(leaves)
    return flat


def measurement_rows(bundle, *, node: str | None = None, expand: str | None = None,
                     where: Mapping[str, Any] | None = None,
                     columns: list[str] | None = None) -> list[dict]:
    """Read a result set, optionally expanding one declared record-array symbol.

    ``where`` applies exact equality (a list means membership). Expansion is
    explicit: there is no implicit join or Cartesian product between tables.
    Provenance columns are retained unless an explicit projection removes them.
    """
    sources = published_sources(bundle)
    if node is not None and node not in sources:
        raise ValueError(f'unknown measurement node {node!r}; available: {sorted(sources)}')
    if expand is not None:
        spec = (bundle.card.get('symbols') or {}).get(expand)
        if spec is None:
            raise ValueError(f'unknown table symbol {expand!r}')
    rows = []
    known = set()
    for source_name, source in sources.items():
        if node is not None and source_name != node:
            continue
        for item in source['rows']:
            values = dict(item.get('symbols') or {})
            base = {'_record': str(bundle.root), '_node': source_name,
                    '_record_id': bundle.measurement_sets.get('record_id', bundle.execution.get('record_id')),
                    '_cell': item['cell'], '_artifact': item.get('artifact'),
                    '_scope': item.get('scope', bundle.measurement_sets.get('scope')),
                    '_missing_symbols': list(item.get('missing_symbols', []))}
            if 'variant' in item:
                base['_variant'] = item['variant']
            if 'source_record' in item:
                base['_source_record'] = item['source_record']
            if 'source_record_id' in item:
                base['_source_record_id'] = item['source_record_id']
            if expand is None:
                records = [dict(base, **values)]
            elif expand in values:
                table = values.pop(expand)
                if table is None:
                    continue  # The source row remains visible without expansion.
                metadata = bundle.card['symbols'][expand].get('metadata') or {}
                if isinstance(table, str) and metadata.get('format'):
                    path = bundle.artifact_path(table_artifact(item, table))
                    if metadata['format'] == 'jsonl':
                        with path.open() as stream:
                            table = [json.loads(line) for line in stream if line.strip()]
                    elif metadata['format'] == 'csv':
                        import csv
                        with path.open() as stream:
                            table = list(csv.DictReader(stream))
                    else:
                        table = json.loads(path.read_text())
                if not isinstance(table, list) or any(not isinstance(r, dict) for r in table):
                    raise ValueError(f'{expand!r} is not a table of records in {item["cell"]!r}')
                declared_columns = metadata.get('columns') or {}
                known.update(declared_columns)
                # Carry scalar context without repeating other potentially large tables.
                values = {k: v for k, v in values.items() if not isinstance(v, (list, dict))}
                records = []
                for record in table:
                    record = _flatten_record(record)
                    for column in declared_columns:
                        record.setdefault(column, None)
                    overlap = set(record) & (set(base) | set(values))
                    if overlap:
                        raise ValueError(f'table columns collide with source fields: {sorted(overlap)}')
                    records.append(dict(base, **values, **record))
            else:
                continue
            for record in records:
                known.update(record)
                rows.append(record)
    requested = set(where or {}) | set(columns or [])
    if rows and (unknown := requested - known):
        raise ValueError(f'unknown table columns: {sorted(unknown)}; available: {sorted(known)}')
    if where:
        rows = [row for row in rows if all(
            row.get(key) in value if isinstance(value, list) else row.get(key) == value
            for key, value in where.items())]
    if columns is not None:
        rows = [{name: row.get(name) for name in columns} for row in rows]
    return rows


def summarize_rows(rows: list[dict], *, group_by: list[str], aggregate: Mapping[str, str]) -> list[dict]:
    """Explicit descriptive reductions; include observed/missing denominators.

    This does not calculate uncertainty, weight cohorts, or decide which rows
    constitute a scientific comparison. The saved selection supplies that scope.
    """
    groups = {}
    for row in rows:
        key = tuple(row.get(name) for name in group_by)
        groups.setdefault(key, []).append(row)
    reducers = {'mean': statistics.fmean, 'min': min, 'max': max, 'sum': sum, 'count': len}
    unknown = set(aggregate.values()) - set(reducers)
    if unknown:
        raise ValueError(f'unknown reductions: {sorted(unknown)}; available: {sorted(reducers)}')
    result = []
    for key, members in groups.items():
        record = dict(zip(group_by, key))
        record['_rows'] = len(members)
        record['_records'] = sorted({row['_record'] for row in members if '_record' in row})
        record['_record_ids'] = sorted({identity for row in members
                                        if (identity := row.get('_source_record_id') or row.get('_record_id'))})
        for name, reduction in aggregate.items():
            if not any(name in row for row in members):
                raise ValueError(f'unknown reduction column {name!r}')
            values = [row[name] for row in members if row.get(name) is not None]
            if reduction != 'count' and any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                raise ValueError(f'{name!r} must contain finite numeric values for {reduction}')
            record[f'{name}.{reduction}'] = reducers[reduction](values) if values or reduction == 'count' else None
            record[f'{name}.observed'] = len(values)
            record[f'{name}.missing'] = len(members) - len(values)
        result.append(record)
    return result
