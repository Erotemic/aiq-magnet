import builtins
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime
from graphlib import TopologicalSorter
from itertools import product
from statistics import fmean
from typing import Any, Dict, List, Optional, Self, Tuple, get_args, get_origin

import kwutil
import safer
import kwconf
import ubelt as ub
import yaml
from kwdagger import Pipeline, ProcessNode
from kwdagger.schedule import ScheduleEvaluationConfig, build_schedule
from loguru import logger
from pydantic import ValidationError
from rich import print

from magnet.utils.util_logger import setup_logging
from magnet.schema import EvaluationCardSchema, MetricObjective
from magnet.theory.cards import basis_from_card

SAFER_USE_TEMPFILE = not ub.WIN32

DEFAULT_CLAIM_AGGREGATION_STRATEGY = {'type': 'all'}
DEFAULT_METRIC_AGGREGATION_STRATEGY = {'type': 'mean'}



def resolve_queue_backend(requested: str | None = None) -> str:
    """
    Choose the cmd_queue backend a card's DAG is scheduled onto.

    Args:
        requested (str | None): an explicit choice. ``None`` reads
            ``MAGNET_QUEUE_BACKEND``, then falls back to ``'tmux'``.

    Returns:
        str: a backend cmd_queue reports as available.

    Defaults to ``tmux`` even at size 1: the same jobs run in the same order as
    ``serial``, but with a live monitor and a separate log per job rather than
    one interleaved stream. ``serial`` is right for CI and pytest, so an
    unavailable backend degrades to it with a notice rather than raising.

    Example:
        >>> from magnet.evaluation import resolve_queue_backend
        >>> resolve_queue_backend('serial')
        'serial'
    """
    import cmd_queue

    if requested is None:
        requested = os.environ.get('MAGNET_QUEUE_BACKEND') or 'tmux'
    requested = requested.strip()

    try:
        available = set(cmd_queue.Queue.available_backends())
    except Exception:
        return requested

    if requested in available:
        return requested
    if requested != 'serial':
        logger.warning(
            f'queue backend {requested!r} is not available '
            f'(have: {sorted(available)}); falling back to serial. '
            'Install tmux for a live monitor and per-job logs.'
        )
    return 'serial'


def _tmux_workers() -> int | None:
    """How many queue workers may run at once, or None for the default.

    This is a GPU-safety knob, not a throughput one. A LeasedProcessNode holds
    its answerer while it waits for the extractor it also needs, so if enough
    shards start at once to claim every GPU, none of them can ever get the
    extractor and none will release: the answerers are waiting on a model that
    has nowhere left to be placed. Observed on a 4-GPU host -- four answerers
    on GPUs 0-3, the shared extractor unplaceable, eight leases queued behind
    it, zero rows produced in an hour.

    Concurrency must therefore stay at or below (GPUs - 1) for a cohort with a
    shared single-GPU extractor. MAGNET cannot know the GPU count, so the
    runner sets this.
    """
    raw = os.environ.get('MAGNET_TMUX_WORKERS', '').strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def _queue_name_for(root_dpath) -> str:
    """A tmux queue name that says which run these sessions belong to.

    cmd_queue's tmux backend matches sessions on the queue name to decide
    which ones are "this queue", and kwdagger's default cannot help here:
    MAGNET passes the pipeline as a DAG OBJECT inside ``params``, not as the
    ``pipeline`` spec string kwdagger names itself from, and a Pipeline has no
    name of its own. So every card fell back to the literal 'schedule-eval'
    and shared one namespace -- starting an Incubilate run reported a
    Princeton run's sessions as conflicts.

    MAGNET does know: ``root_dpath`` is ``<run>/evaluation_runs/<hash>/kwdagger``,
    so the run directory names it. Two runs of the same card share a name,
    which is right -- that is a real conflict. Different cards do not.
    """
    import re

    try:
        parts = ub.Path(root_dpath).absolute().parts
        idx = len(parts) - 1 - parts[::-1].index('evaluation_runs')
        name = parts[idx - 1]
    except (ValueError, IndexError, TypeError):
        return 'schedule-eval'
    name = re.sub(r'[^A-Za-z0-9_.-]', '_', str(name))
    return f'schedule-{name}' if name else 'schedule-eval'


class EvaluationConfig(kwconf.Config):
    """
    Resolve an Evaluation Card
    """

    __epilog__ = """
    Usage:
      ./evaluation.py <evaluation_card_path>

    Examples:
      # Show docs
      python -m magnet.evaluation --help

      # Run example card
      python -m magnet.evaluation magnet/cards/simple.yaml
    """

    path: str = kwconf.Value(
        None, required=True, position=1, help='Path to evaluation card YAML'
    )

    output_path: str = kwconf.Value(
        './evaluation_runs', help='Root data path for saved results'
    )

    override: str | None = kwconf.Value(
        None,
        parser=str,
        help='Override symbol values (e.g. --override dataset: legalbench\nnum_replicates: 5)',
    )

    jobs: int = kwconf.Value(
        1,
        parser=int,
        help=(
            'Number of evaluation jobs. Use 1 for serial execution, '
            '-1 for all available CPUs when using joblib.'
        ),
    )

    parallel_backend: str = kwconf.Value(
        'loky',
        parser=str,
        choices=['loky', 'threading', 'multiprocessing'],
        help='Joblib backend used when --jobs is not 1.',
    )

    queue_backend: str | None = kwconf.Value(
        None,
        parser=str,
        help=(
            'cmd_queue backend the card DAG is scheduled onto: tmux, serial, '
            'slurm, airflow. Defaults to $MAGNET_QUEUE_BACKEND, then tmux, '
            'falling back to serial when unavailable. Prefer tmux even at '
            'size 1: same jobs in the same order, but with a live monitor and '
            'a separate log per job instead of one interleaved stream.'
        ),
    )

    verbose: bool = kwconf.Value(
        False, isflag=True, help='Verbose log output', group='logging'
    )

    validate: str = kwconf.Value(
        'error',
        parser=str,
        choices=['only', 'error', 'warning', 'off'],
        help=(
            "'only': validate schema and exit. "
            "'error': validate and raise on failure (default). "
            "'warning': validate, warn on failure, and proceed. "
            "'off': skip validation entirely."
        ),
    )


# Claim Resolution (pulled out as standalone function for
# multiprocessing support)
def _run_one(
    evaluation: 'EvaluationTask', claim_results_path: ub.Path
) -> Tuple[str, ub.Path, str, Dict[str, Any]]:
    status, _ = evaluation.execute()
    execution_hash = evaluation._execution_hash
    resolved_symbols = evaluation.log['symbols']
    results_fpath = claim_results_path / execution_hash / 'verdict.json'
    results_fpath.parent.ensuredir()

    with safer.open(results_fpath, 'w', temp_file=SAFER_USE_TEMPFILE) as f:
        json.dump(evaluation.log, f, indent=2, ensure_ascii=False)
        f.write('\n')

    return (status, results_fpath, execution_hash, resolved_symbols,
            evaluation.evidence)


class EvaluationCard:
    """
    Specification of an empirical claim with resolvable symbols and metadata

    Example:
        >>> from importlib.resources import files
        >>> from magnet.evaluation import EvaluationCard
        >>> card_name = 'simple.yaml'
        >>> card_path = files('magnet') / 'cards' / card_name
        >>> output_path = './results'
        >>> card = EvaluationCard(card_path, output_path)
        >>> card.evaluate()
        'VERIFIED'
        >>>
        >>> # Replacement example
        >>> import kwutil
        >>> example_symbols = kwutil.Yaml.coerce(
            '''
            symbols:
              data_path:
                type: str
                value: './data/runs'
              confidence:
                type: float
                value: 0.1
              model:
                sweep:
                  - llama-2-13b
                  - gpt-5.4-pro
            ''')
        >>> card.symbols = example_symbols.get('symbols')
        >>> def show_symbol_values(symbols):
        >>>   # Print out symbol resolution
        >>>   for symbol in symbols:
        >>>     if 'sweep' in symbols[symbol]:
        >>>       print(f"{symbol}: {symbols[symbol]['sweep']}")
        >>>     else:
        >>>       print(f"{symbol}: {symbols[symbol]['value']}")
        >>>
        >>> show_symbol_values(card.symbols)
        data_path: ./data/runs
        confidence: 0.1
        model: ['llama-2-13b', 'gpt-5.4-pro']
        >>> override = '''
            confidence: 0.01
            model: [claude-3.5-sonnet, gemini-1.5-pro-001]
        '''
        >>> card.replace(override)
        >>> show_symbol_values(card.symbols)
        data_path: ./data/runs
        confidence: 0.01
        model: ['claude-3.5-sonnet', 'gemini-1.5-pro-001']
    """

    def __init__(
        self, path, output_path: str | os.PathLike[str], validate='error'
    ):
        with open(path, 'r') as f:
            cfg = yaml.safe_load(f)
        if validate in ('error', 'warning'):
            try:
                EvaluationCardSchema.model_validate(cfg)
            except ValidationError as e:
                if validate == 'error':
                    raise e
                logger.warning(
                    f'WARNING! Card validation failed with error:\n{e}'
                )

        self.original_card = cfg
        self.output_path = ub.Path(output_path)
        # Formalization paths in the theory block are relative to the card.
        self.card_dpath = ub.Path(path).parent
        self.basis = None

        self.title = cfg.get('title', '')
        self.description = cfg.get('description', '')

        self.claim = Claim(cfg.get('claim') or {})
        self.claim_aggregation_strategy = cfg.get(
            'claim_aggregation_strategy', DEFAULT_CLAIM_AGGREGATION_STRATEGY
        )
        self.symbols = cfg.get('symbols', {})

        self.evidence_spec = cfg.get('evidence', [])

        # explicit kwdagger spec
        self.has_kwdagger = 'kwdagger' in cfg
        self.kwdagger = cfg.get('kwdagger')
        if self.has_kwdagger:
            self.kwdagger = _resolve_pipeline_path(
                self.kwdagger, self.card_dpath)

        # populate ProcessNode(s) programmatically
        self.has_pipeline = 'pipeline' in cfg
        self.pipeline = cfg.get('pipeline')

        self.evaluations = []
        self._run_hash_cached: str | None = None

    def status(self) -> str:
        """
        Declaration of card state, whether not started, in progress, or complete
        """
        if self.claim.status == 'UNVERIFIED' and len(self.evaluations) > 0:
            not_evaluated_count = sum(
                [
                    evaluation.claim.status == 'UNVERIFIED'
                    for evaluation in self.evaluations
                ]
            )
            percent_not_evaluated = not_evaluated_count / len(self.evaluations)

            if percent_not_evaluated == 0:
                return 'EVALUATED'
            else:
                return f'{percent_not_evaluated:.2f} REMAINING'
        else:
            return 'EVALUATED'

    def replace(self, override_str: str) -> None:
        """
        Handle overrides in symbol field by replacing 'value' entries and appending to sweeps
        """
        override = _plain_data(kwutil.Yaml.coerce(override_str))

        for key, value in override.items():
            if key not in self.symbols:
                raise ValueError(
                    f"Unknown symbol '{key}' -- available: {list(self.symbols.keys())}"
                )
            if 'value' in self.symbols[key]:
                # replacement
                self.symbols[key]['value'] = value
            elif 'sweep' in self.symbols[key]:
                if isinstance(value, list):
                    self.symbols[key]['sweep'] = value
                else:
                    self.symbols[key]['sweep'] = [value]

    @property
    def dag_root_dpath(self) -> Any:
        """Where the DAG's node artifacts live: ``<output>/kwdagger``.

        Deliberately NOT under ``<output>/<card hash>/``. kwdagger already
        identifies a node by hashing its own configuration, so an unchanged
        node keeps its id when a different part of the card changes -- that id
        is the invalidation mechanism, and it is exact. Rooting the DAG under a
        hash of the WHOLE card threw that away: adding one model to a 13-model
        cohort moved all 48 unchanged shards to a new path, so
        ``test -e rows.json`` missed and every one of them recomputed. Two hours
        to redo work that had not changed.

        Sharing a root across card versions is safe because collection is
        instance-driven: ``collect_result_cells`` asks the DAG where its
        artifact is rather than globbing the tree, so a card reads only the
        nodes its own matrix configured. Two cards that configure a node
        identically produce the same id, and the same id means the same
        computation -- reuse is correct, not a collision.

        Per-run provenance (``card.yaml``, ``results/``, ``symbol_metadata``)
        stays under the card-hash directory, so what produced a result is still
        recorded exactly.
        """
        # Underscore-prefixed so it is not mistaken for a run directory --
        # run dirs are ``<card hash>_<timestamp>``, and the repo's collectors
        # already skip ``_``-prefixed names.
        return self.output_path / '_kwdagger'

    def evaluate(
        self,
        jobs: int = 1,
        parallel_backend: str = 'loky',
        verbose: bool = False,
    ) -> str:
        """
        Run the evaluation specification

        1. Resolve symbol definitions
        2. Evaluate claim under symbol values
        3. Write out results
        4. Summarize general finding

        Assumes user provides input path up to (*)
        e.g.
        ├── Milestone
        │   ├── Organization
        │   │   └── Consistency_Algorithm (*)
        │   │       └── ab0012cf_2026-04-21__12-23-34
        │   │           ├── card.yaml
        │   │           └── kwdagger
        │   │           └── results
        """
        results = []

        card_output_path = self.output_path / self._run_hash
        card_output_path.ensuredir()

        setup_logging(verbose, card_output_path)

        with safer.open(
            card_output_path / 'card.yaml', 'w', temp_file=SAFER_USE_TEMPFILE
        ) as f:
            yaml.safe_dump(self.original_card, f, sort_keys=False)

        claim_results_path = card_output_path / 'results'

        raw_symbol_metadata = _parse_symbol_metadata(self.symbols)

        if raw_symbol_metadata:
            with safer.open(
                card_output_path / 'symbol_metadata.json',
                'w',
                temp_file=SAFER_USE_TEMPFILE,
            ) as f:
                json.dump(raw_symbol_metadata, f, indent=2, ensure_ascii=False)

        if self.has_kwdagger:
            processor = KWDaggerProcessor(
                self.kwdagger, root_dpath=self.dag_root_dpath
            )

            if processor.result_node:
                # The DAG is authoritative: each result instance is one cell
                # of the card, evaluated against what that instance computed.
                cells = processor.collect_result_cells()

                for cell in cells:
                    # Coordinates bind as ordinary symbols so each cell hashes
                    # -- and so writes its verdict -- separately. The results
                    # themselves stay qualified, out of the hash.
                    cell_symbols = dict(self.symbols)
                    for name, value in cell['coords'].items():
                        if name in cell_symbols:
                            raise ValueError(
                                f'result node parameter {name!r} collides '
                                f'with a card symbol of the same name'
                            )
                        cell_symbols[name] = {'value': value}

                    self.evaluations.extend(self.dispatch(
                        Symbols.decompose_symbol_defs(cell_symbols),
                        results=cell['results'],
                    ))

                with safer.open(
                    card_output_path / 'result_cells.json',
                    'w',
                    temp_file=SAFER_USE_TEMPFILE,
                ) as f:
                    json.dump(cells, f, indent=2, ensure_ascii=False)
                    f.write('\n')
            else:
                # No declared result node: results are rediscovered from the
                # run tree and the claim is replayed for each one.
                kwdagger_results, symbols = processor.collect_results()

                for sweep in symbols:
                    symbol_with_value = {
                        s: {'value': v} for s, v in sweep.items()
                    }
                    self.symbols.update(symbol_with_value)
                    self.evaluations.extend(
                        self.dispatch(
                            Symbols.decompose_symbol_defs(self.symbols)
                        )
                    )

        elif self.has_pipeline:
            # Implicit pipeline definition needs parsing
            pipeline_runs = GenericPipelineProcessor(
                self.pipeline, root_dpath=self.dag_root_dpath
            ).collect_symbols()

            for run in pipeline_runs:
                run_symbols = pipeline_runs[run]
                self.symbols.update(run_symbols)
                self.evaluations.extend(
                    self.dispatch(Symbols.decompose_symbol_defs(self.symbols))
                )

        else:
            # Serial Evaluation Card
            self.evaluations = self.dispatch(
                Symbols.decompose_symbol_defs(self.symbols)
            )

        if jobs == 1:
            out = [_run_one(e, claim_results_path) for e in self.evaluations]
        else:
            from joblib import Parallel, delayed

            out = Parallel(n_jobs=jobs, backend=parallel_backend, verbose=5)(
                delayed(_run_one)(e, claim_results_path)
                for e in self.evaluations
            )

        results = []
        resolved_symbols = []
        claim_hashes = []
        evidence_records = []
        for status, results_fpath, execution_hash, symbols, evidence in out:
            results.append(status)
            resolved_symbols.append(symbols)
            claim_hashes.append(execution_hash)
            if evidence:
                evidence_records.append(
                    {'claim': execution_hash, 'symbols': symbols,
                     'evidence': evidence}
                )
            logger.info(f'Wrote claim output to {results_fpath}')

        calculated_metrics = {}

        if raw_symbol_metadata:
            metric_definitions = Metric.build_metrics_from_symbol_metadata(
                raw_symbol_metadata
            )
            calculated_metrics = _calculate_metrics(
                metric_definitions,
                resolved_symbols,
                raw_symbol_metadata,
            )

            if calculated_metrics:
                metric_statement = (
                    '================================\n Evaluation Metrics:\n'
                )
                for metric, value in calculated_metrics.items():
                    metric_statement += f'  {metric}: {value: .3f}\n'
                logger.info(metric_statement[:-1])

        total = len(results)

        def percentage(count):
            return count / total

        verified_count = results.count('VERIFIED')
        falsified_count = results.count('FALSIFIED')
        inconclusive_count = results.count('INCONCLUSIVE')

        logger.info('================================')
        logger.info(f'Settings Evaluated: {total}')
        logger.info(f'  Verified:     {percentage(verified_count):.2f}')
        logger.info(f'  Falsified:    {percentage(falsified_count):.2f}')
        logger.info(f'  Inconclusive: {percentage(inconclusive_count):.2f}')
        logger.info('================================')
        logger.info('\n')

        card_result = _reduce_results(results, self.claim_aggregation_strategy)
        aggregate_verdict = {
            'result': card_result,
            'claim_aggregation_strategy': self.claim_aggregation_strategy,
            'claims': claim_hashes,
        }

        if raw_symbol_metadata and calculated_metrics:
            aggregate_verdict['metrics'] = calculated_metrics

        with safer.open(
            card_output_path / 'verdict.json', 'w', temp_file=SAFER_USE_TEMPFILE
        ) as f:
            json.dump(aggregate_verdict, f, indent=2, ensure_ascii=False)
            f.write('\n')

        if evidence_records:
            # Beside the verdict, not inside it: the verdict answers whether
            # the evidence was sufficient, and stays readable on its own.
            with safer.open(
                card_output_path / 'evidence.json', 'w',
                temp_file=SAFER_USE_TEMPFILE,
            ) as f:
                json.dump({
                    'result': card_result,
                    'sufficiency': self.claim_aggregation_strategy,
                    'cells': evidence_records,
                }, f, indent=2, ensure_ascii=False)
                f.write('\n')

        self.basis = basis_from_card(self.original_card, root=self.card_dpath)
        if self.basis is not None:
            with safer.open(
                card_output_path / 'theory.json', 'w', temp_file=SAFER_USE_TEMPFILE
            ) as f:
                json.dump(self.basis.to_dict(), f, indent=2, ensure_ascii=False, default=str)
                f.write('\n')

        self.claim.status = card_result
        return card_result

    def dispatch(
        self,
        flattened_sweep: List['Symbols'],
        results: Dict[str, Any] | None = None,
    ) -> List['EvaluationTask']:
        return [
            EvaluationTask(
                Claim({'python': self.claim.claim}), symbols,
                results=results, evidence_spec=self.evidence_spec,
            )
            for symbols in flattened_sweep
        ]

    def summarize(self) -> None:
        """
        Human-readable summary of card in its current state
        """
        logger.info(f'[bold]Title:[/bold]       {self.title}')
        logger.info(f'[bold]Description:[/bold] {self.description}')
        logger.info('================================')
        # logger.info(f"SYMBOLS:     {self.symbols()}")
        logger.info(f'[bold]CLAIM:[/bold]       \n{self.claim}')

        status = self.status()
        if self.claim.status == 'VERIFIED':
            claim_status_color = 'green'
        elif self.claim.status == 'FALSIFIED':
            claim_status_color = 'red'
        else:
            claim_status_color = 'yellow'

        if status == 'EVALUATED':
            logger.info('================================')
            logger.info(
                f'[bold]RESULT:[/bold]      [bold][{claim_status_color}]{self.claim.status}[/{claim_status_color}][/bold]'
                ''
            )

        logger.info('================================')
        logger.info(f'[bold]CARD STATUS:[/bold] {status}')

    @property
    def _card_hash(self) -> str:
        """The card's content; the same card is the same id on any day."""
        return ub.hash_data(self.original_card)[:8]

    @property
    def _run_hash(self) -> str:
        """
        This card's directory: ``<card id>_<when it first ran>``.

        Re-running an unchanged card returns the directory it already has,
        rather than a new one stamped with the current second. That is what
        lets a pipeline find the cells it already computed -- the DAG's root is
        inside this directory, so a fresh name every run meant `skip_existing`
        arrived at an empty tree and refit everything. Editing the card changes
        the id, which correctly starts a new directory and invalidates the
        cells.

        Computed once per instance. It used to be recomputed on every read,
        which was harmless only because there was exactly one reader; a second
        would have been handed a path that was never created.
        """
        if self._run_hash_cached is None:
            existing = [
                p for p in sorted(self.output_path.glob(f'{self._card_hash}_*'))
                if p.is_dir()
            ]
            if existing:
                newest = max(existing, key=lambda p: p.stat().st_mtime)
                self._run_hash_cached = newest.name
            else:
                timestamp = datetime.now().strftime('%Y-%m-%d__%H-%M-%S')
                self._run_hash_cached = f'{self._card_hash}_{timestamp}'
        return self._run_hash_cached


class GenericPipelineProcessor:
    """
    Handler for yaml-based pipeline specification

    NOTE:
        *possibly merge with KWDaggerProcessor*

    Example:
        >>> from magnet.evaluation import GenericPipelineProcessor
        >>> import kwutil
        >>> # Example snippet of an Evaluation Card
        >>> example_cfg = kwutil.Yaml.coerce(
            '''
            pipeline:
              predict_node:
                executable: python -m magnet.examples.llama_consistency.llama_predict
                algo_params:
                  base_model: ["meta/llama-2-13b", "meta/llama-2-70b"]
                  comp_model: ["meta/llama-2-7b", "meta/llama-3-70b"]
                out_paths:
                  results_fpath: ./llama_results.json
            ''')
        >>> root_dpath = "."
        >>> pipeline_def = example_cfg['pipeline']
        >>> pipeline = GenericPipelineProcessor(pipeline_def, root_dpath)
        >>> #
        >>> # Construct One Node Pipeline
        >>> pipeline.define_kwdagger()
        ...
        >>> pipeline.dag.print_graphs()

        Process Graph
        ╙── predict_node

        IO Graph
        ╙── predict_node
            ╽
            results_fpath

        >>> for attr in ['name', 'executable', 'algo_params', 'out_paths']:
        >>>    print(getattr(pipeline.dag.node_dict['predict_node'], attr))
        predict_node
        python -m magnet.examples.llama_consistency.llama_predict
        ['base_model', 'comp_model']
        {'results_fpath': './llama_results.json'}
        >>> #
        >>> # Parameters matrix
        >>> pipeline.matrix
        {'predict_node.base_model': ['meta/llama-2-13b', 'meta/llama-2-70b'],
        'predict_node.comp_model': ['meta/llama-2-7b', 'meta/llama-3-70b']}
    """

    def __init__(
        self, pipeline_def: Dict[str, Any], root_dpath: ub.Path
    ) -> None:
        self.pipeline = pipeline_def
        self.root_dpath = root_dpath
        self.dag = None
        self.matrix = None
        self.symbols = {}

    def define_kwdagger(self) -> None:
        """
        Construct kwdagger pipeline programmatically

        *only verified for one-stage pipeline, needs 'connector' handling*
        """
        nodes = {}

        for node_name in self.pipeline:
            # collect nodes
            node_params = self.pipeline[node_name]

            # FIXME: should update matrix for full pipeline
            node_params, self.matrix = self._parse_params(
                node_name, node_params
            )

            node = ProcessNode(name=node_name, **node_params)
            nodes[node_name] = node

        self.dag = Pipeline(list(nodes.values()))
        self.dag.build_nx_graphs()

    def dispatch(
        self, backend: str | None = None, skip_existing: bool = True,
        **kwargs: Any
    ) -> None:
        self.define_kwdagger()
        backend = resolve_queue_backend(backend)

        kwdagger_params = {'pipeline': self.dag, 'matrix': self.matrix}

        kwd_config = ScheduleEvaluationConfig(
            params=kwdagger_params,  # includes pipeline and additional params
            root_dpath=self.root_dpath,
            queue_name=_queue_name_for(self.root_dpath),
            **({'tmux_workers': _tmux_workers()}
               if _tmux_workers() is not None else {}),
            backend=backend,
            skip_existing=skip_existing,
            run=True,
        )

        dag, queue = build_schedule(kwd_config)

    def collect_symbols(self) -> Dict[str, Any]:
        """
        Collect results (Evaluation Card 'symbols') in place of 'load_result' in the ProcessNode definition
        """
        if not self.symbols:
            self.dispatch()

        # Glob all results json (only one node in pipeline)
        paths = self.root_dpath.glob(
            f'**/{self.dag.node_dict[next(iter(self.dag.node_dict))].out_paths["results_fpath"]}'
        )

        for symbol_resolution in paths:
            symbols = json.load(open(symbol_resolution, 'r'))
            parent_dir = symbol_resolution.parent.stem
            if 'result' in symbols:
                # assume all fields exist
                for symbol in symbols['result']:
                    # record all sweeps
                    if parent_dir not in self.symbols:
                        self.symbols[parent_dir] = {}

                    self.symbols[parent_dir][symbol] = {
                        'value': symbols['result'][symbol]
                    }

        return self.symbols

    def _parse_params(
        self, node_name: str, node_cfg: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Parse sweepable parameters from definition
        """
        matrix = {}
        for k in node_cfg:
            if isinstance(node_cfg[k], dict) and '_params' in k:
                # TODO: Construct a more robust validator
                for param, v in node_cfg[k].items():
                    matrix[f'{node_name}.{param}'] = v
                # decompose yaml
                node_cfg[k] = list(node_cfg[k].keys())
        return node_cfg, matrix


class KWDaggerProcessor:
    """
    Handler for full kwdagger pipeline specification

    Example
        >>> from magnet.evaluation import KWDaggerProcessor
        >>> from kwdagger.schedule import ScheduleEvaluationConfig, build_schedule
        >>> import kwutil
        >>> # Example snippet of an Evaluation Card (related to GenericPipelineProcessor example)
        >>> example_cfg = kwutil.Yaml.coerce(
            '''
            kwdagger:
              pipeline: magnet.examples.llama_consistency.pipelines.llama_pipeline()
              matrix:
                llama_predict.base_model: ["meta/llama-2-13b", "meta/llama-2-70b"]
                llama_predict.comp_model:  ["meta/llama-2-7b", "meta/llama-3-70b"]
            ''')
        >>> root_dpath = "."
        >>> kwdagger_def = example_cfg['kwdagger']
        >>> pipeline = KWDaggerProcessor(kwdagger_def, root_dpath)
        >>> #
        >>> # Construct Two Node Pipeline (llama_predict -> claim)
        >>> kwdagger_spec = ScheduleEvaluationConfig(params=pipeline.spec, run=False)
        >>> dag, queue = build_schedule(kwdagger_spec)
        ...
        >>> dag.print_graphs()

        Process Graph
        ╙── llama_predict
            ╽
            claim_eval

        IO Graph
        ╙── llama_predict
            ╽
            results_fpath
            ╽
            symbols_fpath
            ╽
            claim_eval
            ╽
            verdict_fpath

        >>> #
        >>> # Parameters matrix
        >>> pipeline.spec['matrix']
        {'llama_predict.base_model': ['meta/llama-2-13b', 'meta/llama-2-70b'],
        'llama_predict.comp_model': ['meta/llama-2-7b', 'meta/llama-3-70b']}
    """

    def __init__(
        self, pipeline_def: Dict[str, Any], root_dpath: ub.Path
    ) -> None:
        # ``result_node`` is a MAGNET-level declaration, not something
        # kwdagger understands, so keep it out of the scheduled spec.
        self.spec = {
            k: v for k, v in pipeline_def.items() if k != 'result_node'
        }
        self.result_node = pipeline_def.get('result_node')
        self.root_dpath = root_dpath
        self.results = []
        self.symbols = []

    def dispatch(
        self, backend: str | None = None, skip_existing: bool = True,
        **kwargs: Any
    ) -> None:
        backend = resolve_queue_backend(backend)
        kwd_config = ScheduleEvaluationConfig(
            params=self.spec,  # includes pipeline and additional params
            root_dpath=self.root_dpath,
            queue_name=_queue_name_for(self.root_dpath),
            **({'tmux_workers': _tmux_workers()}
               if _tmux_workers() is not None else {}),
            backend=backend,
            skip_existing=skip_existing,
            run=True,
            **kwargs,
        )

        self.dag, queue = build_schedule(kwd_config)

    def collect_result_cells(self) -> List[Dict[str, Any]]:
        """
        Read the result node's output for each of its configured instances.

        One instance is one cell of the card: a gather with ``group_by``
        produces one result instance per group. Each is asked where its own
        artifact is rather than globbing the run tree, and its results are
        qualified as ``metrics.<node>.<name>`` -- kwdagger's convention, kept
        so two nodes cannot collide in a claim's namespace.

        Returns:
            List[Dict[str, Any]]: per instance, its distinguishing params
                (``coords``), its ``results``, and its ``artifact`` path.
        """
        if not self.result_node:
            raise ValueError('card must declare kwdagger.result_node')

        if not getattr(self, 'dag', None):
            self.dispatch()

        # build_schedule returns configured instances keyed by process id;
        # node.name is the template name the card refers to.
        instances = [
            node
            for node in self.dag.nodes.values()
            if node.name == self.result_node
        ]
        if not instances:
            available = sorted({node.name for node in self.dag.nodes.values()})
            raise ValueError(
                f'result_node {self.result_node!r} is not a node in the '
                f'pipeline; available: {available}'
            )

        coord_keys = _varying_keys([dict(node.config) for node in instances])

        cells = []
        for node in instances:
            fpath = (
                node.final_node_dpath / node.out_paths[node.primary_out_key]
            )
            if not fpath.exists():
                raise RuntimeError(
                    f'result node {self.result_node!r} produced no '
                    f'{fpath}; the pipeline likely failed upstream'
                )
            payload = json.loads(fpath.read_text())
            cells.append({
                'coords': {key: node.config[key] for key in coord_keys},
                'results': {
                    f'metrics.{self.result_node}.{name}': value
                    for name, value in payload.items()
                    if not name.startswith('_')
                },
                'artifact': str(fpath),
            })
        return cells

    def collect_results(self) -> Tuple[List[str], List[Any]]:
        if not self.results:
            self.dispatch()

        # Glob all Claim node json files recursively
        paths = self.root_dpath.glob('**/verdict.json')

        # Assumes {result: {status: value}} output format
        for claim_json in paths:
            claim_result = json.load(open(claim_json, 'r'))
            if 'result' in claim_result and 'status' in claim_result['result']:
                self.results.append(claim_result['result']['status'])
            if 'result' in claim_result and 'symbols' in claim_result['result']:
                self.symbols.append(claim_result['result']['symbols'])

        return self.results, self.symbols


class EvaluationTask:
    """
    Singular submission from an Evaluation Card
    """

    def __init__(
        self,
        claim: 'Claim',
        symbols: 'Symbols',
        results: Dict[str, Any] | None = None,
        evidence_spec: List[Dict[str, Any]] | None = None,
    ) -> None:
        self.claim = claim
        self.symbols = symbols
        self.results = Results(results or {})
        self.evidence_spec = evidence_spec or []
        self.evidence: List[Dict[str, Any]] = []
        self.output_msg = ''
        self.log: Dict[str, Any] = {}

    def execute(self) -> Tuple[str, str]:
        self.symbols.resolve()
        # x -> y -> z1 -> a1 -> res1
        #           ...
        #           zn -> an -> resn
        # make sure x,y are done once / before sweep
        context = self.symbols()
        for name, value in self.results.bind().items():
            if name in context:
                raise ValueError(
                    f'symbol {name!r} collides with a pipeline result of the '
                    f'same name; rename the symbol'
                )
            context[name] = value

        self.evidence = _collect_evidence(self.evidence_spec, context)

        if self.evidence and not self.claim.claim.strip():
            # Evidence alone decides the cell when there is no claim to run.
            self.result, self.output_msg = _verdict_from_evidence(self.evidence)
        else:
            self.result, self.output_msg = self.claim.evaluate(context)

        self.record_run()
        return self.result, self.output_msg

    def record_run(self) -> None:
        completion_time = datetime.now().isoformat()
        self.log = {
            'status': self.result,
            'output': self.output_msg,
            'symbols': self.symbols.simple_view(),
            'timestamp': completion_time,
        }
        if self.evidence:
            self.log['evidence'] = self.evidence
        if self.results.accessed:
            self.log['consumed'] = sorted(self.results.accessed)

    @property
    def _execution_hash(self) -> str:
        return ub.hash_data(self.symbols.simple_view())[:12]


def _resolve_pipeline_path(
    kwdagger_spec: Dict[str, Any], card_dpath: ub.Path
) -> Dict[str, Any]:
    """
    Make a relative pipeline file path mean the same thing from any directory.

    A card may name a pipeline file rather than inline the DAG or name a
    Python callable. Such a path is written relative to the card, matching how
    the theory block's formalization paths already work, so evaluating a card
    does not depend on where the shell happened to be.

    Args:
        kwdagger_spec (Dict[str, Any]): the card's ``kwdagger`` block.
        card_dpath (ub.Path): the directory holding the card.

    Returns:
        Dict[str, Any]: the spec, with any relative pipeline path made absolute.

    Example:
        >>> import ubelt as ub
        >>> from magnet.evaluation import _resolve_pipeline_path
        >>> spec = {'pipeline': 'module.func()'}
        >>> _resolve_pipeline_path(spec, ub.Path('/cards'))['pipeline']
        'module.func()'
        >>> spec = {'pipeline': {'nodes': {}}}
        >>> _resolve_pipeline_path(spec, ub.Path('/cards'))['pipeline']
        {'nodes': {}}
        >>> spec = {'pipeline': 'dag.yaml'}
        >>> _resolve_pipeline_path(spec, ub.Path('/cards'))['pipeline']
        '/cards/dag.yaml'
        >>> spec = {'pipeline': '/abs/dag.yaml'}
        >>> _resolve_pipeline_path(spec, ub.Path('/cards'))['pipeline']
        '/abs/dag.yaml'
    """
    pipeline = kwdagger_spec.get('pipeline')
    if not isinstance(pipeline, str):
        return kwdagger_spec
    if '::' in pipeline:
        return kwdagger_spec
    if pipeline.rsplit('.', 1)[-1].lower() not in {'yaml', 'yml', 'json'}:
        return kwdagger_spec

    path = ub.Path(pipeline)
    if not path.is_absolute():
        path = card_dpath / path

    resolved = dict(kwdagger_spec)
    resolved['pipeline'] = os.fspath(path)
    return resolved


def _plain_data(data: Any) -> Any:
    """
    Rebuild YAML data out of plain dicts, lists and scalars.

    The loader returns round-trip types that carry formatting. Those reach
    ``original_card`` through an override and then fail in ``yaml.safe_dump``
    when the run directory's copy of the card is written, with
    ``RepresenterError: cannot represent an object``. Any list-valued override
    hit this, so ``--override 'seed: [1, 2]'`` could not run at all.

    Example:
        >>> import kwutil
        >>> from magnet.evaluation import _plain_data
        >>> data = _plain_data(kwutil.Yaml.coerce('seed: [1, 2]'))
        >>> type(data).__name__, type(data['seed']).__name__
        ('dict', 'list')
        >>> import yaml
        >>> yaml.safe_dump(data)
        'seed:\\n- 1\\n- 2\\n'
        >>> quoted = kwutil.Yaml.coerce("cfg: ['a:b=c']")
        >>> yaml.safe_dump(_plain_data(quoted))
        'cfg:\\n- a:b=c\\n'
    """
    if isinstance(data, dict):
        return {_plain_data(key): _plain_data(value)
                for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [_plain_data(value) for value in data]
    # Scalars need this too: a quoted string loads as a str subclass that
    # remembers its quoting style, and safe_dump refuses that as readily as it
    # refuses the sequence type.
    if isinstance(data, str):
        return str(data)
    if isinstance(data, bool):
        return bool(data)
    if isinstance(data, int):
        return int(data)
    if isinstance(data, float):
        return float(data)
    return data


def _parse_symbol_metadata(symbols_spec: Dict[str, Any]) -> Dict[str, Any]:
    metadata = {}
    for name, details in symbols_spec.items():
        symbol_metadata = details.get('metadata')
        if symbol_metadata is not None:
            metadata[name] = symbol_metadata
    return metadata


_RELATIONS = {
    'gt': lambda value, target: value > target,
    'ge': lambda value, target: value >= target,
    'lt': lambda value, target: value < target,
    'le': lambda value, target: value <= target,
    'eq': lambda value, target: value == target,
}


def _relation_holds(relation: Dict[str, Any], value: Any) -> bool:
    """
    Apply a declared relation to a measured value.

    Example:
        >>> from magnet.evaluation import _relation_holds
        >>> _relation_holds({'gt': 0.07}, 0.19)
        True
        >>> _relation_holds({'within': {'of': 50.0, 'tol': 5.0}}, 52.0)
        True
        >>> _relation_holds({'gt': 0, 'lt': 1}, 0.5)
        Traceback (most recent call last):
            ...
        ValueError: evidence relation must name exactly one of ...
    """
    if len(relation) != 1:
        raise ValueError(
            f'evidence relation must name exactly one of '
            f'{sorted(_RELATIONS) + ["within"]}; got {sorted(relation)}'
        )
    (name, target), = relation.items()
    if name == 'within':
        return abs(value - target['of']) <= target['tol']
    if name not in _RELATIONS:
        raise ValueError(
            f'unknown evidence relation {name!r}; '
            f'known: {sorted(_RELATIONS) + ["within"]}'
        )
    return _RELATIONS[name](value, target)


def _lookup_qualified(context: Dict[str, Any], name: str) -> Any:
    """Resolve a plain symbol name or a dotted qualified name."""
    head, _, rest = name.partition('.')
    if head not in context:
        raise KeyError(name)
    value = context[head]
    for part in rest.split('.') if rest else []:
        value = value[part]
    return value


def _collect_evidence(
    spec: List[Dict[str, Any]], context: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Measure each declared item and record whether its relation held.

    Anything the card says about an item that is not the measurement itself --
    ``scope``, ``supports``, ``relaxes`` -- is carried through unread, so the
    record keeps what the run was evidence *for*.
    """
    records = []
    for item in spec:
        name = item['measures']
        record: Dict[str, Any] = {'measures': name}
        try:
            value = _lookup_qualified(context, name)
        except (KeyError, AttributeError) as ex:
            record.update(value=None, held=None, error=str(ex))
        else:
            relation = item.get('relation')
            record['value'] = value
            record['relation'] = relation
            record['held'] = (
                None if relation is None else _relation_holds(relation, value)
            )
        for key in ('scope', 'supports', 'relaxes'):
            if key in item:
                record[key] = item[key]
        records.append(record)
    return records


def _verdict_from_evidence(
    evidence: List[Dict[str, Any]]
) -> Tuple[str, str]:
    """
    Reduce one cell's evidence to a status.

    Example:
        >>> from magnet.evaluation import _verdict_from_evidence
        >>> _verdict_from_evidence([{'measures': 'x', 'held': True}])[0]
        'VERIFIED'
        >>> _verdict_from_evidence([{'measures': 'x', 'held': False}])[0]
        'FALSIFIED'
        >>> _verdict_from_evidence([{'measures': 'x', 'held': None}])[0]
        'INCONCLUSIVE'
    """
    held = [record.get('held') for record in evidence]
    if any(value is None for value in held):
        unmet = [
            record['measures']
            for record in evidence
            if record.get('held') is None
        ]
        return 'INCONCLUSIVE', f'no measurement for: {", ".join(unmet)}'
    if all(held):
        return 'VERIFIED', 'all evidence holds'
    failed = [
        f'{record["measures"]}={record.get("value")!r}'
        for record in evidence
        if not record.get('held')
    ]
    return 'FALSIFIED', f'evidence does not hold: {", ".join(failed)}'


def _varying_keys(configs: List[Dict[str, Any]]) -> List[str]:
    """Config keys whose values differ across configured node instances."""
    if len(configs) < 2:
        return []
    shared = set.intersection(*[set(config) for config in configs])
    return sorted(
        key for key in shared
        if len({repr(config[key]) for config in configs}) > 1
    )


def _reduce_results(results: List[str], reduce_spec: Dict[str, Any]) -> str:
    """
    Reduce per-sweep-point claim outcomes to a single card-level status.

    reduce_spec: dict with key `type`:
      - {'type': 'all'}               any FALSIFIED -> FALSIFIED; any INCONCLUSIVE -> INCONCLUSIVE; else VERIFIED
      - {'type': 'any'}               any VERIFIED -> VERIFIED; any INCONCLUSIVE (and no VERIFIED) -> INCONCLUSIVE; else FALSIFIED
      - {'type': 'fraction', 'parameters': {'threshold': 0.8}}
                                      VERIFIED_count / total >= threshold -> VERIFIED; else FALSIFIED.
                                      INCONCLUSIVE points count in the denominator but not the numerator.
    """
    total = len(results)
    if total == 0:
        return 'INCONCLUSIVE'

    verified_count = results.count('VERIFIED')
    falsified_count = results.count('FALSIFIED')
    inconclusive_count = results.count('INCONCLUSIVE')

    rtype = reduce_spec.get('type', 'all')
    if rtype == 'all':
        if falsified_count:
            return 'FALSIFIED'
        if inconclusive_count:
            return 'INCONCLUSIVE'
        return 'VERIFIED'
    if rtype == 'any':
        if verified_count:
            return 'VERIFIED'
        if inconclusive_count:
            return 'INCONCLUSIVE'
        return 'FALSIFIED'
    if rtype == 'fraction':
        parameters = reduce_spec.get('parameters', {})
        threshold = parameters.get('threshold')
        if threshold is None:
            raise ValueError('reduce type=fraction requires `threshold`')
        frac = verified_count / total
        final_result = 'VERIFIED' if frac >= threshold else 'FALSIFIED'
        logger.info(
            f'[reduce=fraction] {final_result} {verified_count}/{total} ({frac:.3f}) vs threshold {threshold}'
        )
        return final_result

    raise ValueError(f'Unknown reduce type: {rtype!r}')


class Claim:
    """
    Represents a verifiable assertion for a set of resolved symbols

    ***
    Currently assumes
    1. claim is valid and safe python code
    2. all symbols can be resolved from card
    3. No additional dependencies are needed
    4. Any conclusions drawn are as reliable as claim itself (i.e. verification is strictly: 'does code execute without error')
    ***

    Example:
        >>> from magnet.evaluation import Claim
        >>> self = Claim({'python': "assert x + 2 == 4"})
        >>> print(self)
        assert x + 2 == 4
        >>> self.evaluate({'x': 2})
        >>> print(self.status)
        VERIFIED
    """

    def __init__(self, raw: Dict[str, str]) -> None:
        self.claim = raw.get('python', '')
        self.status = 'UNVERIFIED'

    def evaluate(self, symbols: Dict[str, Any] = {}) -> Tuple[str, str]:
        """
        Execute the claim subject to symbols definitions

        if True:
            VERIFIED
        elif AssertionError:
            FALSIFIED
        else:
            INCONCLUSIVE
        """
        out_msg = ''
        try:
            exec(self.claim, symbols)
            self.status = 'VERIFIED'
            out_msg = 'Assertion holds'
            logger.info(out_msg)
        except AssertionError as e:
            self.status = 'FALSIFIED'
            out_msg = f'Assertion does not hold: {e}'
            logger.warning(out_msg)
        except NameError as e:
            self.status = 'INCONCLUSIVE'
            # This doesn't guarantee the missing variable is a symbol
            out_msg = f'SymbolNotResolved: {e}'
            logger.error(out_msg)
        except Exception as e:
            self.status = 'INCONCLUSIVE'
            out_msg = f'ERROR evaluating claim: {e}'
            logger.exception('Unexpected exception while evaluating claim')

        return self.status, out_msg

    def __repr__(self) -> str:
        return self.claim


class Results:
    """
    Pipeline results addressed by their qualified names.

    kwdagger's convention is a flat dict of ``metrics.<node>.<name>`` keys.
    Those are not Python identifiers, so a claim cannot name them directly;
    this exposes each dotted level as an attribute instead. Reads are recorded,
    which is what lets the evidence record say which values a claim consumed.

    Example:
        >>> from magnet.evaluation import Results
        >>> results = Results({'metrics.summarize.pooled_mean': 50.4,
        ...                    'metrics.summarize.num_samples': 5})
        >>> bound = results.bind()
        >>> bound['metrics'].summarize.pooled_mean
        50.4
        >>> sorted(results.accessed)
        ['metrics.summarize.pooled_mean']
        >>> bound['metrics'].summarize.missing
        Traceback (most recent call last):
            ...
        AttributeError: no 'missing' under 'metrics.summarize'; available: ...
    """

    def __init__(
        self,
        flat: Dict[str, Any],
        prefix: str = '',
        accessed: set | None = None,
    ) -> None:
        self._flat = dict(flat)
        self._prefix = prefix
        self._accessed = set() if accessed is None else accessed

    @property
    def accessed(self) -> set:
        return self._accessed

    def bind(self) -> Dict[str, Any]:
        """Top-level names to inject into a claim's namespace."""
        bound = {}
        for key in self._flat:
            root = key.split('.', 1)[0]
            if root in self._flat:
                bound[root] = self._flat[root]
            else:
                bound[root] = Results(self._flat, f'{root}.', self._accessed)
        return bound

    def as_dict(self) -> Dict[str, Any]:
        """
        The leaf values at this level, unqualified.

        For a claim that would rather work in bare names than qualified ones::

            globals().update(metrics.summarize.as_dict())

        Example:
            >>> from magnet.evaluation import Results
            >>> results = Results({'metrics.summarize.mae': 0.1})
            >>> results.bind()['metrics'].summarize.as_dict()
            {'mae': 0.1}
        """
        depth = len(self._prefix)
        values = {}
        for name, value in self._flat.items():
            if name.startswith(self._prefix) and '.' not in name[depth:]:
                self._accessed.add(name)
                values[name[depth:]] = value
        return values

    def __getattr__(self, name: str) -> Any:
        # Underscored names are never results, and answering them here would
        # recurse during unpickling, before _flat exists.
        if name.startswith('_'):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, key: str) -> Any:
        qualified = f'{self._prefix}{key}'
        if qualified in self._flat:
            self._accessed.add(qualified)
            return self._flat[qualified]

        branch = f'{qualified}.'
        if any(name.startswith(branch) for name in self._flat):
            return Results(self._flat, branch, self._accessed)

        raise AttributeError(
            f'no {key!r} under {self._prefix.rstrip(".")!r}; '
            f'available: {", ".join(self._children())}'
        )

    def _children(self) -> List[str]:
        depth = len(self._prefix)
        return sorted({
            name[depth:].split('.', 1)[0]
            for name in self._flat
            if name.startswith(self._prefix)
        })

    def __repr__(self) -> str:
        return f'<Results {self._prefix or "."}: {", ".join(self._children())}>'


class Symbol:
    """
    Single resolvable unit of a claim

    Example:
        >>> from magnet.evaluation import Symbol
        >>> x = Symbol('x', {'type': "List[int]", 'python': "x = [10]"})
        >>> x.eval()
        [10]

    Example:
        >>> # `depends` is accepted as a spelling of `depends_on`.
        >>> from magnet.evaluation import Symbol
        >>> spec = {'type': 'int', 'python': 'y = x + 1', 'depends': ['x']}
        >>> Symbol('y', spec).dependencies
        ['x']
    """

    #: Keys a symbol spec may declare.
    #:
    #: ``depends`` is an accepted spelling of ``depends_on``.  Cards are
    #: hand-written YAML and both spellings are in use; the wrong one used to
    #: be dropped by ``dict.get``, which left the symbol with no declared
    #: dependencies at all.  That is silent and order-dependent -- see
    #: :meth:`Symbols._construct_dependency_order` -- so accept both rather
    #: than let a card claim a dependency the resolver never sees.
    KNOWN_SPEC_KEYS = frozenset({
        'type', 'value', 'sweep', 'python', 'depends_on', 'depends',
        'metadata',
    })

    def __init__(self, name: str, spec: Dict[str, Any]) -> None:
        self.name = name
        self.value = spec.get('value')
        self.sweep = spec.get('sweep')
        self.type = spec.get('type', 'List[int]')
        self.definition = spec.get('python', '')
        self.dependencies = self._resolve_dependencies(name, spec)
        self.metadata = spec.get('metadata')

        unknown = set(spec) - self.KNOWN_SPEC_KEYS
        if unknown:
            # Not fatal: an unrecognized key may be forward-compatible or
            # simply decorative.  But it is never *acted on*, so say so --
            # a misspelling here is otherwise indistinguishable from a key
            # that was honored.
            logger.warning(
                f'symbol {name!r}: ignoring unrecognized key(s) '
                f'{sorted(unknown)}; recognized keys are '
                f'{sorted(self.KNOWN_SPEC_KEYS)}'
            )

    @staticmethod
    def _resolve_dependencies(
        name: str, spec: Dict[str, Any]
    ) -> List[str]:
        """
        Read declared dependencies under either accepted spelling.

        Args:
            name (str): the symbol's name, for error messages.
            spec (Dict[str, Any]): the symbol spec as written in the card.

        Returns:
            List[str]: the declared dependencies, possibly empty.

        Raises:
            ValueError: if both spellings are present and disagree.

        Example:
            >>> from magnet.evaluation import Symbol
            >>> Symbol._resolve_dependencies('y', {'depends_on': ['x']})
            ['x']
            >>> Symbol._resolve_dependencies('y', {'depends': ['x']})
            ['x']
            >>> Symbol._resolve_dependencies('y', {})
            []
            >>> # Both spellings agreeing is redundant but harmless.
            >>> Symbol._resolve_dependencies(
            ...     'y', {'depends': ['x'], 'depends_on': ['x']})
            ['x']
            >>> # Both spellings disagreeing has no defensible reading.
            >>> Symbol._resolve_dependencies(
            ...     'y', {'depends': ['x'], 'depends_on': ['z']})
            Traceback (most recent call last):
                ...
            ValueError: symbol 'y' declares both `depends_on` (['z']) and ...
        """
        canonical = spec.get('depends_on')
        alias = spec.get('depends')

        if canonical is not None and alias is not None:
            if list(canonical) != list(alias):
                raise ValueError(
                    f'symbol {name!r} declares both `depends_on` '
                    f'({canonical!r}) and `depends` ({alias!r}), and they '
                    f'disagree. They are the same key; give one of them.'
                )
            return list(canonical)

        if canonical is not None:
            return list(canonical)
        if alias is not None:
            return list(alias)
        return []

    def eval(self, context: Dict[str, Any] = {}) -> Any:
        """
        Resolve symbol definition

        FIXME: type verification is currently limited and hacky
        """
        if self.value is None:
            logger.debug(f'Resolving: {self.name}')
            exec(self.definition, context)
            if self._check_type(self.type, context[self.name]):
                self.value = context[self.name]
            else:
                raise TypeError(
                    f'{self.name}: {context[self.name]} is not {self.type}'
                )

        return self.value

    def _check_type(self, type_str: str, value: Any) -> bool:
        """
        Validate value is of type str_type
        """
        # TODO: static 'vocabulary' of allowable types / support more than List[Any], Dict[str, Any]
        str_to_type = {'List': List, 'Dict': Dict, 'Tuple': Tuple, 'Any': Any}
        type = eval(type_str, str_to_type)
        return self._check_collections(type, value)

    def _check_collections(self, target_type: Any, value: Any) -> bool:
        """
        Recursively evaluate if value is target_type
        """
        collection_type = get_origin(target_type)
        members = get_args(target_type)

        match collection_type:
            case builtins.list:
                if isinstance(value, list):
                    return all(
                        self._check_collections(members[0], entry)
                        for entry in value
                    )
                return False
            case builtins.dict:
                if isinstance(value, dict):
                    return all(
                        self._check_collections(members[0], key_entry)
                        and self._check_collections(members[1], value_entry)
                        for key_entry, value_entry in value.items()
                    )
                return False
            case builtins.tuple:
                if isinstance(value, tuple) and len(value) == len(members):
                    return all(
                        self._check_collections(type, val)
                        for type, val in zip(members, value)
                    )
                return False
            case None:
                # Any or primative
                return target_type is Any or isinstance(value, target_type)
            case _:
                return False


class Symbols:
    """
    Collection of Symbol configurations used as context for claim

    Example:
        >>> from magnet.evaluation import Symbols
        >>> symbols = Symbols({'x': {'type': "List[int]", 'python': "x = [10]"}})
        >>> symbols()
        {'x': None}
        >>> symbols.resolve()
        >>> symbols()
        {'x': [10]}

    Example:
        >>> # A declared dependency orders resolution, so a card is free to
        >>> # write its symbols in any order.  Here the dependent symbol is
        >>> # declared FIRST, which without the edge would exec `y = x + 1`
        >>> # against a context that has no `x` yet.
        >>> from magnet.evaluation import Symbols
        >>> for spelling in ['depends_on', 'depends']:
        ...     symbols = Symbols({
        ...         'y': {'type': 'int', 'python': 'y = x + 1',
        ...               spelling: ['x']},
        ...         'x': {'type': 'int', 'value': 1},
        ...     })
        ...     symbols.resolve()
        ...     print(f'{spelling}: {symbols()}')
        depends_on: {'y': 2, 'x': 1}
        depends: {'y': 2, 'x': 1}
    """

    def __init__(self, symbol_specs: Dict[str, Any]) -> None:
        self.symbols = {
            symbol: Symbol(symbol, definition)
            for symbol, definition in symbol_specs.items()
        }

    @classmethod
    def decompose_symbol_defs(
        cls, symbol_definitions: Dict[str, Any]
    ) -> List[Self]:
        """
        Flatten sweep values into a list of resolvable Symbols
        """
        configurations = []
        aggregate_configuration = cls(symbol_definitions)

        sweep_symbols = aggregate_configuration._find_sweep_symbols()
        if sweep_symbols:
            sweep_values = [sweep.sweep for sweep in sweep_symbols]
            combinations = product(*sweep_values)

            for combo in combinations:
                sweep_fill = dict(
                    zip([symbol.name for symbol in sweep_symbols], combo)
                )
                flattened_symbols = cls(symbol_definitions)
                for k, v in sweep_fill.items():
                    flattened_symbols.symbols[k].value = v
                configurations.append(flattened_symbols)
        else:
            configurations.append(aggregate_configuration)

        return configurations

    def resolve(self) -> None:
        """
        Trace dependency graph to resolve each symbol definition

        Values stored in Symbol instances
        """
        symbol_definitions = {}

        for symbol in self._construct_dependency_order():
            symbol_value = self.symbols[symbol]
            symbol_definitions_ = symbol_definitions.copy()
            try:
                symbol_definitions[symbol] = symbol_value.eval(
                    symbol_definitions_
                )
            except Exception as ex:
                error_message = ub.codeblock(
                    f"""
                    Error in resolve. ex={ex}

                    {symbol=!r}
                    {symbol_value=!r}
                    {symbol_definitions_=!r}
                    """
                )
                logger.error(error_message)
                raise

    def _find_sweep_symbols(self) -> List['Symbol']:
        return [symbol for symbol in self.symbols.values() if symbol.sweep]

    def _construct_dependency_order(self) -> List[str]:
        """
        Construct dependency order
        """
        dependency_graph = {
            name: symbol.dependencies for name, symbol in self.symbols.items()
        }
        sorter = TopologicalSorter(dependency_graph)
        return list(sorter.static_order())

    def simple_view(self) -> Dict[str, Any]:
        # TODO: replace with free variables and data attestation
        ALLOWABLE_TYPES = [int, float, str, dict]
        return {
            k: v
            for k, v in self().items()
            if type(v) in ALLOWABLE_TYPES
            or (type(v) == list and type(v[0]) == int)
        }

    def __call__(self) -> Dict[str, Any]:
        return {symbol: self.symbols[symbol].value for symbol in self.symbols}


MetricValue = float
MetricReducer = Callable[[List[float]], MetricValue]


class Metric:
    def __init__(
        self,
        name: str,
        objective: MetricObjective,
        reducer: MetricReducer,
    ) -> None:
        self.name = name
        self.objective = objective
        self.reducer = reducer

    def aggregate_calculate(self, runs: List[float]) -> MetricValue:
        logger.info(f'Computing {self.name} Metric across all runs\n')
        return self.reducer(runs)

    @classmethod
    def build_metrics_from_symbol_metadata(
        cls, symbol_metadata: Dict[str, Any]
    ) -> List[Self]:
        metrics = []
        for name, metadata in symbol_metadata.items():
            metric_metadata = metadata.get('define_metric')
            if metric_metadata is not None:
                agg_strategy = metric_metadata.get(
                    'aggregation_strategy', DEFAULT_METRIC_AGGREGATION_STRATEGY
                )
                strategy_name = agg_strategy.get('type')
                parameters = agg_strategy.get('parameters') or {}
                objective = MetricObjective(
                    metric_metadata.get('objective', MetricObjective.MINIMIZE)
                )

                match strategy_name:
                    case 'max':
                        reducer = max
                    case 'min':
                        reducer = min
                    case 'mean':
                        reducer = fmean
                    case 'custom':
                        # Python function
                        raise NotImplementedError
                    case _:
                        logger.warning(
                            'Unrecognized Metric Aggregation Strategy; Please select one of {max, min, mean, custom}'
                        )
                        reducer = None

                if reducer is not None:
                    metrics.append(cls(name, objective, reducer))
        return metrics


def _calculate_metrics(
    metric_definitions: List[Metric],
    evaluations: List['EvaluationTask'] | List[Dict[str, Any]],
    symbol_metadata: Dict[str, Any],
) -> Dict[str, MetricValue]:
    calculated_metrics = {}
    for metric in metric_definitions:
        runs = []
        for evaluation in evaluations:
            if isinstance(evaluation, dict):
                symbols = evaluation
            else:
                symbols = evaluation.symbols()
            symbol_value = symbols.get(metric.name)
            if symbol_value is None:
                logger.error(
                    f'Metric {metric.name} cannot be mapped to a Symbol value'
                )
                break
            runs.append(symbol_value)
        else:
            display_name = symbol_metadata[metric.name].get(
                'display_name', metric.name
            )
            calculated_metrics[display_name] = metric.aggregate_calculate(runs)
    return calculated_metrics


def main(argv: Optional[List[str]] = None, **kwargs: Any) -> None:
    args = EvaluationConfig.cli(
        argv=argv,
        data=kwargs,
        strict=True,
        verbose='auto',
        special_options=False,
    )

    # Item access, not `args.validate`: the option shares its name with
    # `kwconf.Config.validate`, and the method wins attribute lookup. Reading it
    # as an attribute yields a bound method, which silently compares unequal to
    # every mode and turns validation off. See tests/test_kwconf_configs.py.
    validate = args['validate']

    if validate == 'only':
        try:
            with open(args.path, 'r') as f:
                cfg = yaml.safe_load(f)
            EvaluationCardSchema.model_validate(cfg)
            print('Card validation succeeded.')
        except ValidationError as e:
            print('Card validation failed.')
            print(e)
            sys.exit(1)
        return

    card = EvaluationCard(args.path, args.output_path, validate=validate)
    if args.override is not None:
        card.replace(args.override)

    if args.queue_backend:
        # One source of truth: the resolver reads this, and so does any nested
        # dispatch. Threading a parameter through evaluate() would miss the
        # dispatch calls that run from collect_result_cells().
        os.environ['MAGNET_QUEUE_BACKEND'] = args.queue_backend

    card.evaluate(
        jobs=args.jobs,
        parallel_backend=args.parallel_backend,
        verbose=bool(args.verbose),
    )
    card.summarize()


__cli__ = EvaluationConfig
__cli__.main = main

if __name__ == '__main__':
    main(sys.argv[1:])
