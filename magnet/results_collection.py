"""Collect compatible saved variants of one card without scheduling work."""
import json
import shutil
import tempfile
from collections.abc import Mapping
from os import PathLike
from pathlib import Path

import yaml

from magnet.evaluation import (
    Metric,
    _calculate_metrics,
    _parse_symbol_metadata,
    _reduce_results,
)
from magnet.results import (
    MANIFEST,
    export_result_bundle,
    file_digest,
    load_result_bundle,
    verify_result_bundle,
)


def combine_result_bundles(
    sources: Mapping[str, str | PathLike[str]], destination: str | PathLike[str], *,
    notes: str = '', title: str | None = None,
) -> Path:
    """Keep one card and aggregate verdict, with named variants and unchanged cells.

    Matrix overrides and execution provenance remain attached to each variant.
    This collects evidence; it does not rerun claims, pool data or refit models.
    """
    if not sources:
        raise ValueError('at least one named source is required')
    bundles = {label: load_result_bundle(path) for label, path in sources.items()}
    first = next(iter(bundles.values()))
    common_fields = ('name', 'description', 'claim', 'symbols', 'claim_aggregation_strategy')
    for label, bundle in bundles.items():
        checked = verify_result_bundle(bundle.root)
        if not checked['valid']:
            raise ValueError(f'{label}: invalid source bundle: {checked["errors"]}')
        for name in common_fields:
            if bundle.card.get(name) != first.card.get(name):
                raise ValueError(f'{label}: incompatible card field {name!r}')
        if bundle.verdict.get('claim_aggregation_strategy') != first.verdict.get('claim_aggregation_strategy'):
            raise ValueError(f'{label}: incompatible claim aggregation strategy')
        if not bundle.verdict:
            raise ValueError(f'{label}: no recorded verdict')
    destination = Path(destination).absolute()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.magnet-combine-', dir=destination.parent) as folder:
        staging = Path(folder)
        shutil.copyfile(first.root / 'card.yaml', staging / 'card.yaml')
        if title is not None:
            card = dict(first.card, title=title)
            (staging / 'card.yaml').write_text(yaml.safe_dump(card, sort_keys=False))
        metadata_path = first.root / 'symbol_metadata.json'
        metadata = (json.loads(metadata_path.read_text()) if metadata_path.exists()
                    else _parse_symbol_metadata(first.card.get('symbols', {})))
        if metadata:
            (staging / metadata_path.name).write_text(json.dumps(metadata, indent=2) + '\n')
        variants, cells, artifacts = {}, {}, {}
        for label, bundle in bundles.items():
            source_variants = bundle.variants or {label: {
                'claims': list(bundle.cells), 'kwdagger': bundle.card.get('kwdagger'),
                'source_record': str(bundle.root),
                'source_verdict_sha256': file_digest(bundle.root / 'verdict.json'),
                'execution': bundle.execution,
            }}
            for name, variant in source_variants.items():
                if name in variants:
                    raise ValueError(f'duplicate variant label {name!r}')
                variants[name] = variant
            for key, cell in bundle.cells.items():
                if key in cells and cells[key] != cell:
                    raise ValueError(f'conflicting claim records for {key!r}')
                cells[key] = cell
                target = staging / 'results' / key / 'verdict.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundle.root / 'results' / key / 'verdict.json', target)
            references = {item['source']: item for item in bundle.manifest.get('artifacts', [])}
            for cell in bundle.cells.values():
                original = cell.get('artifact')
                if original and original not in references:
                    references[original] = {
                        'source': original, 'node': (bundle.card.get('kwdagger') or {}).get('result_node'),
                        'process_id': cell.get('cell') or Path(original).parent.name,
                    }
            for item in references.values():
                source = bundle.artifact_path(item['source'])
                if not source.is_file():
                    continue  # The native export records missing optional terminal evidence.
                if item['source'] in artifacts:
                    prior = staging / artifacts[item['source']]['path']
                    if file_digest(prior) != file_digest(source):
                        raise ValueError(f'conflicting artifact snapshots: {item["source"]}')
                    continue
                rel = f'artifacts/{len(artifacts):05d}/{source.name}'
                target = staging / rel
                target.parent.mkdir(parents=True)
                shutil.copyfile(source, target)
                config = source.parent / 'job_config.json'
                if config.exists():
                    shutil.copyfile(config, target.parent / config.name)
                artifacts[item['source']] = dict(item, path=rel)
        strategy = first.verdict.get('claim_aggregation_strategy', {'type': 'all'})
        definitions = Metric.build_metrics_from_symbol_metadata(metadata)
        metrics = _calculate_metrics(definitions, [cell['symbols'] for cell in cells.values()], metadata)
        verdict = {
            'result': _reduce_results([cell['status'] for cell in cells.values()], strategy),
            'claim_aggregation_strategy': strategy, 'claims': list(cells),
            'metrics': metrics, 'evidence': {'scope': 'all', 'available': len(cells)},
        }
        documents = {'verdict.json': verdict, 'variants.json': variants}
        for name, data in documents.items():
            (staging / name).write_text(json.dumps(data, indent=2) + '\n')
        if notes:
            (staging / 'report_notes.md').write_text(notes.rstrip() + '\n')
        manifest = {'schema_version': 1, 'format': 'magnet.result-bundle',
                    'artifacts': list(artifacts.values()), 'missing_supporting': [],
                    'files': {}}
        for path in staging.rglob('*'):
            if path.is_file():
                manifest['files'][path.relative_to(staging).as_posix()] = {
                    'sha256': file_digest(path), 'size': path.stat().st_size}
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2) + '\n')
        output = export_result_bundle(staging, destination, supporting='requested')
        exported_manifest = json.loads((output / MANIFEST).read_text())
        exported_manifest.pop('source_record', None)
        exported_manifest['source_records'] = {label: str(bundle.root) for label, bundle in bundles.items()}
        (output / MANIFEST).write_text(json.dumps(exported_manifest, indent=2) + '\n')
        return output
