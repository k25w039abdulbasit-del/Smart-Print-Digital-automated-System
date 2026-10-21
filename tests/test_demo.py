"""The narrated demo is also an acceptance test: it must pass against a real HTTP server."""
from smartprint.demo import run_tour


def test_demo_tour_passes(capsys):
    assert run_tour() == 0
    out = capsys.readouterr().out
    assert "All 19 checks passed" in out
