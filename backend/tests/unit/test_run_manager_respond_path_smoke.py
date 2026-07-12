"""Smoke test for the T8 respond-path scaffolding.

Full behavioural coverage of ``_run_cubepi_respond_path`` /
``_execute_respond_run`` will come via T9's ``resume_run_with_answer`` test
and T16's E2E. This file just locks in the public surface T9 / T10 will
build against:

* ``_run_cubepi_respond_path`` exists on ``RunManager`` and accepts the
  documented keyword-only parameters (``question_id``, ``answer``,
  ``claim_token``);
* ``_execute_respond_run`` exists with the same parameter set.

If T9/T10 grow extra kwargs, that's fine — we only assert presence here,
not signature equality.
"""

from __future__ import annotations

import inspect

from cubeplex.streams.run_manager import RunManager


def test_run_cubepi_respond_path_signature():
    assert hasattr(RunManager, "_run_cubepi_respond_path")
    sig = inspect.signature(RunManager._run_cubepi_respond_path)
    params = set(sig.parameters)
    for required in (
        "ctx",
        "run_id",
        "conversation_id",
        "question_id",
        "answer",
        "claim_token",
        "effective_system_prompt",
        "publish_stream_event",
        "flush_citation_buffer",
        "citation_buffers",
    ):
        assert required in params, f"_run_cubepi_respond_path missing {required!r}"


def test_execute_respond_run_signature():
    assert hasattr(RunManager, "_execute_respond_run")
    sig = inspect.signature(RunManager._execute_respond_run)
    params = set(sig.parameters)
    for required in (
        "run_id",
        "conversation_id",
        "question_id",
        "answer",
        "claim_token",
        "ctx",
    ):
        assert required in params, f"_execute_respond_run missing {required!r}"
