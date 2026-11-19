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
