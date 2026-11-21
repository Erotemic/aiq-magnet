"""Prepare coding benchmark recipes for the existing EvaluationNode."""
from __future__ import annotations

import json
from pathlib import Path


def recipe(request: dict, *, name: str, endpoint: str, worker_python: str, select: dict) -> dict:
    algo = {key: value for key, value in request.items() if key != 'schema_version'}
    algo['select'] = select
    return {'name': name, 'title': name, 'description': 'Coding benchmark protocol smoke test',
        'version': '1.0', 'organizations': [],
        'submitter': {'name': 'Example', 'email': 'example@example.invalid'}, 'links': [],
        'tags': ['example', 'aiq-magnet-evals', 'swebench'],
        'claim': {'python': 'assert metrics.evaluate.eligible'},
        'evidence': {'scope': 'requested'},
        'kwdagger': {'result_node': 'evaluate', 'pipeline': {'nodes': {'evaluate': {
            'class': 'magnet.backends.aiq_evals.EvaluationNode', 'algo_params': algo,
            'perf_params': {'endpoint': endpoint, 'worker_python': worker_python,
                            'verify_serving_provenance': True}}}}}}


def sharded_recipe(requests, *, name: str, endpoint: str, worker_python: str,
                   select: dict) -> dict:
    """One measurement node over a declared task-options matrix, using ordinary kwdagger reuse.

    Every shard retains its native result and trajectory. Measurement-only
    readout preserves partial coverage without turning a failed shard into zero.
    Requests may differ only in task options, not model or generation settings.
    """
    requests = [r.to_dict() if hasattr(r, 'to_dict') else r for r in requests]
    if not requests:
        raise ValueError('provide at least one evaluation request')
    common = {k: v for k, v in requests[0].items() if k != 'task_options'}
    if any({k: v for k, v in r.items() if k != 'task_options'} != common for r in requests):
        raise ValueError('sharded requests must share model, generation and engine settings')
    result = recipe(requests[0], name=name, endpoint=endpoint,
                    worker_python=worker_python, select=select)
    result['description'] = 'Independently reusable coding benchmark measurements'
    result.pop('claim')
    result['kwdagger'].pop('result_node')
    result['kwdagger']['readout_nodes'] = ['evaluate']
    result['kwdagger']['matrix'] = {'evaluate.task_options': [r['task_options'] for r in requests]}
    result['symbols'] = {f'metrics.evaluate.{key}': {'metadata': metadata} for key, metadata in {
        'score': {'display_name': 'Benchmark score', 'unit': '1'},
        'eligible': {'display_name': 'Complete usable result'},
        'ineligible_reasons': {'display_name': 'Incomplete-result reason'},
        'run_path': {'display_name': 'Native evaluation bundle', 'display': False},
        'measurement_identity': {'display_name': 'Measurement identity', 'display': False},
    }.items()}
    return result


def main(argv=True):
    import kwconf
    import yaml

    class Config(kwconf.Config):
        kind = kwconf.Value('verified', choices=['verified', 'pro', 'harbor'])
        endpoint = kwconf.Value(None, required=True, help='infer-stack alias')
        served_model = kwconf.Value(None, help='served name; defaults to alias')
        worker_python = kwconf.Value(None, required=True)
        output = kwconf.Value(None, required=True, help='output YAML recipe')
        evals_root = kwconf.Value(None, help='evaluator source checkout')
        pro_root = kwconf.Value(None, help='pinned Pro V2 checkout')
        image_pins = kwconf.Value(None, help='Pro {instance: sha256:image-id} JSON')
        selection = kwconf.Value(None, help='Verified JSON list or {instance_ids: [...]}')
        shard_size = kwconf.Value(1, help='Verified instances per independently reusable request')
        agent_protocol = kwconf.Value('upstream', help='Verified agent protocol')
        message_limit = kwconf.Value(30, help='Verified per-sample message limit')
        generation = kwconf.Value(None, help='Verified generation settings as JSON or a mapping')
        lease_ttl = kwconf.Value('8h', help='Command-scoped lease TTL; completion releases it')
        lease_timeout = kwconf.Value(1800, help='Endpoint readiness timeout in seconds')
        timeout_seconds = kwconf.Value(None, help='Optional evaluation worker wall-time limit')
        task_path = kwconf.Value(None, help='Harbor task directory')
        agent = kwconf.Value('oracle', help='Harbor built-in or python:module:Class')

    args = Config.cli(argv=argv, strict=True, special_options=False)
    import magnet_evals
    from magnet_evals import EvaluationRequest, ModelBinding

    root = Path(args.evals_root or Path(magnet_evals.__file__).resolve().parents[1])
    binding = ModelBinding(role='primary', model=args.served_model or args.endpoint,
                           provider='openai', provider_options=(
                               {'responses_api': False} if args.kind == 'verified' else {}))
    requests = None
    if args.kind == 'verified' and args.selection:
        from magnet_evals.benchmarks.swe_bench_verified import profile_requests

        if not args.image_pins:
            raise ValueError('Verified selection needs image_pins')
        selection = json.loads(Path(args.selection).read_text())
        ids = selection['instance_ids'] if isinstance(selection, dict) else selection
        generation = json.loads(args.generation) if isinstance(args.generation, str) else args.generation
        requests = profile_requests(ids, json.loads(Path(args.image_pins).read_text()), binding,
            shard_size=args.shard_size, agent_protocol=args.agent_protocol,
            message_limit=args.message_limit, generation=generation)
        select = {'scorer': 'swe_bench_scorer', 'metric': 'accuracy'}
    elif args.kind == 'verified':
        request = EvaluationRequest.from_dict(json.loads((root / 'examples/swebench_verified_request.json').read_text()))
        from dataclasses import replace

        request = replace(request, models=(binding,))
        select = {'scorer': 'swe_bench_scorer', 'metric': 'accuracy'}
    elif args.kind == 'pro':
        from magnet_evals.benchmarks.swe_bench_pro import profile_request

        if not args.pro_root or not args.image_pins:
            raise ValueError('Pro smoke needs pro_root and image_pins')
        request = profile_request(args.pro_root, 'smoke', binding,
                                  json.loads(Path(args.image_pins).read_text()))
        select = {'metric': 'mean', 'score': 'reward'}
    else:
        if not args.task_path:
            raise ValueError('Harbor needs task_path')
        request = EvaluationRequest(engine='harbor', task='path:' + str(Path(args.task_path).resolve()),
                                    data_revision='caller-selected', models=(binding,),
                                    task_options={'agent': args.agent})
        select = {'metric': 'mean', 'score': 'reward'}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared = (sharded_recipe(requests, name='swebench_verified', endpoint=args.endpoint,
                    worker_python=args.worker_python, select=select) if requests else
                recipe(request.to_dict(), name='swebench_' + args.kind + '_smoke',
                    endpoint=args.endpoint, worker_python=args.worker_python, select=select))
    node = prepared['kwdagger']['pipeline']['nodes']['evaluate']
    node.update(lease_ttl=args.lease_ttl, lease_timeout=args.lease_timeout)
    if args.timeout_seconds is not None:
        node['perf_params']['timeout_seconds'] = args.timeout_seconds
    target.write_text(yaml.safe_dump(prepared, sort_keys=False))
    print(target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
