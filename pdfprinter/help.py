"""User guide content shown by Help → User Guide.

The markup carries its own inline CSS because Qt's rich text engine has
no stylesheet cascade: the families are the ones :mod:`theme` registers
application-wide (Lora for body copy, Cormorant Garamond for display
type), the colours are the palette's. Letterspacing on the small-caps
section labels is applied after parsing by
``dialogs._letterspace_section_labels`` — Qt's CSS subset has no
``letter-spacing`` property.
"""

# palette echoes pdfprinter.theme; kept literal so the HTML stays one
# self-contained string that can be rendered by anything.
_INK = "#2d2b2b"
_MUTED = "#6d6a6a"
_FAINT = "#928e8e"
_HAIRLINE = "#dedbdb"
_MARGIN_GUIDE = "#c2352b"
_PUNCH_GUIDE = "#2f4bab"
_UNPRINTABLE = "#c0bdbd"
_CODE_BG = "#ebe8e8"

_BODY = "'Lora', 'DejaVu Serif', serif"
_DISPLAY = "'Cormorant Garamond', 'Lora', serif"

#: One flat hairline. Qt draws ``<hr>`` as a sunken frame, so the rule is
#: a one-cell table with a background instead.
_RULE = (
    '<table width="100%" cellspacing="0" cellpadding="0" border="0" '
    'style="margin-top:20px; margin-bottom:4px;"><tr>'
    f'<td style="background-color:{_HAIRLINE}; font-size:1pt; '
    'line-height:1pt;"> </td></tr></table>'
)


def _section(title: str) -> str:
    """A small-caps section label under a hairline."""
    return (
        f"{_RULE}"
        f'<p style="font-family:{_DISPLAY}; font-size:10pt; color:{_MUTED}; '
        'margin-top:14px; margin-bottom:10px;">'
        f"{title.upper()}</p>"
    )


def _swatch(color: str, dashed: bool = True) -> str:
    """The coloured legend mark: three dashes, or a flat grey strip."""
    if dashed:
        return (
            f'<span style="color:{color}; font-size:13pt; '
            "font-weight:700;\"><b>&#8211;&nbsp;&#8211;&nbsp;&#8211;</b></span>"
        )
    return (
        f'<span style="background-color:{color}; color:{color}; '
        'font-size:9pt;">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>'
    )


def _legend(*rows: tuple[str, str]) -> str:
    table = [
        '<table cellspacing="0" cellpadding="0" border="0" '
        'style="margin-top:6px; margin-bottom:8px;">'
    ]
    for mark, text in rows:
        table.append(
            '<tr><td width="66" style="padding:4px 10px 4px 0px;">'
            f"{mark}</td>"
            f'<td style="padding:4px 0px;">{text}</td></tr>'
        )
    table.append("</table>")
    return "".join(table)


_P = (
    f'<p style="font-family:{_BODY}; font-size:10pt; color:{_INK}; '
    'text-align:justify; margin-top:0px; margin-bottom:10px; '
    'line-height:148%;">'
)
_LI = (
    f'<li style="font-family:{_BODY}; font-size:10pt; color:{_INK}; '
    'margin-bottom:7px;">'
)
_UL = '<ul style="margin-left:16px; margin-top:2px; -qt-list-indent:1;">'
_CODE = (
    f'<span style="font-family:monospace; font-size:9pt; '
    f'background-color:{_CODE_BG};">'
)

HELP_HTML = f"""
<body style="font-family:{_BODY}; font-size:10pt; color:{_INK};">

<p style="font-family:{_DISPLAY}; font-size:22pt; color:{_INK};
   margin-top:18px; margin-bottom:14px;">PDF Printer</p>

{_P}Prints PDFs via CUPS with a preview that shows <b>exactly what will
come out of the printer</b>: the preview is produced by the same CUPS
filter ({_CODE}pdftopdf</span>) and the same transforms that build the
print job, so page selection, layout and margins can be checked on
screen instead of on paper.</p>

{_section("Opening documents")}
{_UL}
{_LI}<b>Open PDF…</b> (Ctrl+O) — regular file dialog; starts in the
current document's folder.</li>
{_LI}<b>Open from Zotero…</b> (Ctrl+Shift+O) — browses your Zotero
library like Zotero itself: collections tree, title/creator/added
columns, search across title, authors and filename. Only PDFs present on
this machine are listed. The picker remembers where you left off.</li>
{_LI}Dragging a PDF onto the window also works.</li>
</ul>

{_section("Preview guides")}
{_legend(
    (_swatch(_MARGIN_GUIDE), "Red dashed — your margins"),
    (_swatch(_PUNCH_GUIDE), "Blue dashed — 18&nbsp;mm punch-hole guide"),
    (_swatch(_UNPRINTABLE, dashed=False),
     "Grey strips — the printer's unprintable border"),
)}
<p style="font-family:{_BODY}; font-size:10pt; color:{_MUTED};
   margin-top:2px; margin-bottom:12px;"><i>All three are screen-only and
never printed — nothing prints inside the grey strips either.</i></p>
{_UL}
{_LI}Zoom: buttons, Ctrl+scroll, touchpad pinch, Ctrl+&#43;/&#8722;/0.
Zoom anchors on the cursor or pinch centre.</li>
{_LI}Scrolling has touchpad inertia; mouse wheel steps are animated.</li>
{_LI}Changing any layout option re-renders the preview in the background
(the status bar shows <i>Rendering preview…</i>).</li>
</ul>

{_section("Print options")}
{_UL}
{_LI}<b>Pages</b> — ranges like {_CODE}1-4,7</span> ({_CODE}4-</span> prints to the end); the preview shows
only the selected pages.</li>
{_LI}<b>Duplex</b> — greyed out on printers without a duplex unit; use
<i>Page set</i> odd/even to print double-sided manually (print odd pages,
re-feed the stack, print even pages).</li>
{_LI}Choices marked <b>(default)</b> are what the printer's driver
reports as its default; selecting them sends nothing and lets the printer
decide. Options a printer does not support are greyed out.
<b>Quality</b>, <b>Paper source</b> and <b>Media type</b> list the
selected printer's actual choices.</li>
{_LI}<b>Grayscale</b> shows the preview in grey too (also when the
printer's own default is grayscale).</li>
{_LI}<b>More options</b> holds the less-used settings (orientation, pages
per sheet, scaling, collate, reverse order…). When collapsed, the button
shows how many of them are set, e.g. <i>(2 set)</i>.</li>
{_LI}<b>Scaling</b> — Automatic/Fit/Fill/No scaling are CUPS modes;
<b>Custom</b> enables the percentage spinner.</li>
</ul>

{_section("Margins &amp; placement")}
{_P}Margins are applied by rewriting the PDF (CUPS ignores margin
options), and the print job is exactly the previewed file.
<b>Placement</b> selects how content is placed:</p>
{_UL}
{_LI}<b>Shrink to fit margins</b> — content is scaled into the margin
box; nothing is ever cut off.</li>
{_LI}<b>Shift by margins</b> — content keeps its size and moves;
whatever crosses the opposite edge is cut off.</li>
{_LI}<b>Center right of punch line</b> — measures each page's actual ink
and centres it, at original size, between the 18&nbsp;mm punch line and
the far edge; shrinks only if the ink is wider than that zone. Margin
values are ignored.</li>
{_LI}<b>Center on punch line, no shrink</b> — same centring but never
shrinks; both edges may clip.</li>
</ul>

{_section("Binder recipe")}
{_P}Placement = <i>Center right of punch line</i>, <i>Mirror margins</i>,
duplex long edge (or odd/even passes). Even pages take the binding
clearance on the other side, following the final printed page order.</p>
{_UL}
{_LI}<b>Mirror margins (binding)</b> — for double-sided printing into a
ring binder: even pages get the binding clearance on the right, so the
bound edge is clear on both sides of each sheet. Page parity follows the
final printed order (after page selection, n-up, …).</li>
{_LI}<b>Punch hole guide</b> — shows a blue dashed line 18&nbsp;mm from
the binding edge as a hole-punching reference. Preview only, never
printed.</li>
</ul>

{_section("Printing")}
{_UL}
{_LI}The Print button reports each stage: <i>Preparing job…</i> →
<i>✓ Submitted</i>, then the job is tracked in the CUPS queue until it
completes (status bar).</li>
{_LI}<b>Use last print's settings</b> restores the full configuration of
the last successful print; <b>Reset</b> returns everything to defaults
(keeping the printer).</li>
{_LI}Structurally broken PDFs (common with publisher downloads) are
repaired automatically before printing, so they don't crash the printer's
filter chain.</li>
</ul>

<p style="font-family:{_BODY}; font-size:9pt; color:{_FAINT};
   margin-top:16px;">&nbsp;</p>
</body>
"""
