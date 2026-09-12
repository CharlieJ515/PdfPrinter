"""CUPS printing backend.

Talks to CUPS through the command-line tools (lpstat/lp) so the PDF is
handed to CUPS as-is — no rasterization on our side and no extra Python
dependencies.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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
    # print a dashed line 18 mm from the binding edge as a punch guide
    hole_guide: bool = False
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


def list_printers() -> list[str]:
    """Return printer names known to CUPS."""
    try:
        out = subprocess.run(
            ["lpstat", "-e"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Cannot query CUPS: {exc}") from exc
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def default_printer() -> str | None:
    try:
        out = subprocess.run(
            ["lpstat", "-d"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # "system default destination: PrinterName"
    match = re.search(r"destination:\s*(\S+)", out.stdout)
    return match.group(1) if match else None


def printer_options(printer: str) -> dict[str, PPDOption]:
    """Return the driver options a printer supports, keyed by PPD keyword.

    Parses `lpoptions -p <printer> -l` lines of the form
    "Keyword/Label: choice1 *default choice2".
    """
    try:
        out = subprocess.run(
            ["lpoptions", "-p", printer, "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Cannot query printer options: {exc}") from exc
    options: dict[str, PPDOption] = {}
    for line in out.stdout.splitlines():
        head, sep, tail = line.partition(":")
        if not sep or "/" not in head:
            continue
        keyword, _, label = head.partition("/")
        choices = []
        default = None
        for token in tail.split():
            if token.startswith("*"):
                token = token[1:]
                default = token
            choices.append(token)
        options[keyword.strip()] = PPDOption(
            keyword.strip(), label.strip(), choices, default
        )
    return options


def find_option(
    options: dict[str, PPDOption], *suffixes: str
) -> PPDOption | None:
    """Find an option whose keyword ends with one of the given suffixes.

    Vendors prefix standard PPD keywords (Brother's BRInputSlot, Canon's
    InputSlot), so suffix matching finds the equivalent across drivers.
    """
    for suffix in suffixes:
        for keyword, option in options.items():
            if keyword.lower().endswith(suffix):
                return option
    return None


def supports_duplex(options: dict[str, PPDOption]) -> bool:
    return any("duplex" in keyword.lower() for keyword in options)


def validate_page_range(text: str) -> bool:
    """Accept forms like "3", "1-4", "1-4,7,10-12"."""
    if not text:
        return True
    return re.fullmatch(r"\d+(-\d+)?(,\d+(-\d+)?)*", text.replace(" ", "")) is not None


PDFTOPDF = "/usr/lib/cups/filter/pdftopdf"
# argument conventions across cups-filters versions, probed at first use:
# 2.x: job-id user title copies options [file]
# 1.x: printer job-id user title copies options [file]
_pdftopdf_args: list[str] | None = None


def pdftopdf_available() -> bool:
    return os.access(PDFTOPDF, os.X_OK)


MM_TO_PT = 72.0 / 25.4


def layout_options(job: PrintJob) -> list[str]:
    """The job options that change the page layout, as option=value strings.

    Used both to build the lp command and to run the pdftopdf preview,
    so the preview and the printed job can never diverge. Physical
    options (quality, tray, media type, color, duplex) don't belong here.
    """
    parts: list[str] = []
    if job.page_range and validate_page_range(job.page_range):
        parts.append(f"page-ranges={job.page_range.replace(' ', '')}")
    if job.page_set:
        parts.append(f"page-set={job.page_set}")
    if job.number_up > 1:
        parts.append(f"number-up={job.number_up}")
        if job.number_up_layout:
            parts.append(f"number-up-layout={job.number_up_layout}")
    if job.landscape:
        parts.append("landscape")
    if job.reverse:
        parts.append("outputorder=reverse")
    if job.media:
        parts.append(f"media={job.media}")

    if job.scaling == "fit":
        # fit-to-page for classic driver queues, print-scaling for IPP ones
        parts += ["fit-to-page", "print-scaling=fit"]
    elif job.scaling:
        parts.append(f"print-scaling={job.scaling}")
    return parts


HOLE_GUIDE_MM = 18.0


def margins_active(job: PrintJob) -> bool:
    return any(
        m > 0
        for m in (
            job.margin_left,
            job.margin_right,
            job.margin_top,
            job.margin_bottom,
        )
    )


def needs_gs_pass(job: PrintJob) -> bool:
    return margins_active(job) or job.hole_guide


def _margin_ps(job: PrintJob) -> str:
    """BeginPage hook: scale and center each page inside the margins."""
    left = job.margin_left * MM_TO_PT
    right = job.margin_right * MM_TO_PT
    top = job.margin_top * MM_TO_PT
    bottom = job.margin_bottom * MM_TO_PT
    mirror = "true" if job.mirror_margins else "false"
    return (
        f"/MLbase {left:.2f} def /MRbase {right:.2f} def "
        f"/MT {top:.2f} def /MB {bottom:.2f} def "
        f"/MIRROR {mirror} def "
        "<< /BeginPage { "
        # showpage count on the stack -> 1-based page number
        "1 add /PN exch def "
        "MIRROR PN 2 mod 0 eq and "
        "{ /ML MRbase def /MR MLbase def } "
        "{ /ML MLbase def /MR MRbase def } ifelse "
        "currentpagedevice /PageSize get aload pop /PH exch def /PW exch def "
        "/SX PW ML MR add sub PW div def "
        "/SY PH MT MB add sub PH div def "
        "/S SX SY 2 copy gt {exch} if pop def "
        "ML PW PW S mul sub ML MR add sub 2 div add "
        "MB PH PH S mul sub MT MB add sub 2 div add "
        "translate S S scale "
        "} >> setpagedevice"
    )


def _hole_guide_ps(job: PrintJob) -> str:
    """EndPage hook: stamp a dashed punch guide at the binding edge.

    pdfwrite drops marks made in BeginPage, so the line is stamped in
    EndPage (the watermark technique). Run as its own pass so the
    margin transform can't displace it.
    """
    mirror = "true" if job.mirror_margins else "false"
    return (
        f"/MIRROR {mirror} def /HG {HOLE_GUIDE_MM * MM_TO_PT:.2f} def "
        "<< /EndPage { exch 1 add /PN exch def 2 ne dup { "
        "gsave newpath 0.6 setgray 0.75 setlinewidth [4 4] 0 setdash "
        "currentpagedevice /PageSize get aload pop /PH exch def /PW exch def "
        "MIRROR PN 2 mod 0 eq and { PW HG sub } { HG } ifelse "
        "dup 0 moveto PH lineto stroke grestore "
        "} if } >> setpagedevice"
    )


def _run_gs(src: str, dest: str, postscript: str) -> None:
    cmd = [
        "gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER",
        "-sDEVICE=pdfwrite", "-o", dest, "-c", postscript, "-f", src,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Failed to run gs: {exc}") from exc
    if out.returncode != 0 or not os.path.exists(dest):
        message = (out.stderr or out.stdout).strip()
        raise PrintError(message.splitlines()[-1] if message else "gs failed")


def apply_margins(src: str, dest: str, job: PrintJob) -> None:
    """Apply margins and/or the punch guide by rewriting the PDF.

    CUPS's pdftopdf ignores the page-left/right/top/bottom options, so
    Ghostscript does the work: one pass scales and centers each page
    inside the margin box (mirrored on even pages if requested), and a
    separate pass stamps the punch guide line. The same file is used
    for printing and previewing, so both always match.
    """
    if shutil.which("gs") is None:
        raise PrintError("Ghostscript (gs) is required for margins")
    passes = []
    if margins_active(job):
        passes.append(_margin_ps(job))
    if job.hole_guide:
        passes.append(_hole_guide_ps(job))
    if not passes:
        raise PrintError("No margin or guide options set")
    with tempfile.TemporaryDirectory(prefix="pdfprinter-gs-") as tmpdir:
        work = src
        for index, postscript in enumerate(passes):
            out = (
                dest
                if index == len(passes) - 1
                else os.path.join(tmpdir, f"pass{index}.pdf")
            )
            _run_gs(work, out, postscript)
            work = out


def preview_job_options(job: PrintJob) -> str:
    return " ".join(layout_options(job))


def transform_for_preview(src: str, options: str, dest: str) -> None:
    """Run CUPS's pdftopdf filter to produce the as-printed document."""
    global _pdftopdf_args
    if not pdftopdf_available():
        raise PrintError(f"{PDFTOPDF} not available")

    candidates = (
        [_pdftopdf_args]
        if _pdftopdf_args is not None
        else [["1", "user", "title", "1"], ["printer", "1", "user", "title", "1"]]
    )
    last_error = ""
    for args in candidates:
        try:
            with open(dest, "wb") as out_file:
                result = subprocess.run(
                    [PDFTOPDF, *args, options, src],
                    stdout=out_file,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PrintError(f"Failed to run pdftopdf: {exc}") from exc
        if result.returncode == 0:
            _pdftopdf_args = args
            return
        last_error = result.stderr.decode(errors="replace").strip()
    raise PrintError(last_error.splitlines()[-1] if last_error else "pdftopdf failed")


def make_page_subset(src: str, page_range: str, dest: str) -> None:
    """Write a PDF containing only the given pages, using qpdf.

    Used for the print preview; the actual print job always sends the
    original file with a CUPS page-ranges option.
    """
    if not page_range or not validate_page_range(page_range):
        raise PrintError(f"Invalid page range: {page_range!r}")
    cmd = ["qpdf", src, "--pages", ".", page_range.replace(" ", ""), "--", dest]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Failed to run qpdf: {exc}") from exc
    # qpdf exits 3 for warnings while still producing output
    if out.returncode not in (0, 3):
        raise PrintError(out.stderr.strip() or "qpdf failed")


def print_file(path: str, job: PrintJob) -> str:
    """Submit a file to CUPS. Returns the CUPS job id string."""
    if not job.printer:
        raise PrintError("No printer selected")
    if not validate_page_range(job.page_range):
        raise PrintError(f"Invalid page range: {job.page_range!r}")

    cmd = ["lp", "-d", job.printer, "-n", str(job.copies)]
    cmd += ["-o", f"sides={job.duplex}"]
    if job.color_mode:
        cmd += ["-o", f"print-color-mode={job.color_mode}"]
    if job.collate and job.copies > 1:
        cmd += ["-o", "collate=true"]
    for key, value in job.extra_options.items():
        cmd += ["-o", f"{key}={value}"]
    try:
        with tempfile.TemporaryDirectory(prefix="pdfprinter-") as tmpdir:
            # apply layout locally (same as the preview), THEN margins, so
            # mirrored margins follow the final output order — e.g. with
            # pages 2-5, output page 1 (source page 2) gets the left
            # margin. lp spools a copy, so the temp files can go after.
            work = path
            options = layout_options(job)
            if options and pdftopdf_available():
                layout_path = os.path.join(tmpdir, "layout.pdf")
                transform_for_preview(work, " ".join(options), layout_path)
                work = layout_path
                if job.media:  # still selects the paper/tray
                    cmd += ["-o", f"media={job.media}"]
            else:
                # no local pdftopdf: fall back to server-side options
                for option in options:
                    cmd += ["-o", option]
            if needs_gs_pass(job):
                margined = os.path.join(tmpdir, "margined.pdf")
                apply_margins(work, margined, job)
                work = margined
            cmd += ["--", work]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Failed to run lp: {exc}") from exc
    if out.returncode != 0:
        raise PrintError(out.stderr.strip() or "lp failed")

    # "request id is Printer-123 (1 file(s))"
    match = re.search(r"request id is (\S+)", out.stdout)
    return match.group(1) if match else out.stdout.strip()
