Measurement-first evaluation cards
=================================

A recipe describes computation and the measurements it publishes. Running it
produces an evaluation card: bound symbol values, their definitions, independent
result sets, evidence scope and source artifact identities. A Boolean claim is
not required. Measurements can support several later questions without changing
the computations that produced them.

The execution and analysis boundaries are separate::

    recipe -> kwdagger nodes -> cached artifacts
      |                             |
      | published symbols           | aggregate loaders
      +-----------------------------+
                                    |
                           evaluation.json
                                    |
                     symbols / tables / reports / export

``evaluation.json`` records a measurement snapshot. It is not a pipeline
completion marker: partial or historical results are valid inputs. Existing
``verdict.json`` and assertion records remain compatibility output for older
recipes. Measurement-first reports do not use those assessments as their
headline; ``results show --legacy_claims=1`` includes them explicitly.

Declare measurements
--------------------

Use ``kwdagger.readout_nodes`` to select independent result sets. A node's
normal kwdagger loader supplies qualified columns. A metadata-only symbol
names the exact column it publishes:

.. code-block:: yaml

   symbols:
     metrics.analysis.quality:
       metadata:
         display_name: Correctness prediction
         display: true
         description: Per-model held-out predictions on retained answers.
         columns:
           model_id: {kind: model, display_name: Answerer}
           auroc: {display_name: AUROC, unit: '1'}
           n: {display_name: Evaluated answers, unit: answers}
     metrics.analysis.answers_file:
       metadata:
         display: false
         format: jsonl
         columns:
           question_id: {display_name: Answer ID}
           scores.joint: {display_name: Predicted correctness, unit: '1'}
   kwdagger:
     readout_nodes: [analysis]
     pipeline: my_package.pipelines.experiment()

``quality`` is a list of records. ``answers_file`` is a path to a JSONL table,
so detailed observations need not be duplicated inside the evaluation card.
JSON arrays and CSV files are also supported. JSON/JSONL retain their types;
CSV fields remain text. Normalize quantities in a producer node when necessary.
Nested record fields become dotted table columns, such as ``scores.joint``.

Labels, units and descriptions explain measurements; MAGNET does not infer
units, join populations, certify comparability or convert a question into
work. Different row structures remain separate. ``display: false`` keeps a
symbol queryable while omitting it from the default human report. Null values,
empty tables and absent symbols are preserved distinctly.

Run and read
------------

The bundled example uses synthetic answer scores and CPU analysis only:

.. code-block:: console

   magnet evaluate_new magnet/examples/measurements/card.yaml \
       --backend=tmux --output_path=evaluation_runs \
       --record_path_file=evaluation_runs/record.json
   magnet results show evaluation_runs/RECORD
   magnet results symbols evaluation_runs/RECORD

The record descriptor identifies the exact evaluation directory. The symbol
catalog includes definitions, units, table columns and counts of observed,
null and missing bindings. Execution state is recorded separately in
``execution.json``; it is not a scientific finding.

Read values through the library without loading pipeline code:

.. code-block:: python

   from magnet.results import load_evaluation_card

   card = load_evaluation_card('evaluation_runs/RECORD')
   definitions = card.symbol_catalog()
   quality = card.table(node='measure', expand='metrics.measure.quality')
   answers = card.table(node='measure', expand='metrics.measure.answers_file',
                        columns=['question_id', 'correct', 'scores.strong_signal'])

The returned pandas tables retain record, node, process, artifact and evidence
scope columns unless explicitly projected away. Expansion selects one table;
it carries scalar context without repeating other tables. It never performs
an implicit join or Cartesian product.

Structured analysis
-------------------

.. code-block:: console

   magnet results table evaluation_runs/RECORD --node=measure \
       --expand=metrics.measure.quality --where='{method: strong_signal}' \
       --group_by=method --aggregate='{auroc: mean, brier_score: mean}' \
       --output=analysis.json

``where`` uses exact equality; a list selects allowed values. ``columns``
projects explicit fields. Reductions are explicit ``mean``, ``min``, ``max``,
``sum`` or ``count``. Each reduction reports observed and missing counts. JSON
output includes the complete selection specification and source record; CSV
output with ``--output`` writes a companion ``.selection.json`` file.

These are descriptive operations, not a statistical protocol. They do not
choose weighting, uncertainty estimates, train/test splits or deployment
policies. Put those calculations in ordinary kwdagger nodes, publish their
outputs as symbols and record their configuration. Such a node can consume
``load_evaluation_card(...).table(...)`` as its input interface.

``results combine`` preserves measurement rows with named variants and their
source records. A subsequent table selection can use ``_variant`` to compare
explicitly chosen conditions. It does not silently deduplicate or pool the
measurement rows into one observation.

Reuse and portability
---------------------

Symbol declarations, reports and table selections do not enter computational
identity. Existing node IDs and cached results stay under kwdagger's existing
parameter/path convention. New scientific computation requires an explicitly
configured node; selecting saved values requests no work.

``--readout_only=1`` writes a new evaluation snapshot from available artifacts
without running missing nodes. ``--dry_run`` instead reports requested work
without reading evidence. Available rows and missing measurements remain
visible independently of failed or pending requests.

.. code-block:: console

   magnet results export evaluation_runs/RECORD portable/RECORD --supporting=terminal
   magnet results verify portable/RECORD

A core export includes the evaluation snapshot and definitions. Selecting
``terminal`` also includes the referenced primary artifacts and declared
file-backed tables. Table reading then works after the original cache and
pipeline code are removed. A missing optional file is reported in the export
manifest; a missing declared evaluation snapshot makes verification fail.
