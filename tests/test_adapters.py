"""Printer adapters build safe argv lists (no shell) and fail in the right *category*."""
from pathlib import Path

from smartprint.server.adapters.printer import CupsPrinter, PrintError, PrintRequest, SumatraPrinter


def req(**kw) -> PrintRequest:
    base = dict(job_id="j1", code="SP-ABCD", attempt=1, file_path=Path("/tmp/a b; rm -rf x.pdf"), printer="Shop-Printer",
                copies=3, color="COLOR", duplex=True, paper="A4", pages=[1, 2, 3, 5])
    base.update(kw)
    return PrintRequest(**base)


def test_sumatra_command_for_windows_shops():
    cmd = SumatraPrinter(r"C:\S\SumatraPDF.exe").build_command(req())
    assert cmd[:4] == [r"C:\S\SumatraPDF.exe", "-print-to", "Shop-Printer", "-print-settings"]
    assert cmd[4] == "1-3,5,color,duplexlong,paper=a4,3x"
    assert "-silent" in cmd and cmd[-1] == "/tmp/a b; rm -rf x.pdf"      # one argv element: never shell-interpreted


def test_sumatra_single_copy_bw_simplex():
    assert SumatraPrinter("x").print_settings(req(copies=1, color="BW", duplex=False, pages=[4])) == "4,monochrome,simplex,paper=a4"


def test_sumatra_missing_exe_is_retryable_and_unhealthy(tmp_path):
    p = SumatraPrinter(str(tmp_path / "nope.exe"))
    ok, detail = p.health()
    assert not ok and "not found" in detail
    try:
        p.submit(req())
    except PrintError as e:
        assert e.retryable                     # nothing was sent to the printer -> safe to retry
    else:
        raise AssertionError("expected PrintError")


def test_cups_command_is_an_argv_list():
    cmd = CupsPrinter().build_command(req())
    assert cmd[0] == "lp" and cmd[-2:] == ["--", "/tmp/a b; rm -rf x.pdf"]
    assert "page-ranges=1-3,5" in cmd and "sides=two-sided-long-edge" in cmd and "print-color-mode=color" in cmd
