# Changelog

This changelog follows the specifications detailed in: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), although we have not yet reached a `1.0.0` release.

### A leased node is confined to the GPUs Slurm gave it

`infer-stack run` was rendered without `--allowed_gpus`, so every DAG node
planned against every GPU on the box rather than the ones its job was
allocated. Two nodes then placed model servers on the same card and one died
with CUDA OOM.

Nothing in the environment covered for it. `aiq-gpu` sets
`ConstrainDevices=yes` but `TaskPlugin=task/none`, and ConstrainDevices does
nothing without task/cgroup, so no device cgroup is ever created: measured
inside a 2-GPU allocation, `nvidia-smi -L` listed all four cards. infer-stack
takes its inventory from that list. `INFER_STACK_ALLOWED_GPUS` is not a
fallback either -- it is read by the catalog commands, not by `acquire`/`run`
-- so the allow-list has to be on the command line.

The value cannot be known when the command is rendered: the DAG is built on
the submit host, where no allocation exists. So the command carries shell text
that resolves at job time, reading `SLURM_JOB_GPUS` and falling back to
`SLURM_STEP_GPUS` (under `srun` the step variable is the one that is set).
`CUDA_VISIBLE_DEVICES` is deliberately not consulted: it may hold GPU UUIDs,
which infer-stack's `int()` parse rejects.

It expands to nothing at all when neither variable is set, so the tmux backend
renders exactly the command it did before. `MAGNET_LEASE_ALLOWED_GPUS=0`
suppresses it.

### tmux queue sessions name their run

Every card's queue was called `schedule-eval`, so cmd_queue's tmux backend --
which matches sessions on that name to decide what is a conflict -- treated
unrelated runs as conflicting and offered to kill them.

kwdagger cannot name it: MAGNET passes the pipeline as a DAG object inside
`params` rather than as the `pipeline` spec string kwdagger derives a name
from, and a `Pipeline` has no name of its own. MAGNET does know, because
`root_dpath` is `<run>/evaluation_runs/<hash>/kwdagger`, so the queue is now
named for the run directory: `schedule-incubilate_lift_scaled-up`.

Two runs of the same card still share a name, which is correct -- that is a
real conflict. Different cards no longer collide.

### A card change no longer recomputes the cells it did not change

The DAG's node artifacts move from `<output>/<card hash>_<timestamp>/kwdagger`
to `<output>/_kwdagger`, shared across card versions.

kwdagger already identifies a node by hashing its own configuration, so an
unchanged node keeps its id when a different part of the card changes. Rooting
the DAG under a hash of the WHOLE card discarded that: adding one model to a
13-model cohort moved all 48 unchanged shards to a new path, so the
`test -e <artifact>` guard missed on every one and two hours of unchanged work
was recomputed.

Sharing the root is safe because collection is instance-driven --
`collect_result_cells` asks the DAG where its artifact is rather than globbing
the tree, so a card reads only the nodes its own matrix configured. Two cards
that configure a node identically produce the same id, and the same id means
the same computation.

Per-run provenance (`card.yaml`, `results/`, `symbol_metadata.json`) stays
under the card-hash directory, so what produced a result is still recorded
exactly. Artifacts from before this change are orphaned and recomputed once.

## Unreleased

### Added

* Cards may declare `evidence`: what was measured, the relation asserted, and
  what it is evidence for (`supports`, `scope`, `relaxes`). Records are written
  to `evidence.json` beside `verdict.json`. A card may define `evidence`
  instead of `claim`, in which case the evidence decides each cell.
* Pipeline results are reachable from a claim by their qualified names, e.g.
  `metrics.<node>.<name>`.

### Changed

* A `result_node` with more than one configured instance is now one cell of
  the card each, evaluated and written separately, rather than an error. The
  parameters that distinguish the instances bind as symbols.
* Terminal artifacts are read from each configured instance's own directory
  instead of by globbing the run tree.

### Fixed

* Re-running an unchanged card reuses its run directory instead of creating a
  new one stamped with the current second. The DAG's root lives inside that
  directory, so a fresh name every run meant `skip_existing` always arrived at
  an empty tree and recomputed every node. Editing the card changes its id and
  still starts a new directory.
* `EvaluationCard._run_hash` is computed once per instance rather than on every
  read. It called `datetime.now()` each time, so two readers would have
  disagreed about where the run was written.

* Evaluation card symbols may declare dependencies as either `depends_on` or
  `depends`. Only the former was read, so the latter was silently dropped and
  resolution order fell back to declaration order in the YAML.

### Changed

* `magnet evaluate` now warns when a symbol spec contains an unrecognized key,
  instead of ignoring it silently.

## Version 0.0.2 -- Released 2026-05-08

### Added

* Added per-instance predictor base class (`InstancePredictor`) and random example
* User can now specify patterns to helm runs, suites, or all outputs as predictor input
* Added symbol sweeping capability to evaluation card evaluator
* Added modal CLI for `evaluation.py` script
* Added support for KWDagger pipelines in evaluation cards (both as explicit pipelines, and YAML defined pipelines)
* Added support for symbol overrides to `magnet evaluate` with the `--override` argument
* Added parallelization to `magnet evaluate` with the `--jobs` (and `--parallel_backend`) arguments
* Added claim resolution and final result file output to `magnet evaluate`
* Added support for `claim_aggregation_strategy` to evaluation cards (supporting `any`, `all`, and `fraction` strategies)

### Changed

* Switched to single argument path input for example predictors
* Cleaned up predicted vs. actual code for predictors
* HelmRuns.coerce can now accept a more expressive set of inputs
* BREAKING: You must how specify `helm_runs` when calling the predictor.
* `magnet download helm` can now download multiple benchmarks

### Fixed

* Fixed doctests and README wrt predictor refactors
* Updated `predict_inputs_exploration.ipynb` notebook wrt API updates

## Version 0.0.1 -- Released 2025-10-28

* Initial release; includes minimum working implementations for:
  * Evaluation card specification and evaluation
  * [HELM](https://github.com/stanford-crfm/helm) benchmark output downloading and data interfaces
  * Benchmark `Predictor` class (with random, and perturbation based examples)
  * Utility for "offline" HELM perturbation application
  * Ad-hoc inference and direct model access through HELM
  * Command-line wrapper for `helm-run` supporting runs against "offline" dataset instances
