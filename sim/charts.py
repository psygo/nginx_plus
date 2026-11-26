"""Charts for the reports and pull requests: SVG in a light and a dark version, rendered
to PNG (rsvg-convert) because GitHub shows images in descriptions, not inline SVG.

One visual spec everywhere: a validated categorical palette (slots 1-3 pass the
colour-vision checks pairwise in both modes), thin marks (2 px lines, bars of at most
20 px with a 4 px rounded data end and a square baseline, dots of radius 4.5 with a 2 px
ring in the surface colour), solid hairline grid, text in ink colours (never in a series
colour), a legend whenever there are two or more series. Every chart has a table twin in
the report or pull request text.

  render(chart_fn, "name", outdir, **kwargs) writes name-light.{svg,png} and
  name-dark.{svg,png}; chart_fn(theme, **kwargs) returns an Svg.
"""
import math
import os
import shutil
import subprocess
from xml.sax.saxutils import escape

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781",
                  grid="#e1e0d9", base="#c3c2b7", series=["#2a78d6", "#eb6834", "#1baf7a"],
                  shade=("#86b6ef", "#2a78d6")),      # one hue, two steps: before / after
    "dark": dict(surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781",
                 grid="#2c2c2a", base="#383835", series=["#3987e5", "#d95926", "#199e70"],
                 shade=("#184f95", "#3987e5")),
}
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"


class Svg:
    def __init__(self, w, h, theme):
        self.w, self.h, self.t, self.items = w, h, theme, []

    def add(self, s):
        self.items.append(s)

    def text(self, x, y, s, size=13, color=None, anchor="start", weight="normal"):
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color or self.t["ink"]}" '
                 f'text-anchor="{anchor}" font-weight="{weight}">{escape(str(s))}</text>')

    def line(self, x1, y1, x2, y2, color, width=1.0):
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" '
                 f'stroke-width="{width}" stroke-linecap="round"/>')

    def polyline(self, pts, color, width=2.0):
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        self.add(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')

    def dot(self, x, y, color, r=4.5, hollow=False):
        fill = self.t["surface"] if hollow else color
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 2:.1f}" fill="{self.t["surface"]}"/>')
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}" stroke="{color}" '
                 f'stroke-width="{2 if hollow else 0}"/>')

    def hbar(self, x0, x1, y, h, color, radius=4.0):
        """Horizontal bar from the baseline x0 to the data end x1: rounded at x1 only."""
        if abs(x1 - x0) < 0.5:
            return
        r = min(radius, abs(x1 - x0), h / 2)
        s = 1 if x1 > x0 else -1
        xe = x1 - s * r
        sweep = 1 if s > 0 else 0
        d = (f"M{x0:.1f},{y:.1f} L{xe:.1f},{y:.1f} A{r},{r} 0 0 {sweep} {x1:.1f},{y + r:.1f} "
             f"L{x1:.1f},{y + h - r:.1f} A{r},{r} 0 0 {sweep} {xe:.1f},{y + h:.1f} L{x0:.1f},{y + h:.1f} Z")
        self.add(f'<path d="{d}" fill="{color}"/>')

    def header(self, title, subtitle, left=24):
        self.text(left, 30, title, size=17, weight="bold")
        y = 52
        for line in subtitle if isinstance(subtitle, (list, tuple)) else [subtitle]:
            self.text(left, y, line, size=12.5, color=self.t["ink2"])
            y += 18
        return y

    def legend(self, x, y, entries, kind="dot"):
        """entries: (label, colour). Returns the y below the legend."""
        for label, color in entries:
            if kind == "line":
                self.line(x, y - 4, x + 16, y - 4, color, 2.5)
                tx = x + 22
            else:
                self.dot(x + 5, y - 4, color, r=4.5)
                tx = x + 16
            self.text(tx, y, label, size=12.5, color=self.t["ink2"])
            x = tx + 7.2 * len(label) + 22
        return y + 14

    def svg(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" font-family="{FONT}">'
                f'<rect width="{self.w}" height="{self.h}" rx="8" fill="{self.t["surface"]}"/>'
                + "".join(self.items) + "</svg>")


def nice_ticks(lo, hi, n=5):
    """Round tick values covering [lo, hi]."""
    span = hi - lo if hi > lo else 1.0
    raw = span / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=10 * mag)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    if ticks[-1] < hi - 1e-9:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def pct(v, signed=True):
    """Percent label with a real minus sign; values that round to zero print as 0 %."""
    if abs(v) < 0.5:
        return "0 %"
    s = f"{v:+.0f} %" if signed else f"{v:.0f} %"
    return s.replace("-", "−")


def pct_tick(v):
    """Axis tick: exact (+2.5 %, not a rounded +2 %)."""
    return f"{v:+g} %".replace("-", "−")


def _padded(vals, labelled):
    """Axis range with room for the labels: 8 % beyond the extremes on the side(s) where
    labelled values lie."""
    lo, hi = min(min(vals), 0), max(max(vals), 0)
    span = (hi - lo) or 1.0
    if labelled and min(labelled) < 0:
        lo -= 0.08 * span
    if labelled and max(labelled) > 0:
        hi += 0.08 * span
    return lo, hi


def render(fn, name, outdir, scale=2, **kw):
    os.makedirs(outdir, exist_ok=True)
    out = []
    for mode, theme in THEMES.items():
        svg = fn(theme, **kw).svg()
        base = os.path.join(outdir, f"{name}-{mode}")
        with open(base + ".svg", "w") as f:
            f.write(svg)
        if shutil.which("rsvg-convert"):
            subprocess.run(["rsvg-convert", "-z", str(scale), "-o", base + ".png", base + ".svg"],
                           check=True)
        out.append(base)
    return out


def picture(url_light, url_dark, alt):
    """Markdown for GitHub: the dark image for dark mode, the light one otherwise."""
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="{url_dark}">'
            f'<img alt="{escape(alt)}" src="{url_light}"></picture>')


# --------------------------------------------------------------------------
# Chart types
# --------------------------------------------------------------------------

def line_chart(t, title, subtitle, series, xlabel, ylabel, ref=None, w=760, h=380, ymax=None):
    """series: (name, [(x, y)], slot[, end label]). The end label defaults to the last
    value; None leaves only the end dot. ref: (y, label), a hairline reference value."""
    s = Svg(w, h, t)
    y0 = s.header(title, subtitle)
    y0 = s.legend(24, y0 + 8, [(e[0], t["series"][e[2]]) for e in series], kind="line") + 6
    L, R, T, B = 64, w - 150, y0 + 10, h - 46
    xs = [x for e in series for x, _ in e[1]]
    ys = [y for e in series for _, y in e[1]] + ([ref[0]] if ref else [])
    xt, yt = nice_ticks(0, max(xs), 6), nice_ticks(0, ymax or max(ys), 5)
    X = lambda v: L + (v - xt[0]) / (xt[-1] - xt[0]) * (R - L)       # noqa: E731
    Y = lambda v: B - (v - yt[0]) / (yt[-1] - yt[0]) * (B - T)       # noqa: E731
    for v in yt:
        s.line(L, Y(v), R, Y(v), t["grid"] if v else t["base"])
        s.text(L - 8, Y(v) + 4, f"{v:g}", size=11.5, color=t["muted"], anchor="end")
    for v in xt:
        s.text(X(v), B + 18, f"{v:,.0f}", size=11.5, color=t["muted"], anchor="middle")
    s.text((L + R) / 2, h - 8, xlabel, size=12, color=t["ink2"], anchor="middle")
    s.add(f'<text transform="translate(16,{(T + B) / 2:.0f}) rotate(-90)" font-size="12" '
          f'fill="{t["ink2"]}" text-anchor="middle">{escape(ylabel)}</text>')
    if ref:
        s.line(L, Y(ref[0]), R, Y(ref[0]), t["muted"], 1.0)
        s.text(R + 8, Y(ref[0]) + 4, ref[1], size=11.5, color=t["ink2"])
    for e in series:
        s.polyline([(X(x), Y(y)) for x, y in e[1]], t["series"][e[2]])
    for e in series:
        x, y = e[1][-1]
        s.dot(X(x), Y(y), t["series"][e[2]])
        label = e[3] if len(e) > 3 else f"{y:.1f} ms"
        if label:
            s.text(X(x) + 10, Y(y) + 4, label, size=12, color=t["ink"], weight="bold")
    return s


def _x_axis(s, t, lo, hi, L, R, T, B, label, fmt=pct_tick):
    ticks = nice_ticks(lo, hi, 6)
    X = lambda v: L + (v - ticks[0]) / (ticks[-1] - ticks[0]) * (R - L)   # noqa: E731
    for v in ticks:
        s.line(X(v), T, X(v), B, t["base"] if v == 0 else t["grid"])
        s.text(X(v), B + 17, fmt(v) if v else "0", size=11.5, color=t["muted"], anchor="middle")
    s.text((L + R) / 2, B + 36, label, size=12, color=t["ink2"], anchor="middle")
    return X


def forest(t, title, subtitle, rows, xlabel, w=760, row_h=24, label_w=250, note=None):
    """Estimates with 95 % CIs, one series. rows: (label, g, lo, hi) in %, or None for a
    group gap; or ("# heading",) for a group heading. Filled dot: CI excludes 0."""
    n_rows = sum(1 for r in rows if r is not None)
    hdr_h = 66 + 18 * (len(subtitle) - 1 if isinstance(subtitle, (list, tuple)) else 0)
    h = hdr_h + 30 + n_rows * row_h + sum(8 for r in rows if r is None) + 60
    s = Svg(w, h, t)
    y = s.header(title, subtitle) + 18
    vals = [v for r in rows if r and len(r) == 4 for v in r[1:]]
    L, R = label_w, w - 92
    T, B = y, h - 52
    X = _x_axis(s, t, min(min(vals), 0), max(max(vals), 0), L, R, T, B, xlabel)
    s.text(w - 24, y - 4, "change", size=11, color=t["muted"], anchor="end")
    c = t["series"][0]
    for r in rows:
        if r is None:
            y += 8
            continue
        if len(r) == 1:
            s.text(24, y + row_h / 2 + 4, r[0].lstrip("# "), size=12, color=t["ink"], weight="bold")
            y += row_h
            continue
        label, g, lo, hi = r
        cy = y + row_h / 2
        s.text(L - 12, cy + 4, label, size=12, color=t["ink2"], anchor="end")
        s.line(X(lo), cy, X(hi), cy, c, 2.0)
        s.dot(X(g), cy, c, hollow=lo <= 0 <= hi)
        s.text(w - 24, cy + 4, pct(g), size=12, color=t["ink"], anchor="end")
        y += row_h
    if note:
        s.text(24, h - 10, note, size=11, color=t["muted"])
    return s


def grouped_bars(t, title, subtitle, cats, series, xlabel, label_series=None, w=760,
                 bar_h=12, gap=2, group_gap=16, label_w=230, xlim=None):
    """Horizontal bars per category, one per series. series: (name, [values in %], slot).
    Values of `label_series` (an index or a list of them) are printed at the bar tips. xlim=(lo, hi) clips
    the axis: a bar beyond it runs to the edge and its true value is printed past it."""
    k = len(series)
    grp_h = k * bar_h + (k - 1) * gap
    hdr = 66 + 18 * (len(subtitle) - 1 if isinstance(subtitle, (list, tuple)) else 0)
    h = hdr + 34 + len(cats) * (grp_h + group_gap) + 52
    s = Svg(w, h, t)
    y = s.header(title, subtitle)
    y = s.legend(24, y + 10, [(n, t["series"][j]) for n, _, j in series]) + 8
    vals = [v for _, vs, _ in series for v in vs]
    sel = label_series if isinstance(label_series, (list, tuple)) else (
        [] if label_series is None else [label_series])
    lo, hi = xlim or _padded(vals, [v for j in sel for v in series[j][1]])
    L, R, T, B = label_w, w - (110 if xlim else 60), y, h - 52
    X = _x_axis(s, t, lo, hi, L, R, T, B, xlabel)
    edge_lo, edge_hi = nice_ticks(lo, hi, 6)[0], nice_ticks(lo, hi, 6)[-1]
    for i, cat in enumerate(cats):
        gy = y + 6 + i * (grp_h + group_gap)
        s.text(L - 12, gy + grp_h / 2 + 4, cat, size=12, color=t["ink2"], anchor="end")
        for j, (name, vs, slot) in enumerate(series):
            by = gy + j * (bar_h + gap)
            v = vs[i]
            vd = min(max(v, edge_lo), edge_hi)
            s.hbar(X(0), X(vd), by, bar_h, t["series"][slot])
            labelled = j in label_series if isinstance(label_series, (list, tuple)) else j == label_series
            if labelled or vd != v:
                text = pct(v) + (" ›" if vd > v or vd < v else "")
                s.text(X(vd) + (-6 if vd < 0 else 6), by + bar_h - 2, text, size=11.5,
                       color=t["ink"], anchor="end" if vd < 0 else "start", weight="bold")
    return s


def dumbbell(t, title, subtitle, rows, names, xlabel, w=760, row_h=34, label_w=270):
    """Before -> after per row. rows: (label, before, after) in %. names: (before, after)."""
    hdr = 66 + 18 * (len(subtitle) - 1 if isinstance(subtitle, (list, tuple)) else 0)
    h = hdr + 34 + len(rows) * row_h + 52
    s = Svg(w, h, t)
    y = s.header(title, subtitle)
    c0, c1 = t["shade"]
    y = s.legend(24, y + 10, [(names[0], c0), (names[1], c1)]) + 8
    vals = [v for _, a, b in rows for v in (a, b)]
    L, R, T, B = label_w, w - 40, y, h - 52
    X = _x_axis(s, t, *_padded(vals, [b for _, _, b in rows]), L, R, T, B, xlabel)
    for i, (label, a, b) in enumerate(rows):
        cy = y + i * row_h + row_h / 2
        s.text(L - 12, cy + 4, label, size=12, color=t["ink2"], anchor="end")
        s.line(X(a), cy, X(b), cy, t["muted"], 2.0)
        s.dot(X(a), cy, c0)
        s.dot(X(b), cy, c1)
        s.text(X(b) - 12, cy + 4, pct(b), size=12, color=t["ink"], anchor="end", weight="bold")
    return s
