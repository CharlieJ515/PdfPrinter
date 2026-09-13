"""Core data model, validation and job persistence."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields


class PrintError(Exception):
    pass


@dataclass
class PrintJob:
    printer: str
    copies: int = 1
    page_range: str = ""  # e.g. "1-4,7"; empty means all pages
    duplex: str = "one-sided"  # one-sided | two-sided-long-edge | two-sided-short-edge
    color_mode: str = ""  # "" (printer default) | color | monochrome
    media: str = ""  # "" (printer default) | A4 | Letter | ...
    landscape: bool = False
    number_up: int = 1  # pages per sheet: 1, 2, 4, 6, 9, 16
    number_up_layout: str = ""  # "" (default) | lrtb | tblr | rltb | btlr
    collate: bool = False
    page_set: str = ""  # "" (all) | odd | even
    reverse: bool = False
    scaling: str = ""  # "" (printer default) | fit | fill | none
    # margins in millimetres; 0 means printer default
    margin_left: float = 0.0
    margin_right: float = 0.0
    margin_top: float = 0.0
    margin_bottom: float = 0.0
    # swap left/right margins on even pages (binding edge for duplex)
    mirror_margins: bool = False
    # show a punch guide 18 mm from the binding edge in the PREVIEW
    # only — it is never part of the printed output
    hole_guide: bool = False
    # manual content scale; 100 = original size
    scale_percent: int = 100
    # how margins place the content:
    #   fit       — shrink into the margin box (nothing clips)
    #   shift     — translate at original size (opposite edge may clip)
    #   hole      — center the ink between the punch line and the far
    #               edge, shrinking only if it is wider than the zone
    #   hole-clip — same centering, never shrinks (may clip both edges)
    margin_mode: str = "fit"
    # the printer's unprintable border (left, bottom, right, top) in
    # points, from its PPD; punch-zone placement keeps content out of it
    hw_margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    # printer-specific PPD choices, e.g. {"BRResolution": "Fine"}
    extra_options: dict[str, str] = field(default_factory=dict)


@dataclass
class PPDOption:
    """One option a printer's driver exposes, from `lpoptions -l`."""

    keyword: str
    label: str
    choices: list[str]
    default: str | None


def _last_job_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "pdfprinter", "last_print.json")


def save_last_job(job: PrintJob) -> None:
    """Remember a submitted job's configuration; failures are non-fatal."""
    try:
        path = _last_job_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(job), fh, indent=2)
    except OSError:
        pass


def load_last_job() -> PrintJob | None:
    try:
        with open(_last_job_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    known = {f.name for f in fields(PrintJob)}
    try:
        return PrintJob(**{k: v for k, v in data.items() if k in known})
    except TypeError:
        return None


def validate_page_range(text: str) -> bool:
    """Accept forms like "3", "1-4", "1-4,7,10-12" and open ends "4-"."""
    if not text:
        return True
    return (
        re.fullmatch(r"\d+(-\d*)?(,\d+(-\d*)?)*", text.replace(" ", ""))
        is not None
    )


def _expand_open_ranges(text: str, end: str) -> str:
    """Rewrite open-ended items ("4-") with an explicit end marker.

    CUPS's pdftopdf silently drops "4-" style items, but clamps
    oversized ends, so "9999" works there; qpdf wants "z".
    """
    return re.sub(r"(\d+)-(?=,|$)", rf"\g<1>-{end}", text.replace(" ", ""))


MM_TO_PT = 72.0 / 25.4


HOLE_GUIDE_MM = 18.0

