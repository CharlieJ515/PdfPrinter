"""Printing backend facade.

The implementation is split by concern (models / cups / transforms);
this module re-exports the public surface so callers keep one import.
"""

from .models import (  # noqa: F401
    HOLE_GUIDE_MM,
    MM_TO_PT,
    PPDOption,
    PrintError,
    PrintJob,
    load_last_job,
    save_last_job,
    validate_page_range,
)
from .cups import (  # noqa: F401
    _parse_hw_margins,
    default_printer,
    find_option,
    job_state,
    list_printers,
    printer_hw_margins,
    printer_options,
    supports_duplex,
)
from .transforms import (  # noqa: F401
    apply_margins,
    content_ink_boxes,
    ink_boxes,
    layout_options,
    make_page_subset,
    margins_active,
    needs_gs_pass,
    pdftopdf_available,
    preview_job_options,
    print_file,
    transform_for_preview,
)
