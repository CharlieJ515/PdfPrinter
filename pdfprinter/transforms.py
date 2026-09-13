"""The document transform pipeline: preview and print jobs.

Everything that rewrites a PDF on its way to the printer lives here:
CUPS's pdftopdf layout filter, the Ghostscript repair/sanitize passes,
pikepdf margin/placement transforms, and the ink measurement used by
punch-zone placement. Preview and print share these code paths so the
preview can never diverge from the submitted job.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from .models import (
    HOLE_GUIDE_MM,
    MM_TO_PT,
    PrintError,
    PrintJob,
    _expand_open_ranges,
    validate_page_range,
)


PDFTOPDF = "/usr/lib/cups/filter/pdftopdf"


_pdftopdf_args: list[str] | None = None


def _file_key(path: str) -> tuple[str, float]:
    """Cache key that invalidates when the file changes on disk."""
    return (path, os.path.getmtime(path))


def pdftopdf_available() -> bool:
    return os.access(PDFTOPDF, os.X_OK)


def layout_options(job: PrintJob) -> list[str]:
    """The job options that change the page layout, as option=value strings.

    Used both to build the lp command and to run the pdftopdf preview,
    so the preview and the printed job can never diverge. Physical
    options (quality, tray, media type, color, duplex) don't belong here.
    """
    parts: list[str] = []
    if job.page_range and validate_page_range(job.page_range):
        parts.append(f"page-ranges={_expand_open_ranges(job.page_range, '9999')}")
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


_content_bbox_cache: dict[tuple[str, float], list | None] = {}


def content_ink_boxes(src: str) -> list[tuple[float, float, float, float]]:
    """Ink boxes of what actually renders, ignoring page furniture.

    Unlike :func:`ink_boxes` (gs bbox device, which counts every marking
    operation including invisible ones), this rasterizes each page the
    way the print pipeline will and measures the visible ink, dropping
    decorations that commonly inflate the box: marks at the page edge,
    thin border rules that outrun the content, tall narrow rotated
    margin watermarks, faint gray furniture, and stray specks. Saturated
    color counts as ink even when light, so pale figures survive.

    Falls back to :func:`ink_boxes` when numpy or rasterization is
    unavailable, and per page to the raw visible extents whenever the
    filtered box looks implausible.
    """
    key = _file_key(src)
    cached = _content_bbox_cache.get(key)
    if cached is not None:
        return cached
    try:
        import numpy as np
    except ImportError:
        return ink_boxes(src)
    dpi = 100
    try:
        with tempfile.TemporaryDirectory(prefix="pdfprinter-cb-") as tmpdir:
            result = subprocess.run(
                ["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER",
                 "-sDEVICE=ppmraw", f"-r{dpi}",
                 "-o", os.path.join(tmpdir, "p%04d.ppm"), "-f", src],
                capture_output=True, timeout=300,
            )
            if result.returncode != 0:
                return ink_boxes(src)
            boxes = []
            index = 1
            while True:
                path = os.path.join(tmpdir, f"p{index:04d}.ppm")
                if not os.path.exists(path):
                    break
                boxes.append(_content_box_of_page(np, path, dpi))
                index += 1
    except (OSError, subprocess.TimeoutExpired):
        return ink_boxes(src)
    if not boxes:
        return ink_boxes(src)
    _content_bbox_cache[key] = boxes
    return boxes


def _read_ppm(np, path):
    with open(path, "rb") as fh:
        if fh.readline().strip() != b"P6":
            return None
        line = fh.readline()
        while line.startswith(b"#"):
            line = fh.readline()
        w, h = map(int, line.split())
        fh.readline()  # maxval
        return np.frombuffer(fh.read(), dtype=np.uint8).reshape(h, w, 3)


def _longest_runs(np, mask, axis):
    m = mask if axis == 0 else mask.T
    run = np.zeros(m.shape[1], dtype=np.int32)
    best = np.zeros(m.shape[1], dtype=np.int32)
    for row in m:
        run = np.where(row, run + 1, 0)
        best = np.maximum(best, run)
    return best


def _bands(active):
    i, n, out = 0, len(active), []
    while i < n:
        if active[i]:
            j = i
            while j + 1 < n and active[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _zero_bands(out, flags, limit, axis):
    for a, b in _bands(flags):
        if b - a + 1 <= limit:
            if axis == 0:
                out[:, a:b + 1] = False
            else:
                out[a:b + 1, :] = False


def _content_box_of_page(np, path, dpi):
    rgb = _read_ppm(np, path)
    scale = 72.0 / dpi
    if rgb is None:
        return (0.0, 0.0, 1.0, 1.0)
    h, w = rgb.shape[:2]
    page = (0.0, 0.0, w * scale, h * scale)
    lum = rgb.astype(np.int16).mean(axis=2)
    chroma = (
        rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
    )
    ink = (lum < 200) | (chroma > 40)  # dark, or saturated even if light
    if not ink.any():
        return page

    def extents(mask):
        ys, xs = np.nonzero(mask)
        return (
            xs.min() * scale, (h - 1 - ys.max()) * scale,
            (xs.max() + 1) * scale, (h - ys.min()) * scale,
        )

    raw = extents(ink)
    out = ink.copy()
    edge = max(2, dpi // 50)
    out[:edge, :] = out[-edge:, :] = False
    out[:, :edge] = out[:, -edge:] = False
    _zero_bands(out, _longest_runs(np, out, 0) > 0.22 * h, 8, 0)
    _zero_bands(out, _longest_runs(np, out, 1) > 0.22 * w, 8, 1)
    for a, b in _bands(out.any(axis=0)):
        band_rows = np.flatnonzero(out[:, a:b + 1].any(axis=1))
        if (
            b - a + 1 <= 14
            and len(band_rows)
            and band_rows[-1] - band_rows[0] >= 0.6 * h
        ):
            out[:, a:b + 1] = False
    for axis in (0, 1):
        active = out.any(axis=1 - axis)
        for a, b in _bands(active):
            chunk = out[:, a:b + 1] if axis == 0 else out[a:b + 1, :]
            if chunk.sum() < 15:
                chunk[:] = False
    if not out.any():
        return raw
    box = extents(out)
    # sanity: a filtered box far smaller than the visible ink means the
    # heuristics ate real content — trust the raw extents instead
    if (box[2] - box[0]) < 0.5 * (raw[2] - raw[0]):
        return raw
    return box


def needs_gs_pass(job: PrintJob) -> bool:
    return (
        margins_active(job)
        or job.scale_percent != 100
        or job.margin_mode.startswith("hole")
    )


_bbox_cache: dict[tuple[str, float], list[tuple[float, float, float, float]]] = {}


def ink_boxes(src: str) -> list[tuple[float, float, float, float]]:
    """Per-page ink bounding boxes (x0, y0, x1, y1) via gs's bbox device."""
    key = _file_key(src)
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

    if job.margin_mode == "hole-content":
        inks = content_ink_boxes(src)
    elif job.margin_mode.startswith("hole"):
        inks = ink_boxes(src)
    else:
        inks = []

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
    """:func:`layout_options` as the single pdftopdf argument string.

    The only place the options are joined; everything that needs the
    string form goes through here.
    """
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
    key = _file_key(src)
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
    cmd = [
        "qpdf", src, "--pages", ".",
        _expand_open_ranges(page_range, "z"), "--", dest,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Failed to run qpdf: {exc}") from exc
    # qpdf exits 3 for warnings while still producing output
    if out.returncode not in (0, 3):
        raise PrintError(out.stderr.strip() or "qpdf failed")


_normalized_cache: dict[tuple[str, float], str] = {}
_normalized_dir: tempfile.TemporaryDirectory | None = None


def normalize_page_boxes(src: str) -> str:
    """Make the printable page equal the visible page.

    Some journal PDFs are untrimmed press sheets: a large MediaBox with
    a CropBox marking the actual page. Viewers render the CropBox, but
    the transform pipeline and CUPS act on the MediaBox — the printer
    then auto-scales the oversized sheet and the output no longer
    matches the preview. When any page's boxes differ, this rewrites
    the file with MediaBox = CropBox (content untouched); otherwise the
    original path is returned. Cached per file mtime.
    """
    global _normalized_dir
    try:
        key = _file_key(src)
    except OSError:
        return src
    cached = _normalized_cache.get(key)
    if cached is not None:
        return cached if cached else src
    result = _normalize_with_pikepdf(src)
    if result is None:  # pikepdf unavailable: gs honours the CropBox
        result = _normalize_with_gs(src)
    _normalized_cache[key] = result or ""
    return result or src


def _normalize_dest(src: str) -> str:
    global _normalized_dir
    if _normalized_dir is None:
        _normalized_dir = tempfile.TemporaryDirectory(
            prefix="pdfprinter-boxes-"
        )
    return os.path.join(
        _normalized_dir.name, f"{abs(hash(_file_key(src)))}.pdf"
    )


def _normalize_with_pikepdf(src: str) -> str | None:
    try:
        import pikepdf
    except ImportError:
        return None
    try:
        with pikepdf.open(src) as pdf:
            differs = False
            for page in pdf.pages:
                media = [round(float(v), 2) for v in page.mediabox]
                crop = [
                    round(float(v), 2)
                    for v in page.get("/CropBox", page.mediabox)
                ]
                if media != crop:
                    differs = True
                    break
            if not differs:
                return src
            for page in pdf.pages:
                crop = page.get("/CropBox")
                if crop is None:
                    continue
                # clip to the crop: press-sheet junk outside it (slug
                # lines, crop marks) must never paint into the strips a
                # later impose/margin step exposes — viewers don't show it
                x0, y0, x1, y1 = (float(v) for v in crop)
                page.contents_add(
                    pikepdf.Stream(
                        pdf,
                        f"q {x0:.3f} {y0:.3f} {x1 - x0:.3f} "
                        f"{y1 - y0:.3f} re W n\n".encode(),
                    ),
                    prepend=True,
                )
                page.contents_add(pikepdf.Stream(pdf, b"\nQ"), prepend=False)
                page.mediabox = crop
                for box in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
                    if box in page:
                        del page[box]
            dest = _normalize_dest(src)
            pdf.save(dest)
            return dest
    except Exception:  # noqa: BLE001 — treat unreadable input as-is
        return src


def _normalize_with_gs(src: str) -> str | None:
    if shutil.which("gs") is None:
        return src
    dest = _normalize_dest(src)
    try:
        out = subprocess.run(
            ["gs", "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-dUseCropBox",
             "-sDEVICE=pdfwrite", "-o", dest, "-f", src],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return src
    return dest if out.returncode == 0 and os.path.exists(dest) else src


_MEDIA_PTS = {
    "a3": (841.89, 1190.55),
    "a4": (595.28, 841.89),
    "a5": (419.53, 595.28),
    "a6": (297.64, 419.53),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "executive": (522.0, 756.0),
    "tabloid": (792.0, 1224.0),
    "ledger": (1224.0, 792.0),
    "statement": (396.0, 612.0),
    "folio": (612.0, 936.0),
    "b5": (498.9, 708.66),
    "b4": (708.66, 1000.63),
}

_media_default_cache: dict[str, tuple[float, float] | None] = {}


def media_size_pt(name: str) -> tuple[float, float] | None:
    """Width/height in points for a PPD or IPP media name, else None.

    Handles plain PPD names ("A4", "Letter", "A4.Borderless"),
    self-describing IPP names ("iso_a4_210x297mm", "na_letter_8.5x11in"),
    and PPD custom sizes ("Custom.WIDTHxHEIGHT", points unless suffixed).
    """
    if not name:
        return None
    n = name.strip().lower()
    if n.startswith("custom."):
        m = re.fullmatch(
            r"custom\.(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)?", n
        )
        if not m:
            return None
        w, h = float(m.group(1)), float(m.group(2))
        unit = {"mm": MM_TO_PT, "in": 72.0}.get(m.group(3) or "", 1.0)
        return (w * unit, h * unit)
    m = re.search(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)$", n)
    if m:
        unit = MM_TO_PT if m.group(3) == "mm" else 72.0
        return (float(m.group(1)) * unit, float(m.group(2)) * unit)
    base = n.split(".", 1)[0]
    for token in (base, *base.split("_")):
        if token in _MEDIA_PTS:
            return _MEDIA_PTS[token]
    return None


def resolve_media_size(job: PrintJob) -> tuple[float, float] | None:
    """The paper size the job will land on, in points.

    The job's own media choice if set, else the printer driver's
    default page size (cached per printer). None when neither can be
    resolved — the pipeline then leaves page sizes alone, as before.
    """
    if job.media:
        size = media_size_pt(job.media)
        if size is not None:
            return size
    if not job.printer:
        return None
    if job.printer not in _media_default_cache:
        size = None
        try:
            from . import cups

            options = cups.printer_options(job.printer)
            option = cups.find_option(options, "pagesize", "media")
            if option is not None and option.default:
                size = media_size_pt(option.default)
        except Exception:  # noqa: BLE001 — unreachable printer: no impose
            size = None
        _media_default_cache[job.printer] = size
    return _media_default_cache[job.printer]


def _impose_scale(scaling: str, fit_w: float, fit_h: float) -> float:
    fit = min(fit_w, fit_h)
    if scaling == "none":
        return 1.0
    if scaling == "fit":
        return fit
    # "auto" / printer default: shrink oversized pages, never enlarge
    return min(1.0, fit)


_paperfit_cache: dict[tuple, bool] = {}


def needs_paper_fit(src: str, job: PrintJob) -> bool:
    """Whether the document itself changes on its way to the paper.

    True when crop-box normalization would rewrite the file or when
    page sizes differ from the paper the job resolves to — the preview
    must then run the pipeline even with every option at its default,
    or it would show the untransformed document while the print is
    imposed. Cached per file and paper size.
    """
    try:
        if normalize_page_boxes(src) != src:
            return True
        size = resolve_media_size(job)
        if size is None:
            return False
        key = (_file_key(src), size)
        if key not in _paperfit_cache:
            _paperfit_cache[key] = _pages_off_media(src, size)
        return _paperfit_cache[key]
    except OSError:
        return False


def _pages_off_media(path: str, size: tuple[float, float]) -> bool:
    try:
        import pikepdf
    except ImportError:
        return False
    media_w, media_h = size
    try:
        with pikepdf.open(path) as pdf:
            for page in pdf.pages:
                box = [float(v) for v in page.mediabox]
                rotate = int(page.get("/Rotate", 0)) % 360
                canvas_w, canvas_h = (
                    (media_h, media_w) if rotate in (90, 270)
                    else (media_w, media_h)
                )
                if (
                    abs(box[2] - box[0] - canvas_w) > 1.0
                    or abs(box[3] - box[1] - canvas_h) > 1.0
                    or abs(box[0]) > 0.5
                    or abs(box[1]) > 0.5
                ):
                    return True
    except Exception:  # noqa: BLE001 — unreadable input: nothing to fit
        return False
    return False


def impose_media(
    src: str, dest: str, size: tuple[float, float], scaling: str
) -> bool:
    """Place every page onto a paper-sized canvas, once, locally.

    This is the fitting CUPS would otherwise do server-side (invisible
    to the preview): pages are scaled per the job's scaling mode,
    auto-rotated when their orientation mismatches the paper, and
    centered. Afterwards page size == paper size exactly, so the
    server's own scaling pass has nothing left to do — the submit adds
    print-scaling=none to pin that down. Returns False without writing
    when every page already matches the paper.
    """
    try:
        import pikepdf
    except ImportError:
        return False
    media_w, media_h = size

    def canvas_for(page) -> tuple[float, float]:
        # a pre-rotated page displays with swapped dimensions; give it
        # a swapped canvas so the displayed result is paper-sized
        rotate = int(page.get("/Rotate", 0)) % 360
        if rotate in (90, 270):
            return (media_h, media_w)
        return (media_w, media_h)

    if not _pages_off_media(src, size):
        return False
    with pikepdf.open(src) as pdf:
        for page in pdf.pages:
            box = [float(v) for v in page.mediabox]
            page_w, page_h = box[2] - box[0], box[3] - box[1]
            if page_w <= 0 or page_h <= 0:
                continue
            canvas_w, canvas_h = canvas_for(page)
            rotate = int(page.get("/Rotate", 0)) % 360
            if rotate == 0 and (page_w > page_h) != (canvas_w > canvas_h):
                # rotate the content 90° to match the paper orientation
                s = _impose_scale(scaling, canvas_w / page_h, canvas_h / page_w)
                tx = canvas_w / 2 + s * (box[1] + box[3]) / 2
                ty = canvas_h / 2 - s * (box[0] + box[2]) / 2
                matrix = f"q 0 {s:.5f} -{s:.5f} 0 {tx:.3f} {ty:.3f} cm\n"
            else:
                s = _impose_scale(scaling, canvas_w / page_w, canvas_h / page_h)
                tx = (canvas_w - page_w * s) / 2 - s * box[0]
                ty = (canvas_h - page_h * s) / 2 - s * box[1]
                matrix = f"q {s:.5f} 0 0 {s:.5f} {tx:.3f} {ty:.3f} cm\n"
            page.contents_add(
                pikepdf.Stream(pdf, matrix.encode()), prepend=True
            )
            page.contents_add(pikepdf.Stream(pdf, b"\nQ"), prepend=False)
            page.mediabox = pikepdf.Array([0, 0, canvas_w, canvas_h])
            for key in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
                if key in page:
                    del page[key]
        pdf.save(dest)
    return True


def build_output(
    src: str, job: PrintJob, workdir: str, *, qpdf_fallback: bool = True
) -> tuple[str, bool]:
    """Produce the transformed document for `job` in `workdir`.

    Returns (path, layout_applied): path is `src` itself when nothing
    applies; layout_applied says whether layout options were applied
    locally (False means the caller must pass them to CUPS).

    Layout (pdftopdf) runs first and margins/scale/placement are applied
    to its result, so mirrored margins follow the final output order --
    e.g. with pages 2-5, output page 1 (source page 2) gets the left
    margin. Preview and print share this function so they can never
    diverge; the print path wraps it in its own sanitize/guard passes.
    """
    work = normalize_page_boxes(src)
    layout_applied = False
    options = layout_options(job)
    if options:
        layout_path = os.path.join(workdir, "preview.pdf")
        if pdftopdf_available():
            transform_for_preview(work, preview_job_options(job), layout_path)
            work = layout_path
            layout_applied = True
        elif (
            qpdf_fallback  # preview-only: print sends the range to CUPS
            and job.page_range
            and validate_page_range(job.page_range)
            and shutil.which("qpdf")
        ):
            # no pdftopdf: at least apply the page selection locally
            make_page_subset(work, job.page_range, layout_path)
            work = layout_path
    size = resolve_media_size(job)
    if size is not None:
        imposed_path = os.path.join(workdir, "imposed.pdf")
        if impose_media(work, imposed_path, size, job.scaling):
            work = imposed_path
    if needs_gs_pass(job):
        margined_path = os.path.join(workdir, "margined.pdf")
        apply_margins(work, margined_path, job)
        work = margined_path
    return work, layout_applied


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
            # lp spools a copy, so the temp files can go after.
            work = path
            # sanitize first: structurally defective PDFs make printer
            # filter chains fail on the device (e.g. Canon error #853);
            # the rewrite is appearance-neutral and cached per document
            if shutil.which("gs") is not None:
                try:
                    work = _repaired_copy(work)
                except PrintError:
                    pass  # print the original rather than not at all
            # the shared pipeline: layout, then margins (see build_output)
            work, layout_applied = build_output(work, job, tmpdir, qpdf_fallback=False)
            if layout_applied:
                if job.media:  # still selects the paper/tray
                    cmd += ["-o", f"media={job.media}"]
            else:
                # no local pdftopdf: fall back to server-side options
                for option in layout_options(job):
                    cmd += ["-o", option]
            if (
                (layout_applied or not layout_options(job))
                and resolve_media_size(job) is not None
            ):
                # pages were imposed to the paper size locally; forbid
                # the server from rescaling behind the preview's back
                cmd += ["-o", "print-scaling=none"]
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

