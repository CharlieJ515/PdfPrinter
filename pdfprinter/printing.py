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


_hw_margin_cache: dict[str, tuple[float, float, float, float] | None] = {}


def printer_hw_margins(
    printer: str,
) -> tuple[float, float, float, float] | None:
    """The printer's unprintable border (left, bottom, right, top) in pt.

    Read from the queue's PPD, or queried from the device for driverless
    (IPP) queues. None when it cannot be determined.
    """
    if printer in _hw_margin_cache:
        return _hw_margin_cache[printer]
    result = None
    for text in _ppd_sources(printer):
        result = _parse_hw_margins(text)
        if result is not None:
            break
    _hw_margin_cache[printer] = result
    return result


def _ppd_sources(printer: str):
    """Yield PPD texts for a queue, most direct source first."""
    try:
        with open(f"/etc/cups/ppd/{printer}.ppd", encoding="utf-8",
                  errors="replace") as fh:
            yield fh.read()
    except OSError:
        pass
    # cupsd serves permanent queues' PPDs regardless of file permissions
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://localhost:631/printers/{printer}.ppd", timeout=5
        ) as response:
            yield response.read().decode("utf-8", errors="replace")
    except OSError:
        pass
    # discovered/driverless printers: query the device itself
    uri = None
    try:
        for cmd in (["lpstat", "-v", printer], ["lpoptions", "-p", printer]):
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
            match = re.search(r"((?:ipps?|dnssd)://\S+)", out.stdout)
            if match:
                uri = match.group(1)
                break
    except (OSError, subprocess.TimeoutExpired):
        return
    if uri is None:
        return
    # dnssd URIs resolve through the same mDNS host in ipp form
    uri = re.sub(r"^dnssd://", "ipp://", uri).split("?")[0]
    try:
        ppd = subprocess.run(
            ["driverless", "cat", uri],
            capture_output=True, text=True, timeout=20,
        )
        if ppd.returncode == 0:
            yield ppd.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass


def _parse_hw_margins(ppd: str) -> tuple[float, float, float, float] | None:
    default = re.search(r"\*DefaultImageableArea:\s*(\S+)", ppd)
    name = re.escape(default.group(1)) if default else r"\S+"
    # PPD entries may carry a translation ("*ImageableArea A4/A4: ...")
    area = re.search(
        rf'\*ImageableArea\s+{name}(?:/[^:]*)?:\s*"([\d. ]+)"', ppd
    )
    dim = re.search(
        rf'\*PaperDimension\s+{name}(?:/[^:]*)?:\s*"([\d. ]+)"', ppd
    )
    if not area or not dim:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in area.group(1).split()[:4])
        width, height = (float(v) for v in dim.group(1).split()[:2])
    except ValueError:
        return None
    margins = (x0, y0, width - x1, height - y1)
    if any(m < 0 or m > 72 for m in margins):
        return None  # implausible; don't trust it
    return margins


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
    return (
        margins_active(job)
        or job.scale_percent != 100
        or job.margin_mode.startswith("hole")
    )


_bbox_cache: dict[tuple[str, float], list[tuple[float, float, float, float]]] = {}


def ink_boxes(src: str) -> list[tuple[float, float, float, float]]:
    """Per-page ink bounding boxes (x0, y0, x1, y1) via gs's bbox device."""
    key = (src, os.path.getmtime(src))
    cached = _bbox_cache.get(key)
    if cached is not None:
        return cached
    if shutil.which("gs") is None:
        raise PrintError("Ghostscript (gs) is required to measure content")
    cmd = ["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER",
           "-sDEVICE=bbox", "-o", os.devnull, "-f", src]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Failed to measure content: {exc}") from exc
    boxes = []
    for line in out.stderr.splitlines():
        if line.startswith("%%HiResBoundingBox:"):
            try:
                x0, y0, x1, y1 = (float(v) for v in line.split()[1:5])
            except ValueError:
                continue
            boxes.append((x0, y0, x1, y1))
    if not boxes:
        raise PrintError("Could not measure the content's bounding boxes")
    _bbox_cache[key] = boxes
    return boxes




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
        f"/MIRROR {mirror} def /USC {job.scale_percent / 100:.4f} def "
        f"/SHIFT {'true' if job.margin_mode == 'shift' else 'false'} def "
        "<< /BeginPage { "
        # showpage count on the stack -> 1-based page number
        "1 add /PN exch def "
        "MIRROR PN 2 mod 0 eq and "
        "{ /ML MRbase def /MR MLbase def } "
        "{ /ML MLbase def /MR MRbase def } ifelse "
        "currentpagedevice /PageSize get aload pop /PH exch def /PW exch def "
        "SHIFT { "
        # original size: translate only, may clip the opposite edge
        "/S USC def "
        "PW PW S mul sub 2 div ML MR sub add "
        "PH PH S mul sub 2 div MB MT sub add "
        "} { "
        "/SX PW ML MR add sub PW div def "
        "/SY PH MT MB add sub PH div def "
        "/S SX SY 2 copy gt {exch} if pop def "
        # manual scale composes with the margin fit, centered in the box
        "/S S USC mul def "
        "ML PW PW S mul sub ML MR add sub 2 div add "
        "MB PH PH S mul sub MT MB add sub 2 div add "
        "} ifelse "
        "translate S S scale "
        "} >> setpagedevice"
    )


def _run_gs(src: str, dest: str, postscript: str) -> None:
    # NOTE: do not add -dCompatibilityLevel=1.4 here — its output makes
    # CUPS's pdftopdf fail ("missing required flags"), which reaches the
    # printer as broken data (Canon error #853).
    # -dPreserveAnnots=false: broken Link annotations (no appearance
    # stream) make server-side pdftopdf corrupt the images on their
    # pages — figures silently vanish from the printout. Annotations
    # are not printable content, so drop them for print files.
    cmd = [
        "gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER",
        "-sDEVICE=pdfwrite", "-dPreserveAnnots=false",
        "-o", dest, "-c", postscript, "-f", src,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Failed to run gs: {exc}") from exc
    if out.returncode != 0 or not os.path.exists(dest):
        message = (out.stderr or out.stdout).strip()
        raise PrintError(message.splitlines()[-1] if message else "gs failed")


def _apply_margins_pikepdf(src: str, dest: str, job: PrintJob) -> None:
    """Wrap each page's content in a scale+center matrix with pikepdf.

    Exact and per-page: unlike the Ghostscript BeginPage approach, page
    sizes are left untouched (gs's PDF interpreter folds a BeginPage
    CTM scale into the output page size for non-default page sizes,
    silently cancelling the margins).
    """
    import pikepdf

    left = job.margin_left * MM_TO_PT
    right = job.margin_right * MM_TO_PT
    top = job.margin_top * MM_TO_PT
    bottom = job.margin_bottom * MM_TO_PT
    user_scale = job.scale_percent / 100.0

    inks = ink_boxes(src) if job.margin_mode.startswith("hole") else []

    with pikepdf.open(src) as pdf:
        for index, page in enumerate(pdf.pages):
            box = [float(v) for v in page.mediabox]
            page_w, page_h = box[2] - box[0], box[3] - box[1]
            if page_w <= 0 or page_h <= 0:
                continue
            mirrored = job.mirror_margins and (index + 1) % 2 == 0
            ml, mr = (right, left) if mirrored else (left, right)
            if job.margin_mode.startswith("hole"):
                # center the ink between the punch line and the far edge
                if index >= len(inks):
                    continue
                ink = inks[index]
                hole = HOLE_GUIDE_MM * MM_TO_PT
                # keep out of the printer's unprintable border on the
                # far edge (the punch line is well inside it anyway)
                hw_l, _, hw_r, _ = job.hw_margins
                if mirrored:
                    zone_x0, zone_x1 = box[0] + hw_l, box[2] - hole
                else:
                    zone_x0, zone_x1 = box[0] + hole, box[2] - hw_r
                ink_w = max(ink[2] - ink[0], 1.0)
                if job.margin_mode == "hole-clip":
                    # original size no matter what; edges may clip
                    scale = max(0.01, user_scale)
                else:
                    scale = max(
                        0.01, min(user_scale, (zone_x1 - zone_x0) / ink_w)
                    )
                ink_cx = (ink[0] + ink[2]) / 2
                ink_cy = (ink[1] + ink[3]) / 2
                tx = (zone_x0 + zone_x1) / 2 - scale * ink_cx
                ty = ink_cy * (1 - scale)  # keep the vertical center put
                page.contents_add(
                    pikepdf.Stream(
                        pdf,
                        f"q {scale:.5f} 0 0 {scale:.5f} "
                        f"{tx:.3f} {ty:.3f} cm\n".encode(),
                    ),
                    prepend=True,
                )
                page.contents_add(pikepdf.Stream(pdf, b"\nQ"), prepend=False)
                continue
            if job.margin_mode == "shift":
                # keep original size: translate only, may clip opposite edge
                scale = max(0.01, user_scale)
                tx = box[0] + (page_w - page_w * scale) / 2 + (ml - mr)
                ty = box[1] + (page_h - page_h * scale) / 2 + (bottom - top)
            else:
                fit = min(
                    (page_w - ml - mr) / page_w,
                    (page_h - top - bottom) / page_h,
                )
                scale = max(0.01, fit * user_scale)
                # center the scaled content inside the margin box
                tx = box[0] + ml + (page_w - page_w * scale - ml - mr) / 2
                ty = box[1] + bottom + (page_h - page_h * scale - top - bottom) / 2
            tx -= scale * box[0]
            ty -= scale * box[1]
            page.contents_add(
                pikepdf.Stream(
                    pdf,
                    f"q {scale:.5f} 0 0 {scale:.5f} {tx:.3f} {ty:.3f} cm\n".encode(),
                ),
                prepend=True,
            )
            page.contents_add(pikepdf.Stream(pdf, b"\nQ"), prepend=False)
        pdf.save(dest)


def apply_margins(src: str, dest: str, job: PrintJob) -> None:
    """Apply margins/manual scale by rewriting the PDF.

    Prefers pikepdf (exact, keeps page sizes); falls back to a
    Ghostscript BeginPage transform when pikepdf is unavailable. The
    same file is used for printing and previewing, so both always match.
    """
    if not needs_gs_pass(job):
        raise PrintError("No margins or manual scale set")
    try:
        _apply_margins_pikepdf(src, dest, job)
        return
    except ImportError:
        pass
    except Exception:
        # broken input pikepdf cannot parse: try a repaired copy
        try:
            _apply_margins_pikepdf(_repaired_copy(src), dest, job)
            return
        except Exception:
            pass
    if job.margin_mode.startswith("hole"):
        raise PrintError("Punch-zone centering requires python-pikepdf")
    if shutil.which("gs") is None:
        raise PrintError(
            "python-pikepdf or Ghostscript (gs) is required for margins/scaling"
        )
    _run_gs(src, dest, _margin_ps(job))


def preview_job_options(job: PrintJob) -> str:
    return " ".join(layout_options(job))


def _run_pdftopdf(src: str, options: str, dest: str) -> str | None:
    """Run the filter once; returns None on success, else the error text."""
    global _pdftopdf_args
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
            return None
        last_error = result.stderr.decode(errors="replace").strip()
    return last_error or "pdftopdf failed"


_repair_dir: tempfile.TemporaryDirectory | None = None
_repair_cache: dict[tuple[str, float], str] = {}


def _repaired_copy(src: str) -> str:
    """Rewrite a PDF with Ghostscript, fixing structural defects.

    Cached per (path, mtime) so repeated preview updates on the same
    document pay the repair cost once.
    """
    global _repair_dir
    key = (src, os.path.getmtime(src))
    cached = _repair_cache.get(key)
    if cached and os.path.isfile(cached):
        return cached
    if _repair_dir is None:
        _repair_dir = tempfile.TemporaryDirectory(prefix="pdfprinter-repair-")
    dest = os.path.join(_repair_dir.name, f"{abs(hash(key))}.pdf")
    tmp = f"{dest}.{os.getpid()}.tmp"
    _run_gs(src, tmp, "")
    os.replace(tmp, dest)
    _repair_cache[key] = dest
    return dest


def transform_for_preview(src: str, options: str, dest: str) -> None:
    """Run CUPS's pdftopdf filter to produce the as-printed document."""
    if not pdftopdf_available():
        raise PrintError(f"{PDFTOPDF} not available")
    error = _run_pdftopdf(src, options, dest)
    if error is None:
        return
    # real-world PDFs with structural defects (e.g. annotation appearance
    # streams missing /BBox) make pdftopdf fail outright; a Ghostscript
    # rewrite repairs them, so retry on a repaired copy
    if shutil.which("gs") is not None:
        try:
            repaired = _repaired_copy(src)
        except PrintError:
            repaired = None
        if repaired is not None:
            retry_error = _run_pdftopdf(repaired, options, dest)
            if retry_error is None:
                return
            error = retry_error
    raise PrintError(error.splitlines()[-1] if error else "pdftopdf failed")


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


def job_state(job_id: str) -> str:
    """Where a submitted job is now: 'queued', 'completed' or 'gone'.

    'gone' means CUPS no longer lists it as queued or completed —
    typically canceled or aborted.
    """
    try:
        queued = subprocess.run(
            ["lpstat", "-o"], capture_output=True, text=True, timeout=10
        )
        if job_id in queued.stdout:
            return "queued"
        completed = subprocess.run(
            ["lpstat", "-W", "completed", "-o"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if job_id in completed.stdout:
            return "completed"
    except (OSError, subprocess.TimeoutExpired):
        return "queued"  # cannot tell; keep watching
    return "gone"


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
            # sanitize first: structurally defective PDFs make printer
            # filter chains fail on the device (e.g. Canon error #853);
            # the rewrite is appearance-neutral and cached per document
            if shutil.which("gs") is not None:
                try:
                    work = _repaired_copy(work)
                except PrintError:
                    pass  # print the original rather than not at all
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
            # final guard: CUPS runs pdftopdf server-side on every job;
            # if it would fail on our file, the printer receives broken
            # data and errors out (e.g. Canon #853) — check locally and
            # repair the outgoing file if needed
            if pdftopdf_available():
                probe = os.path.join(tmpdir, "probe.pdf")
                if _run_pdftopdf(work, "", probe) is not None:
                    fixed = os.path.join(tmpdir, "sanitized.pdf")
                    _run_gs(work, fixed, "")
                    if _run_pdftopdf(fixed, "", probe) is None:
                        work = fixed
            cmd += ["--", work]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Failed to run lp: {exc}") from exc
    if out.returncode != 0:
        raise PrintError(out.stderr.strip() or "lp failed")

    # "request id is Printer-123 (1 file(s))"
    match = re.search(r"request id is (\S+)", out.stdout)
    return match.group(1) if match else out.stdout.strip()
