"""Composition root: the few long-lived collaborators, wired once at startup."""
from __future__ import annotations

from dataclasses import dataclass

from .adapters.payment import MockProvider, PaymentProvider
from .adapters.printer import CupsPrinter, ManualXerox, PrinterAdapter, SpoolPrinter, SumatraPrinter
from .config import Settings
from .db import Database
from .security import LoginThrottle


@dataclass
class AppContext:
    settings: Settings
    db: Database
    provider: PaymentProvider
    printer: PrinterAdapter
    xerox: ManualXerox
    throttle: LoginThrottle


def build_context(settings: Settings) -> AppContext:
    settings.validate_for_runtime()
    settings.ensure_dirs()
    db = Database(settings.database_url)
    provider = MockProvider(settings.webhook_secret)
    printer: PrinterAdapter
    if settings.printer_backend == "cups":
        printer = CupsPrinter()
    elif settings.printer_backend == "sumatra":
        printer = SumatraPrinter(settings.sumatra_path)
    else:
        printer = SpoolPrinter(settings.spool_dir)
    return AppContext(settings, db, provider, printer, ManualXerox(), LoginThrottle())
