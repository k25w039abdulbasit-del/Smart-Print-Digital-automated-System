"""Printer adapter layer (slide 7: PRINTER - adapter layer).

The business code only knows `PrinterAdapter`. Today: a spool-folder printer (dev,
demos, CI), CUPS (`lp`) for Linux/macOS print servers and SumatraPDF for Windows. An IPP
adapter is one more small class, with no change to the domain logic.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from pypdf import PdfReader, PdfWriter


class PrintError(Exception):
    """`retryable=True` means *nothing was printed* and trying again is safe."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class PrintRequest:
    job_id: str
    code: str
    attempt: int
    file_path: Path
    printer: str
    copies: int
    color: str            # BW | COLOR
    duplex: bool
    paper: str            # A4 | A3 | LETTER
    pages: list[int]      # 1-based pages to print (already validated)


class PrinterAdapter(Protocol):
    name: str

    def submit(self, req: PrintRequest) -> str: ...
    def health(self) -> tuple[bool, str]: ...


class SpoolPrinter:
    """Writes the exact pages that would reach the paper into data/spool/<code>/.

    Great for demos, CI and development: the output is a real PDF + a JSON ticket.
    `fail_next` lets tests and the demo inject printer faults.
    """
    name = "spool"

    def __init__(self, spool_dir: Path):
        self.spool_dir = spool_dir
        self.fail_next: Optional[PrintError] = None
        self.delay_s: float = 0.0

    def submit(self, req: PrintRequest) -> str:
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        out = self.spool_dir / f"{req.code}-a{req.attempt}"
        out.mkdir(parents=True, exist_ok=True)
        reader = PdfReader(str(req.file_path))
        writer = PdfWriter()
        for _ in range(req.copies):
            for p in req.pages:
                writer.add_page(reader.pages[p - 1])
        with (out / "output.pdf").open("wb") as f:
            writer.write(f)
        (out / "ticket.json").write_text(json.dumps({
            "job": req.job_id, "code": req.code, "printer": req.printer, "copies": req.copies,
            "color": req.color, "duplex": req.duplex, "paper": req.paper, "pages": req.pages,
            "total_sides": len(req.pages) * req.copies,
        }, indent=2))
        return f"spool:{out.name}"

    def health(self) -> tuple[bool, str]:
        return self.spool_dir.exists(), f"spool folder {self.spool_dir}"


class CupsPrinter:
    """Real printing through CUPS `lp`. Command is built as an argv list - no shell."""
    name = "cups"

    def __init__(self, lp: str = "lp", lpstat: str = "lpstat"):
        self.lp, self.lpstat = lp, lpstat

    @staticmethod
    def page_ranges(pages: list[int]) -> str:
        out, start, prev = [], pages[0], pages[0]
        for p in pages[1:] + [None]:  # type: ignore[list-item]
            if p is not None and p == prev + 1:
                prev = p
                continue
            out.append(str(start) if start == prev else f"{start}-{prev}")
            if p is not None:
                start = prev = p
        return ",".join(out)

    def build_command(self, req: PrintRequest) -> list[str]:
        cmd = [self.lp, "-d", req.printer, "-t", f"SmartPrint {req.code}", "-n", str(req.copies),
               "-o", f"media={req.paper.title() if req.paper == 'LETTER' else req.paper}",
               "-o", "sides=two-sided-long-edge" if req.duplex else "sides=one-sided",
               "-o", "print-color-mode=color" if req.color == "COLOR" else "print-color-mode=monochrome"]
        cmd += ["-o", f"page-ranges={self.page_ranges(req.pages)}"]
        return cmd + ["--", str(req.file_path)]

    def submit(self, req: PrintRequest) -> str:
        if not shutil.which(self.lp):
            raise PrintError("CUPS 'lp' command not found", retryable=True)
        try:
            res = subprocess.run(self.build_command(req), capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            raise PrintError("CUPS did not answer in time - job may or may not be queued", retryable=False) from e
        if res.returncode != 0:
            raise PrintError(res.stderr.strip() or "lp failed", retryable=True)
        m = re.search(r"request id is (\S+)", res.stdout)
        return f"cups:{m.group(1) if m else 'unknown'}"

    def health(self) -> tuple[bool, str]:
        if not shutil.which(self.lpstat):
            return False, "CUPS not installed"
        res = subprocess.run([self.lpstat, "-r"], capture_output=True, text=True)
        return "is running" in res.stdout, res.stdout.strip() or res.stderr.strip()


class SumatraPrinter:
    """Windows printing through the SumatraPDF command line (silent, no shell, no GUI popups).

    Windows print shops are the common case, and Windows has no `lp`. SumatraPDF is a small,
    free PDF viewer with a stable CLI:  -print-to <printer> -print-settings "<list>" -silent.
    The argv is built (and unit-tested) here; validate once on the shop's real printer.
    """
    name = "sumatra"

    def __init__(self, exe: str):
        self.exe = exe

    @staticmethod
    def print_settings(req: PrintRequest) -> str:
        parts = [CupsPrinter.page_ranges(req.pages),
                 "color" if req.color == "COLOR" else "monochrome",
                 "duplexlong" if req.duplex else "simplex",
                 f"paper={req.paper.lower()}"]
        if req.copies > 1:
            parts.append(f"{req.copies}x")
        return ",".join(parts)

    def build_command(self, req: PrintRequest) -> list[str]:
        return [self.exe, "-print-to", req.printer, "-print-settings", self.print_settings(req),
                "-silent", "-exit-when-done", str(req.file_path)]

    def submit(self, req: PrintRequest) -> str:
        if not Path(self.exe).exists():
            raise PrintError(f"SumatraPDF not found at {self.exe}", retryable=True)
        try:
            res = subprocess.run(self.build_command(req), capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            raise PrintError("SumatraPDF did not finish in time - pages may have printed", retryable=False) from e
        if res.returncode != 0:
            raise PrintError(res.stderr.strip() or f"SumatraPDF exited with {res.returncode}", retryable=True)
        return f"sumatra:{req.code}-a{req.attempt}"

    def health(self) -> tuple[bool, str]:
        ok = Path(self.exe).exists()
        return ok, f"SumatraPDF at {self.exe}" if ok else f"SumatraPDF not found at {self.exe}"


class XeroxAdapter(Protocol):
    supports_automation: bool

    def instructions(self, code: str, copies: int, color: str, paper: str, duplex: bool, originals: int) -> str: ...


class ManualXerox:
    """Slide 6/10: Xerox is staff-assisted unless a verified MFP integration exists.

    A future `MfpXerox` adapter can set supports_automation=True and trigger the copy
    job itself; the workflow (configure -> price -> pay -> verify) does not change.
    """
    supports_automation = False

    def instructions(self, code, copies, color, paper, duplex, originals) -> str:
        side = "double-sided" if duplex else "single-sided"
        return (f"Copy {originals} original page(s) x {copies} on {paper}, "
                f"{'colour' if color == 'COLOR' else 'black & white'}, {side}.")
