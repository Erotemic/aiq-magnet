# Coding benchmark recipes

Harbor and SWE-bench use `EvaluationNode` and the existing kwdagger scheduler.
Prepare a concrete Verified recipe with the evaluator's five frozen instances:

```bash
python -m magnet.examples.aiq_evals.swebench --kind verified \
  --endpoint coding --worker_python /path/to/verified-worker/bin/python \
  --evals_root /path/to/aiq-magnet-evals --output verified.yaml
magnet evaluate_new verified.yaml --output_path results --per_node_leasing=True
```

For Pro V2, use `--kind pro --pro_root /path/to/swe-bench-pro --image_pins pins.json`
and the Harbor worker interpreter. `pins.json` maps the frozen Ansible smoke
instance to its physical `sha256:` image ID. The evaluator's `profile_request`
pins the upstream revision, manifest, locked agent and fresh replay protocol.
Put the checkout's `v2/tooling` on `PYTHONPATH` for the worker. This recipe uses
the `local-harbor/pro-v2-offline-replay/v1` protocol, not a publisher score.

For a generic Harbor task, use `--kind harbor --task_path /path/to/task --agent
python:your_module:Agent` with the Harbor worker. The default oracle is useful
for protocol checks. A reusable task needs pinned sandbox images and a fully
identified agent runtime; installed agents currently remain non-reusable.

The generated claim checks evidence eligibility. Replace it with a scientific
claim appropriate to the experiment. These recipes run tiny smoke sets and do
not launch Verified's 500 tasks, HARD-51, or Pro's 642 tasks.

For a caller-selected Verified set, the same helper supports an explicit
selection and image-pin map:

```bash
python -m magnet.examples.aiq_evals.swebench --kind verified \
  --endpoint coding --worker_python /path/to/verified-worker/bin/python \
  --selection selection.json --image_pins images.json --shard_size 1 \
  --agent_protocol minimal-edit/v1 --message_limit 200 --output verified.yaml
magnet evaluate_new verified.yaml --output_path results \
  --backend tmux --tmux_workers 4 --per_node_leasing=True
```

`selection.json` is a list or an object containing `instance_ids`; `images.json`
maps selected instances to digest-pinned task images. This path uses
`swe_bench_verified.profile_requests` and the existing EvaluationNode matrix,
not a separate controller. The default smoke path above is unchanged.
`--generation` accepts JSON overrides, e.g. output budget or reasoning effort.
For an infer-stack LiteLLM gateway, `--provider litellm-proxy` selects Inspect's
native proxy client, including model metadata lookup and normal termination
at context limits. The default remains `openai`. Command-scoped leasing passes
the same gateway key under the client's required `LITELLM_PROXY_API_KEY` name;
no second credential is configured. The proxy client uses chat completions
without streaming.

Each shard has its own native result, trajectory and cache identity. Rerunning
the same configuration reuses completed shards and retries unfinished work
through the ordinary scheduler. Model failures scored zero remain zero;
operational failures supply no score. The generated card publishes measurements
without a scientific Boolean claim. Partial readouts remain available.
Native grading runs once; no oracle/NOP or fresh regrade is added to this path.
Verified selects the native `swe_bench_scorer` metric `mean` (resolved fraction),
not `accuracy`. A selector correction reuses the same native measurement.

Use `magnet results table RECORD --node evaluate` for per-shard measurements.
An explicit `--aggregate '{"metrics.evaluate.score":"mean"}'` reports the
mean and observed/missing counts. A full benchmark score requires the full
selected coverage; a partial mean is not the full benchmark score.
`--lease_ttl` and `--lease_timeout` configure command-scoped leases in the
generated node; completion/cancellation still releases them. An optional
`--timeout_seconds` bounds one evaluation worker without changing the benchmark
score or retrying it internally.

Scheduling renders declared requests without importing engines, querying
infer-stack or running native workers. The target interpreter and container
need not exist on the planning machine. Kwdagger node IDs describe the declared
work; native measurement identities belong to the evaluator's result store.

When a leased job executes, its gate binds serving provenance, resolves in the
target environment, and checks that store. Completed native measurements are
reused without acquiring a model lease. Otherwise the leased realization is
checked against those runtime facts. Mutable or unresolved scientific inputs
disable native reuse. The request handoff file lives in the already-shared
node output directory and contains no credentials. Explicit conflicting model
cache tokens still fail at execution; `verify_serving_provenance: false` retains
caller-owned identity assertions.

Existing native stores and old generated commands remain supported. Switching
to declared-config node IDs can change the outer node directory, while the
same runtime request still reuses its original native bundle.

For outer Docker workers, mount the worker environment and its Python runtime,
both source checkouts, task inputs, output/store directories, Docker CLI/plugins
and `/var/run/docker.sock` at their host paths. Forward the Docker socket's
group with `--group-add`. The public lease descriptor is mounted read-only at
execution time. Preflight uses the same container wrapper as execution.

Inspect patches can be exported from the referenced run and checked in fresh
official grader containers using the evaluator's
`dev/regrade_swebench_verified.py`. Pro generation patches and replay lineage
stay in the referenced native bundle. Errors and incomplete grading never
supply a claim-facing zero score.

Run CPU/Docker acceptance with:

```bash
AIQ_MAGNET_EVALS_DIR=/path/to/aiq-magnet-evals \
INFER_STACK_DIR=/path/to/infer_stack dev/ci/swebench_vm_acceptance.sh
```

This uses real Harbor containers, deterministic tool calls and infer-stack's
null serving backend. It proves protocol and scheduling behavior; it does not
load model weights or establish GPU/model capability.
