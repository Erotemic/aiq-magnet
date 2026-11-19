"""Commands for persisted results; none execute claims or request computation."""
import json
from pathlib import Path

import kwconf

from magnet.results import (
    discover_result_bundles,
    export_result_bundle,
    load_result_bundle,
    render_result,
    verify_result_bundle,
)


class ShowResultCLI(kwconf.Config):
    """Present an existing native result, including older dashboard bundles."""
    path = kwconf.Value(None, required=True, position=1)
    format = kwconf.Value('markdown', choices=['markdown', 'text', 'json'])
    output = kwconf.Value(None, help='Write the presentation here instead of stdout')
    details = kwconf.Flag(True, help='Include full claim and evidence details; --details=0 shows only the variant table')
    legacy_claims = kwconf.Flag(False, help='Include compatibility Boolean assessments in measurement-first reports')

    @classmethod
    def main(cls, argv=None, **kwargs):
        args = cls.cli(argv=argv, data=kwargs, strict=True)
        bundle = load_result_bundle(args.path)
        text = render_result(bundle, format=args.format, details=args.details, legacy_claims=args.legacy_claims)
        if args.output:
            target = Path(args.output).absolute()
            if target.parent == bundle.root or target.is_relative_to(bundle.root / 'results'):
                if target.name not in {'report.md', 'report.txt'}:
                    raise ValueError('presentation must not replace native result files')
            target.write_text(text)
        else:
            print(text, end='')


class ExportResultCLI(kwconf.Config):
    """Export a native result with optional selected supporting artifacts."""
    source = kwconf.Value(None, required=True, position=1)
    destination = kwconf.Value(None, required=True, position=2)
    supporting = kwconf.Value('none', choices=['none', 'terminal', 'requested'])
    nodes = kwconf.Value('', help='Comma-separated requested node names; empty selects all')
    include_logs = kwconf.Flag(False)
    attachments = kwconf.Value(None, help='JSON/YAML mapping (or mapping file) of attachment names to files')

    @classmethod
    def main(cls, argv=None, **kwargs):
        args = cls.cli(argv=argv, data=kwargs, strict=True)
        attachments = None
        if args.attachments:
            import kwutil
            attachments = kwutil.Yaml.coerce(args.attachments)
            if not isinstance(attachments, dict):
                raise ValueError('attachments must map relative names to files')
        result = export_result_bundle(args.source, args.destination, supporting=args.supporting,
                                      nodes=tuple(x.strip() for x in args.nodes.split(',') if x.strip()),
                                      include_logs=args.include_logs, attachments=attachments)
        print(result)


class VerifyResultCLI(kwconf.Config):
    """Verify an exported bundle's bytes and claim references."""
    path = kwconf.Value(None, required=True, position=1)

    @classmethod
    def main(cls, argv=None, **kwargs):
        args = cls.cli(argv=argv, data=kwargs, strict=True)
        result = verify_result_bundle(args.path)
        print(json.dumps(result, indent=2))
        return 0 if result['valid'] else 1


class CombineResultsCLI(kwconf.Config):
    """Collect named saved variants of one card into one result and report."""
    sources = kwconf.Value(None, required=True, position=1, help='YAML/JSON mapping of labels to result paths')
    destination = kwconf.Value(None, required=True, position=2)
    notes = kwconf.Value(None, help='Markdown file containing findings and limitations for the report')
    title = kwconf.Value(None, help='Presentation title for the collected card')

    @classmethod
    def main(cls, argv=None, **kwargs):
        import kwutil

        from magnet.results_collection import combine_result_bundles
        args = cls.cli(argv=argv, data=kwargs, strict=True)
        sources = kwutil.Yaml.coerce(args.sources)
        if not isinstance(sources, dict):
            raise ValueError('sources must map variant labels to result paths')
        notes = Path(args.notes).read_text() if args.notes else ''
        print(combine_result_bundles(sources, args.destination, notes=notes, title=args.title))


class ResultsCLI(kwconf.ModalCLI):
    """Read, present, export and check existing serialized evaluation results."""
    combine = CombineResultsCLI


class SymbolsResultCLI(kwconf.Config):
    """Inspect published symbols, units, table columns and observed availability."""
    path = kwconf.Value(None, required=True, position=1)

    @classmethod
    def main(cls, argv=None, **kwargs):
        args = cls.cli(argv=argv, data=kwargs, strict=True)
        print(json.dumps(load_result_bundle(args.path).symbol_catalog(), indent=2))


class TableResultCLI(kwconf.Config):
    """Select saved measurements with an explicit, serializable analysis scope."""
    path = kwconf.Value(None, required=True, position=1)
    node = kwconf.Value(None, help='Select an independent measurement source node')
    expand = kwconf.Value(None, help='Expand one declared record-array symbol into rows')
    where = kwconf.Value(None, help='JSON/YAML mapping of column names to exact values or allowed-value lists')
    columns = kwconf.Value(None, help='Comma-separated output columns; default preserves all columns')
    group_by = kwconf.Value(None, help='Comma-separated grouping columns for an explicit descriptive reduction')
    aggregate = kwconf.Value(None, help='JSON/YAML mapping of value columns to mean/min/max/sum/count')
    format = kwconf.Value('json', choices=['json', 'csv'])
    output = kwconf.Value(None)

    @classmethod
    def main(cls, argv=None, **kwargs):
        import csv
        import io
        import kwutil
        from magnet.measurements import measurement_rows, summarize_rows

        args = cls.cli(argv=argv, data=kwargs, strict=True)
        bundle = load_result_bundle(args.path)
        where = kwutil.Yaml.coerce(args.where) if args.where else {}
        aggregate = kwutil.Yaml.coerce(args.aggregate) if args.aggregate else {}
        if not isinstance(where, dict) or not isinstance(aggregate, dict):
            raise ValueError('where and aggregate must be mappings')
        columns = [x.strip() for x in args.columns.split(',')] if args.columns else None
        group_by = [x.strip() for x in args.group_by.split(',')] if args.group_by else []
        if group_by and not aggregate:
            raise ValueError('group_by requires an explicit aggregate mapping')
        rows = measurement_rows(bundle, node=args.node, expand=args.expand, where=where)
        if aggregate:
            if rows and (unknown := set(group_by) - set().union(*(set(row) for row in rows))):
                raise ValueError(f'unknown grouping columns: {sorted(unknown)}')
            rows = summarize_rows(rows, group_by=group_by, aggregate=aggregate)
        if columns:
            if rows and (unknown := set(columns) - set().union(*(set(row) for row in rows))):
                raise ValueError(f'unknown output columns: {sorted(unknown)}')
            rows = [{name: row.get(name) for name in columns} for row in rows]
        selection = {'record': str(bundle.root), 'node': args.node, 'expand': args.expand,
                     'record_id': bundle.measurement_sets.get('record_id', bundle.execution.get('record_id')),
                     'where': where, 'columns': columns, 'group_by': group_by, 'aggregate': aggregate,
                     'evidence_scope': bundle.measurement_sets.get('scope')}
        if args.format == 'json':
            text = json.dumps({'selection': selection, 'rows': rows}, indent=2) + '\n'
        else:
            stream = io.StringIO()
            names = columns or list(dict.fromkeys(name for row in rows for name in row))
            writer = csv.DictWriter(stream, fieldnames=names)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})
            text = stream.getvalue()
        if args.output:
            target = Path(args.output).absolute()
            native = {'card.yaml', 'verdict.json', 'evaluation.json', 'symbol_metadata.json',
                      'execution.json', 'requested_runs.json', 'bundle_manifest.json'}
            if (target.parent == bundle.root and target.name in native) or target.is_relative_to(bundle.root / 'results'):
                raise ValueError('table output must not replace native result files')
            target.write_text(text)
            if args.format == 'csv':
                target.with_suffix(target.suffix + '.selection.json').write_text(json.dumps(selection, indent=2) + '\n')
        else:
            print(text, end='')


class ListResultsCLI(kwconf.Config):
    """Find saved evaluation records without loading pipeline code or caches."""
    path = kwconf.Value(None, required=True, position=1)
    format = kwconf.Value('text', choices=['text', 'json'])

    @classmethod
    def main(cls, argv=None, **kwargs):
        args = cls.cli(argv=argv, data=kwargs, strict=True)
        records = discover_result_bundles(args.path)
        if args.format == 'json':
            print(json.dumps(records, indent=2))
        else:
            for item in records:
                state = item.get('measurement_state', item['result'])
                print(f"{state:<15} claims={item['available_claims']}/{item['claims']} "
                      f"execution={item['execution_state']}  {item['path']}")


ResultsCLI.register(ShowResultCLI, command='show')
ResultsCLI.register(ExportResultCLI, command='export')
ResultsCLI.register(VerifyResultCLI, command='verify')
ResultsCLI.register(ListResultsCLI, command='list')
ResultsCLI.register(SymbolsResultCLI, command='symbols')
ResultsCLI.register(TableResultCLI, command='table')
