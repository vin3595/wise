"""The shape every report returns, and its CSV download (pure, unit-tested).

A report is a table: columns (with a kind that says how to show and total them), rows, and a totals
row. The Reports screen and the CSV/Excel download both render from this, so adding a report means
writing one query, nothing on the screen.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

KINDS = {"text", "money", "int", "percent", "date", "time", "datetime"}


@dataclass(frozen=True)
class Col:
    key: str
    label: str
    kind: str = "text"   # money values are paise; percent values are basis points
    total: bool = False  # sum this column into the totals row

    def __post_init__(self) -> None:
        assert self.kind in KINDS, self.kind


@dataclass
class Table:
    title: str
    columns: list[Col]
    rows: list[dict] = field(default_factory=list)
    note: str | None = None
    totals_label: str = "Total"

    def totals(self) -> dict | None:
        summed = [c for c in self.columns if c.total]
        if not summed or not self.rows:
            return None
        out: dict = {self.columns[0].key: self.totals_label}
        for c in summed:
            out[c.key] = sum(int(r.get(c.key) or 0) for r in self.rows)
        return out

    def as_dict(self) -> dict:
        return {"title": self.title, "note": self.note,
                "columns": [{"key": c.key, "label": c.label, "kind": c.kind} for c in self.columns],
                "rows": self.rows, "totals": self.totals()}


def _cell(value, kind: str) -> str:
    if value is None:
        return ""
    if kind == "money":
        return f"{int(value) / 100:.2f}"
    if kind == "percent":
        return f"{int(value) / 100:.2f}"
    return str(value)


def to_csv(t: Table, subtitle: str = "") -> str:
    """CSV that opens straight in Excel: money in rupees with 2 decimals, a totals row at the end.
    Starts with a byte-order mark so Excel reads ₹ and Hindi names correctly."""
    buf = io.StringIO()
    w = csv.writer(buf)
    if subtitle:
        w.writerow([f"{t.title}: {subtitle}"])
    w.writerow([c.label + (" (₹)" if c.kind == "money" else " (%)" if c.kind == "percent" else "") for c in t.columns])
    for r in t.rows:
        w.writerow([_cell(r.get(c.key), c.kind) for c in t.columns])
    tot = t.totals()
    if tot:
        w.writerow([_cell(tot.get(c.key), c.kind) if c.key in tot else "" for c in t.columns])
    return "﻿" + buf.getvalue()


def share_bp(part: int, whole: int) -> int:
    """part as basis points of whole (12.34% = 1234)."""
    return round(part * 10000 / whole) if whole else 0
