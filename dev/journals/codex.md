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
