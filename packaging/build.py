"""Build stand-alone executables with PyInstaller (slide 8: PACKAGING).

    python packaging/build.py kiosk staff server      # any subset; default = all three
    python packaging/build.py --onefile kiosk         # single .exe instead of a folder

Output lands in dist/<name>/ . Build on the OS you are targeting (PyInstaller does not cross-compile):
build on Windows for the shop PCs. Customers' PCs get `smartprint-kiosk`; the counter PC gets
`smartprint-staff`; the server machine gets `smartprint-server` (+ PostgreSQL).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import PyInstaller.__main__ as pyi

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

TARGETS = {
    "kiosk": dict(entry="entry_kiosk.py", name="smartprint-kiosk", windowed=True,
                  extra=["--collect-submodules", "smartprint.client"]),
    "staff": dict(entry="entry_staff.py", name="smartprint-staff", windowed=True,
                  extra=["--collect-submodules", "smartprint.client"]),
    "server": dict(entry="entry_server.py", name="smartprint-server", windowed=False,
                   extra=["--collect-submodules", "smartprint.server", "--collect-submodules", "uvicorn",
                          "--collect-all", "pypdfium2", "--collect-all", "psycopg_binary", "--collect-all", "reportlab",
                          "--hidden-import", "sqlalchemy.dialects.postgresql.psycopg",
                          "--add-data", f"{ROOT / 'alembic'}{';' if sys.platform == 'win32' else ':'}alembic",
                          "--add-data", f"{ROOT / 'alembic.ini'}{';' if sys.platform == 'win32' else ':'}."]),
}


def build(target: str, onefile: bool) -> None:
    t = TARGETS[target]
    args = [str(HERE / t["entry"]), "--name", t["name"], "--noconfirm", "--clean",
            "--paths", str(ROOT / "src"),
            "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / target),
            "--specpath", str(ROOT / "build"),
            "--onefile" if onefile else "--onedir"]
    if t["windowed"]:
        args.append("--windowed")
    pyi.run(args + t["extra"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*", choices=[*TARGETS, []], default=list(TARGETS))
    ap.add_argument("--onefile", action="store_true")
    a = ap.parse_args()
    for t in (a.targets or list(TARGETS)):
        build(t, a.onefile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
