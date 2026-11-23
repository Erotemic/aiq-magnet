"""Execution-time native request resolution for EvaluationNode jobs.

The generated job invokes this in its target host/container/worker environment.
Command construction never invokes it. Legacy one-shot callers remain supported.
The runtime request handoff carries serving facts bound by the executing gate.
Print one JSON resolution on the final stdout line; no model evaluation occurs.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import kwconf

RESOLUTION_SCHEMA = 'magnet-aiq-evals-resolution/1'


class ResolveNodeCLI(kwconf.Config):
    """Resolve an EvaluationNode's identities where the node runs."""

    __prog__ = 'python -m magnet.backends.aiq_evals.cli.resolve_node'

    request = kwconf.Value(None, required=True, parser=str, help='EvaluationRequest as JSON')
    worker_python = kwconf.Value(None, parser=str, help='engine worker interpreter')
    runtime_request_file = kwconf.Value(None, parser=str, help='execution-time request handoff file')
    import_source = kwconf.Value(None, parser=str, help='native artifacts the node imports')
    allow_external_symlinks = kwconf.Value(False, isflag=True, help='follow links out of the import source')


def resolve(request_dict: dict, worker_python: str | None, import_source: str | None,
            allow_external_symlinks: bool) -> dict:
    from magnet_evals import (
        EvaluationRequest,
        ExecutionContext,
        native_source_identity,
        resolve_evaluation_async,
    )

    request = EvaluationRequest.from_dict(request_dict)
    with tempfile.TemporaryDirectory(prefix='magnet-preflight-') as scratch:
        context = ExecutionContext(output_dir=Path(scratch), worker_python=worker_python)
        # Identity never needs a secret's value, and a key may only exist inside
        # the node's later endpoint lease; execution still checks it.
        resolved = asyncio.run(resolve_evaluation_async(request, context, require_secrets=False))
    identity = resolved.identity
    return {
        'schema': RESOLUTION_SCHEMA,
        'measurement_identity': {
            'digest': identity.digest,
            'reusable': bool(identity.reusable),
            'unknown_reasons': list(identity.unknown_reasons),
        },
        'import_identity': (
            None if import_source is None
            else native_source_identity(import_source, allow_external_symlinks=allow_external_symlinks)
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = ResolveNodeCLI.cli(argv=True if argv is None else argv, strict=True, special_options=False)
    try:
        request = (json.loads(Path(args.runtime_request_file).read_text())['request']
                   if args.runtime_request_file else json.loads(args.request))
        payload = resolve(
            request,
            args.worker_python or None,
            args.import_source or None,
            bool(args.allow_external_symlinks),
        )
    except Exception as ex:  # reported to the scheduling process, which fails loudly
        print(f'{type(ex).__name__}: {ex}', file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
