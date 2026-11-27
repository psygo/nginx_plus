#!/usr/bin/env python3
"""Real-nginx figures for nginx/nginx#2050, from the stored Linux runs (the simulation
figures of #2048 are made in nginx_sim, research/pr/figs_pr.py).

  python3 bench/linux/figs_pr2.py    # results/figs/pr2_*.png (light and dark)

pr2_real      real nginx, bucket=5ms smooth=3s vs stock least_time, per cell with 95 % CI
pr2_params    real nginx, each parameter alone and both together
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nginx_sim_path  # noqa: E402,F401  (puts nginx_sim on sys.path)
from nginx_sim import charts  # noqa: E402
from nginx_sim.stats import paired_ratio  # noqa: E402
from report_linux import disturbed, metric  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "figs")


def linux_by():
    rows = []
    for p in sorted(glob.glob(os.path.join(RES, "linux_*.jsonl"))):
        rows += [json.loads(x) for x in open(p) if x.strip()]
    bad = {id(r) for r in disturbed(rows, lambda r: (r["scenario"], r["util"], r["workers"]))}
    by = {}
    for r in rows:
        if id(r) not in bad:
            by.setdefault((r["workers"], r["scenario"], r["util"], r["variant"]), {})[r["rep"]] = metric(r)
    return by


PR2_ROWS = [
    "# Servers of unequal speed",
    (1, "fast3", 0.5, "3 servers, 2/6/15 ms · 50 %"),
    (1, "fast3", 0.7, "3 servers, 2/6/15 ms · 70 %"),
    (1, "fast3", 0.85, "3 servers, 2/6/15 ms · 85 %"),
    (1, "hetero8", 0.5, "8 servers, unequal · 50 %"),
    (1, "hetero8", 0.7, "8 servers, unequal · 70 %"),
    (1, "hetero8", 0.85, "8 servers, unequal · 85 %"),
    (1, "lognorm_het", 0.7, "8 unequal, lognormal · 70 %"),
    (1, "lognorm_het", 0.85, "8 unequal, lognormal · 85 %"),
    (1, "slow3", 0.7, "3 servers, 20/60/150 ms · 70 %"),
    "# Mixed 2 ms / 80 ms requests",
    (1, "bimodal4", 0.7, "4 equal servers · 70 %"),
    (1, "bimodal4", 0.85, "4 equal servers · 85 %"),
    (1, "bimodal4_het", 0.7, "4 servers 2,1,1,½ · 70 %"),
    (1, "bimodal4_het", 0.85, "4 servers 2,1,1,½ · 85 %"),
    "# One server at ¼ speed for 20 s",
    (1, "slowdown", 0.7, "4 equal servers · 70 %"),
    "# Equal servers (control)",
    (1, "homog8", 0.7, "8 equal servers · 70 %"),
    (1, "homog8", 0.85, "8 equal servers · 85 %"),
    "# 4 worker processes with zone",
    (4, "hetero8", 0.85, "8 servers, unequal · 85 %"),
    (4, "bimodal4_het", 0.85, "4 servers 2,1,1,½, mixed · 85 %"),
]


def fig_pr2_real(t):
    by = linux_by()
    rows = []
    for r in PR2_ROWS:
        if isinstance(r, str):
            if rows:
                rows.append(None)
            rows.append((r,))
            continue
        w, sc, u, label = r
        g, lo, hi = paired_ratio(by[(w, sc, u, "p123_b5_s3")], by[(w, sc, u, "lt")])
        rows.append((label, (g - 1) * 100, (lo - 1) * 100, (hi - 1) * 100))
    return charts.forest(
        t, "Real nginx: p99 with bucket=5ms smooth=3s vs stock least_time",
        ["Linux, -O2 builds, 5 paired repetitions per cell (same arrivals and request costs).",
         "Dot: geometric mean of the paired ratios; line: 95 % CI; hollow dot: the CI includes 0."],
        rows, "p99 change vs stock least_time", label_w=262)


def fig_pr2_params(t):
    by = linux_by()
    cells = [(1, "fast3", 0.5, "3 servers, 2/6/15 ms · 50 %"),
             (1, "hetero8", 0.7, "8 servers, unequal · 70 %"),
             (1, "lognorm_het", 0.7, "lognormal, unequal · 70 %"),
             (1, "bimodal4", 0.85, "4 equal, mixed · 85 %"),
             (1, "bimodal4_het", 0.85, "4 servers 2,1,1,½, mixed · 85 %")]
    series = []
    for name, v, slot in (("bucket=5ms only", "p12_b5", 1), ("smooth=3s only", "p13_s3", 2),
                          ("both", "p123_b5_s3", 0)):
        vals = [(paired_ratio(by[(w, sc, u, v)], by[(w, sc, u, "lt")])[0] - 1) * 100
                for w, sc, u, _ in cells]
        series.append((name, vals, slot))
    return charts.grouped_bars(
        t, "Each parameter is needed",
        ["Real nginx, p99 change vs stock least_time. bucket does most with servers of unequal",
         "speed; smooth does most with mixed request costs, where bucket alone does nothing."],
        [c[3] for c in cells], series, "p99 change vs stock least_time", label_series=2,
        label_w=250)


def main():
    for name, fn in (("pr2_real", fig_pr2_real), ("pr2_params", fig_pr2_params)):
        charts.render(fn, name, OUT)
        print(name)


if __name__ == "__main__":
    main()
