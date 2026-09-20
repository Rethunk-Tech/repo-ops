"""The worker pool and each worker's CPU share must stay within one machine."""

import os

from repo_ops_upgrade.run import _cap_child_cpu, _default_jobs


def test_default_jobs_leaves_headroom_for_gate_parallelism(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    assert _default_jobs() == 8
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert _default_jobs() == 4


def test_cap_child_cpu_divides_the_machine_and_respects_an_explicit_value(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    monkeypatch.delenv("GOMAXPROCS", raising=False)
    _cap_child_cpu(8)
    assert os.environ["GOMAXPROCS"] == "4"

    monkeypatch.setenv("GOMAXPROCS", "1")
    _cap_child_cpu(8)
    assert os.environ["GOMAXPROCS"] == "1"
