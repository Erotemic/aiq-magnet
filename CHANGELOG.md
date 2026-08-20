# Changelog

This changelog follows the specifications detailed in: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), although we have not yet reached a `1.0.0` release.

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
