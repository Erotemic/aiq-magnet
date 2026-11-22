## 2026-10-13 17:06:00 -0400

GPT-6 (Codex; weights/config variant unknown). An operator needed a shared
serving allocation to own devices once while CPU DAG clients borrowed its
endpoints. Per-client reservations can duplicate physical accounting around
coalesced engines; simply disabling leases would lose the lifetime contract.

Added opt-in invocation-level pool descriptors to LeaseSettings and the
evaluation CLI. LeaseCapability wraps only endpoint-using nodes with a
stdlib client guard. Slurm owns physical lifetime; the infer-stack ledger
owns logical endpoint protection. Descriptors stay private and are not a
second mutable lease authority. The guard checks both authorities, bounds
concurrent clients with kernel locks, masks CUDA devices and stops children
on owner loss. Existing command leasing and computation identities remain
unchanged. The scheduler integration must still stop engines and queued work;
hard-kill/host-crash cleanup is deliberately not promised here.

Validation: focused guard/leasing tests pass, including a real child stopped
on owner loss, released/expired claims, unsafe descriptor permissions,
environment allowlisting, GPU-reserving client refusal, slot locking and
unchanged process identity. Existing evaluation, parameter, container,
process and identity tests pass. A downstream local Slurm/null-backend HTTP
fixture also stopped CPU clients and released the lease after owner scancel;
that is lifecycle evidence, not real-GPU acceptance. No workload-specific
names, sampling policy or model selection enter this public implementation.

## 2026-11-18 21:01:37 -0500

Added a measurement-first result contract: recipes publish named scalar/table
symbols from independent readout nodes, and `evaluation.json` records the
available values and source computations. Claims are optional. Existing
assertion records remain compatible, while new reports and the reader API
present measurements without a Boolean headline.

The important boundary is the result set. A model-level table, a task summary
and per-observation scores cannot be treated as one row merely because they
belong to one recipe. Sources remain independent; expansion selects one table
and reductions are explicit. Nulls, empty tables and missing bindings remain
distinguishable. File-backed tables avoid copying detailed observations into
every snapshot, and exports retain portable locations and record identities.

I am confident in keeping this outside execution identity. Labels and table
selections do not become algorithm parameters, and further scientific work is
still a normal configured node. The remaining concern is interpretation:
units and descriptions cannot establish comparability, weighting, uncertainty
or practical benefit. The API deliberately exposes those choices rather than
trying to infer them. A dashboard migration remains separate consumer work.

Validation covered legacy cards, optional claims, independent result grains,
unknowns, source scope, cache reuse, named variants, detached reading and
portable file tables. The broad suite passed 412 tests with 29 skipped; its
remaining concurrent-engine test passed after isolating tmux from an unrelated
server environment. Focused reader/collection checks and a packaged CPU-only
example also passed. Built a wheel and verified the example/code assets ship.

## 2026-11-21 12:25:57 -0500

Model: GPT-6 (Codex); exact serving variant/configuration unavailable.

The user approved extending the existing coding-benchmark recipe helper for
caller-selected Verified evaluations. The helper now compiles the evaluator's
independent requests into an ordinary EvaluationNode task-options matrix.
Its measurement-only readout declares score, eligibility and native bundle
paths. Optional generation and lease/runtime budgets are configuration.
The original smoke recipe and claim behavior remain the default path.

A pinned Inspect synthetic probe exercises the real scheduler, worker and
result store: a successful zero is retained, an injected infrastructure failure
stays unscored, and a second invocation reruns only that failed request.
Published measurement rows preserve the resulting 0/1 values. The example
suite passed 22 tests with two unrelated engines unavailable; additional
selection/lease-budget checks and all three original smoke compilations pass.
A 500-request selection also compiled without resolving engines or leasing.

The fresh test worker initially failed because unbounded dependency resolution
selected agent-client-protocol 1.0, whose acp.helpers API is incompatible with
the pinned Inspect runtime. Reinstalling with the evaluator's existing Python
3.12 constraints restored native execution. Reusing the recorded environment
is preferable to inventing a second worker bootstrap. This change adds no
campaign controller or second grading implementation.

## 2026-11-21 18:55:30 -0500

Model: GPT-6 (Codex); exact serving variant/configuration unavailable.

The user reported a queue of failed native evaluation jobs. Scheduling and
execution were resolving the same request through different task checkouts:
a pre-existing tmux server did not inherit the scheduler's exported PYTHONPATH.
The resulting identity mismatch stopped evaluation before native execution.
Changing only the task-source hash in the scheduled identity exactly reproduced
the observed runtime identity, so bypassing the identity gate would have hidden
a real source discrepancy.

Added opt-in import-path capture to host_interpreter, enabled for EvaluationNode
resolution, gate, child and completion commands. Existing generic host nodes
retain their previous behavior. Capturing an unset path as empty also prevents
a stale worker path from taking precedence. Failed identity checks retain their
resolved source facts for diagnosis without another probe of the running job.

A private tmux server seeded with a conflicting task path ran four native
Inspect evaluations through real null-backend leases. All used the scheduler's
source, emitted valid results and released their leases. Existing leased
primary/grader, result reuse and configuration-drift cases also pass. Serial
subprocess tests were insufficient here: they inherited the same environment
that scheduled the work. Test the persistent queue server's environment as a
separate execution context whenever render-time source resolution matters.

The broader container/native-integration/example suite passed 78 cases with
three unavailable optional-engine cases skipped. One assertion searched for a
program-name substring anywhere in a command, including captured directory
paths; checking actual shell tokens fixes that false positive, and its rerun
passes. With the four-worker regression and three existing leased Inspect
cases, 83 exercised cases pass. Focused lint and whitespace checks pass.

## 2026-11-22: reuse Inspect's native proxy client

Model: GPT-6 (Codex); exact serving variant/configuration unavailable.

The coding-benchmark helper now exposes its existing ModelBinding provider
choice, retaining the OpenAI default. Selecting `litellm-proxy` uses native
Inspect metadata/error handling and nonstreaming chat requests. For leased
proxy models, lease_runtime aliases the same gateway key to the SDK's expected
environment variable. No additional credential, gateway service or provider
registry is introduced. An unleased auxiliary model does not trigger the alias.

A real null-backend infer-stack lease and pinned Inspect ReAct run exercise
metadata lookup, a tool call, context-stop scoring, inherited-key replacement,
credential redaction and lease release. That test and three existing native
OpenAI primary/grader, reuse and serving-drift cases pass. Recipe/unit checks
also pass. The evaluator owns two further native completion/context fixtures;
no live model performance is established by these tests.
