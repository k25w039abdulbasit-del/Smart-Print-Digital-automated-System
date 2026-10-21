"""Small reusable widgets: cards, pills, stepper, segmented control, QR code, bar chart."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget, QGraphicsDropShadowEffect)

from .. import theme


def label(text: str = "", name: str = "", wrap: bool = False, align: Optional[Qt.AlignmentFlag] = None) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    if align is not None:
        lb.setAlignment(align)
    return lb


def card(layout_cls=QVBoxLayout, margins: int = 20, spacing: int = 12, shadow: bool = True, name: str = "card") -> QFrame:
    f = QFrame()
    f.setObjectName(name)
    lay = layout_cls(f)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    if shadow:
        e = QGraphicsDropShadowEffect(f)
        e.setBlurRadius(24)
        e.setOffset(0, 4)
        e.setColor(QColor(11, 31, 58, 28))
        f.setGraphicsEffect(e)
    return f


class Pill(QLabel):
    """Rounded status chip coloured by job state."""

    def __init__(self, text: str = "", color: str = theme.MUTED):
        super().__init__(text)
        self.set(text, color)

    def set(self, text: str, color: str) -> None:
        self.setText(text)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"background:{QColor(color).lighter(180).name()}; color:{color}; border-radius:11px;"
                           f"padding:3px 12px; font-weight:700; font-size:12px;")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


class Stepper(QWidget):
    """FILE -> SETTINGS -> PAY -> PRINT, exactly as drawn on slide 2."""

    def __init__(self, steps: Sequence[str] = ("FILE", "SETTINGS", "PAY", "PRINT")):
        super().__init__()
        self.steps, self.current = list(steps), 0
        self.setFixedHeight(64)

    def set_step(self, i: int) -> None:
        self.current = i
        self.update()

    def paintEvent(self, _) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n, w, h = len(self.steps), self.width(), self.height()
        seg = w / n
        r = 15
        for i, name in enumerate(self.steps):
            cx, cy = seg * i + seg / 2, h / 2 - 8
            if i < n - 1:
                done = i < self.current
                p.setPen(QPen(QColor(theme.GREEN if done else theme.LINE), 3))
                p.drawLine(QPointF(cx + r + 6, cy), QPointF(cx + seg - r - 6, cy))
            state = "done" if i < self.current else "now" if i == self.current else "todo"
            fill = {"done": theme.GREEN, "now": theme.BLUE, "todo": "#FFFFFF"}[state]
            p.setPen(QPen(QColor(theme.LINE if state == "todo" else fill), 2))
            p.setBrush(QBrush(QColor(fill)))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setPen(QColor("white" if state != "todo" else theme.MUTED))
            f = QFont(self.font())
            f.setBold(True)
            f.setPixelSize(14)
            p.setFont(f)
            p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, "✓" if state == "done" else str(i + 1))
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor(theme.INK if state == "now" else theme.MUTED))
            p.drawText(QRectF(cx - seg / 2, cy + r + 4, seg, 18), Qt.AlignCenter, name)


class Segmented(QWidget):
    """Pick-one buttons (B/W | Colour). Emits `changed(value)`."""
    changed = Signal(object)

    def __init__(self, options: Sequence[tuple[str, object]], value: object = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._values: dict[int, object] = {}
        for i, (text, val) in enumerate(options):
            b = QPushButton(text.replace("&", "&&"))   # '&' would otherwise become a keyboard mnemonic
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, i)
            self._values[i] = val
            lay.addWidget(b, 1)
        self.group.idClicked.connect(lambda i: self.changed.emit(self._values[i]))
        self.set_value(value if value is not None else options[0][1])

    def value(self) -> object:
        return self._values[self.group.checkedId()]

    def set_value(self, v: object) -> None:
        for i, val in self._values.items():
            if val == v:
                self.group.button(i).setChecked(True)


class QRWidget(QWidget):
    def __init__(self, size: int = 220):
        super().__init__()
        self.setFixedSize(size, size)
        self._matrix: list[list[bool]] = []

    def set_data(self, text: str) -> None:
        import segno
        self._matrix = [[bool(c) for c in row] for row in segno.make(text, error="m").matrix]
        self.update()

    def paintEvent(self, _) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor("white"))
        if not self._matrix:
            return
        n = len(self._matrix)
        quiet = 2
        cell = self.width() / (n + 2 * quiet)
        p.setBrush(QColor(theme.NAVY))
        p.setPen(Qt.NoPen)
        for y, row in enumerate(self._matrix):
            for x, on in enumerate(row):
                if on:
                    p.drawRect(QRectF((x + quiet) * cell, (y + quiet) * cell, cell + 0.6, cell + 0.6))


class BarChart(QWidget):
    """Tiny dependency-free bar chart (hourly revenue)."""

    def __init__(self):
        super().__init__()
        self.values: list[float] = []
        self.labels: list[str] = []
        self.setMinimumHeight(160)

    def set_data(self, values: Sequence[float], labels: Sequence[str]) -> None:
        self.values, self.labels = list(values), list(labels)
        self.update()

    def paintEvent(self, _) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.values:
            return
        w, h, pad_b = self.width(), self.height(), 22
        top = max(max(self.values), 1)
        n = len(self.values)
        bw = w / n
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawLine(0, h - pad_b, w, h - pad_b)
        f = QFont(self.font())
        f.setPixelSize(10)
        p.setFont(f)
        for i, v in enumerate(self.values):
            bh = (h - pad_b - 14) * v / top
            rect = QRectF(i * bw + bw * 0.18, h - pad_b - bh, bw * 0.64, bh)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BLUE if v else theme.LINE))
            p.drawRoundedRect(rect if v else QRectF(rect.x(), h - pad_b - 3, rect.width(), 3), 3, 3)
            if i % 3 == 0:
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(i * bw - 4, h - pad_b + 4, bw + 8, 14), Qt.AlignCenter, self.labels[i])


class NumberStepper(QWidget):
    """Touch-friendly  [-]  12  [+]  control (plain label: a styled QSpinBox collapses its editor)."""
    valueChanged = Signal(int)

    def __init__(self, lo: int = 1, hi: int = 100, value: int = 1):
        super().__init__()
        self._lo, self._hi, self._value = lo, hi, min(max(value, lo), hi)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.minus, self.plus = QPushButton("\u2212"), QPushButton("+")
        for b in (self.minus, self.plus):
            b.setObjectName("seg")
            b.setFixedWidth(52)
            b.setCursor(Qt.PointingHandCursor)
            b.setAutoRepeat(True)                    # hold to change quickly
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(80)
        self.display = QLabel(str(self._value))
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setObjectName("stepperValue")
        self.display.setStyleSheet("QLabel#stepperValue { background: white; border: 1px solid #DCE3EE; "
                                   "border-radius: 10px; padding: 9px 12px; font-size: 17px; font-weight: 700; }")
        self.minus.clicked.connect(lambda: self.setValue(self._value - 1))
        self.plus.clicked.connect(lambda: self.setValue(self._value + 1))
        lay.addWidget(self.minus)
        lay.addWidget(self.display, 1)
        lay.addWidget(self.plus)
        self._sync()

    def _sync(self) -> None:
        self.display.setText(str(self._value))
        self.minus.setEnabled(self._value > self._lo)
        self.plus.setEnabled(self._value < self._hi)

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:
        v = min(max(int(v), self._lo), self._hi)
        if v != self._value:
            self._value = v
            self._sync()
            self.valueChanged.emit(v)

    def setRange(self, lo: int, hi: int) -> None:
        self._lo, self._hi = lo, hi
        self._value = min(max(self._value, lo), hi)
        self._sync()
