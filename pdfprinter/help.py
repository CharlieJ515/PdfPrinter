"""User guide content shown by Help → User Guide."""

HELP_HTML = """
<h2>PDF Printer</h2>
<p>Prints PDFs via CUPS with a preview that shows <b>exactly what will
come out of the printer</b>: the preview is produced by the same CUPS
filter (<code>pdftopdf</code>) and the same transforms that build the
print job, so page selection, layout and margins can be checked on
screen instead of on paper.</p>

<h3>Opening documents</h3>
<ul>
<li><b>Open PDF…</b> (Ctrl+O) — regular file dialog; starts in the
current document's folder.</li>
<li><b>Open from Zotero…</b> (Ctrl+Shift+O) — browses your Zotero
library like Zotero itself: collections tree, title/creator columns,
search across title, authors and filename. Only PDFs present on this
machine are listed. The picker remembers where you left off.</li>
<li>Dragging a PDF onto the window also works.</li>
</ul>

<h3>Preview</h3>
<ul>
<li>Zoom: buttons, Ctrl+scroll, touchpad pinch, Ctrl+&#43;/&#8722;/0.
Zoom anchors on the cursor or pinch center.</li>
<li>Scrolling has touchpad inertia; mouse wheel steps are animated.</li>
<li>Red dashed lines mark the margins; the blue dashed line is the
punch-hole guide; gray strips at the page edges are the printer's own
unprintable border (nothing prints there). All screen-only — never
printed.</li>
<li>Changing any layout option re-renders the preview in the
background (status bar shows <i>Rendering preview…</i>).</li>
</ul>

<h3>Print options</h3>
<ul>
<li><b>Pages</b> — ranges like <code>1-4,7</code>; the preview shows
only the selected pages.</li>
<li><b>Duplex</b> — grayed out on printers without a duplex unit; use
<i>Page set</i> odd/even to print double-sided manually (print odd
pages, re-feed the stack, print even pages).</li>
<li>Choices marked <b>(default)</b> are what the printer's driver
reports as its default; selecting them sends nothing and lets the
printer decide. Options a printer does not support are grayed out.
<b>Quality</b>, <b>Paper source</b> and <b>Media type</b> list the
selected printer's actual choices.</li>
<li><b>Grayscale</b> shows the preview in gray too (also when the
printer's own default is grayscale).</li>
<li><b>More options</b> holds the less-used settings (orientation,
pages per sheet, scaling, collate, reverse order…). When collapsed,
the button shows how many of them are set, e.g. <i>(2 set)</i>.</li>
<li><b>Scaling</b> — Automatic/Fit/Fill/No scaling are CUPS modes;
<b>Custom</b> enables the percentage spinner.</li>
</ul>

<h3>Margins &amp; binder printing</h3>
<p>Margins are applied by rewriting the PDF (CUPS ignores margin
options), and the print job is exactly the previewed file.
<b>Placement</b> selects how content is placed:</p>
<ul>
<li><b>Shrink to fit margins</b> — content is scaled into the margin
box; nothing is ever cut off.</li>
<li><b>Shift by margins</b> — content keeps its size and moves;
whatever crosses the opposite edge is cut off.</li>
<li><b>Center right of punch line</b> — measures each page's actual
ink and centers it, at original size, between the 18&nbsp;mm punch
line and the far edge; shrinks only if the ink is wider than that
zone. Margin values are ignored.</li>
<li><b>Center on punch line, no shrink</b> — same centering but never
shrinks; both edges may clip.</li>
</ul>
<ul>
<li><b>Mirror margins (binding)</b> — for double-sided printing into a
ring binder: even pages get the binding clearance on the right, so the
bound edge is clear on both sides of each sheet. Page parity follows
the final printed order (after page selection, n-up, …).</li>
<li><b>Punch hole guide</b> — shows a blue dashed line 18&nbsp;mm from
the binding edge as a hole-punching reference. Preview only, never
printed.</li>
</ul>
<p>Typical binder recipe: <i>Placement = Center right of punch line</i>,
<i>Mirror margins</i>, duplex long edge (or odd/even passes).</p>

<h3>Printing</h3>
<ul>
<li>The Print button reports each stage: <i>Preparing job…</i> →
<i>✓ Submitted</i>, then the job is tracked in the CUPS queue until it
completes (status bar).</li>
<li><b>Use last print's settings</b> restores the full configuration
of the last successful print; <b>Reset</b> returns everything to
defaults (keeping the printer).</li>
<li>Structurally broken PDFs (common with publisher downloads) are
repaired automatically before printing, so they don't crash the
printer's filter chain.</li>
</ul>
"""
