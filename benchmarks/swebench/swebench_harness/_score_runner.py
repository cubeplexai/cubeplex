"""In-process invoker for SWE-bench's run_evaluation.main.

Why not just `python -m swebench.harness.run_evaluation …`? That CLI path
intermittently loads an EMPTY dataset at run_evaluation.py:521 and aborts
with "Some instance IDs not found in dataset!" — even when the same
`load_swebench_dataset(name, split, ids)` call returns the full set when
invoked directly. Calling `main(**kwargs)` in-process with an explicit
instance_ids list is reliable. This thin wrapper is subprocessed by
score.py so the scorer still runs in its own isolated process.

Usage: _score_runner.py <json-kwargs-file>
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    kwargs = json.loads(open(sys.argv[1]).read())
    from swebench.harness.run_evaluation import main as run_eval_main

    run_eval_main(**kwargs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
