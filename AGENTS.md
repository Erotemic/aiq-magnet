# Agent instructions

## Command construction must work without the execution environment

MAGNET's kwdagger integration must produce commands on a machine that cannot
run them. No GPU, Docker, evaluation engine, task plugins, model files,
credentials, network service or target worker interpreter is required to
read configuration, compute a node ID, compile a graph or render a command.

Properties such as final_algo_config must read declared configuration. Do not
spawn subprocesses, resolve native tasks, query serving catalogs or infer
runtime identities from those properties. A mandatory preparation pass before
compilation violates the same contract. Putting that work in a batch or warm
worker only changes the cost, not the incorrect environment requirement.

Generated commands resolve native facts and obtain evaluations when they run
in their execution environment. Preserve the evaluator's native result store
and its reusable measurements independently of kwdagger node IDs. Keep legacy
cards and previously rendered commands compatible while correcting the path.
Read kwdagger's planning_execution_boundary design note before changing this
integration. A safeguard that prevents command construction on another
machine is a layering bug, even when motivated by provenance or cache reuse.

## Working state and attribution

Keep the active source checkout stable while its generated jobs are running.
Prepare runtime changes in a separate branch/worktree and prove them there.
Commit completed work with a Co-authored-by trailer naming the actual model
(and serving variant when known). Keep downstream evaluation intent and
team-specific information out of this public repository.
