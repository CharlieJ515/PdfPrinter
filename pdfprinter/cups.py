"""CUPS queue and driver queries (read-only introspection)."""

from __future__ import annotations

import re
import subprocess

from .models import PPDOption, PrintError


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

