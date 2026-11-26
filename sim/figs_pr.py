#!/usr/bin/env python3
"""Figures for the pull request texts (pr/PR1.md, pr/PR2.md), from stored results.

  python3 sim/figs_pr.py         # results/figs/pr1_*.png, pr2_*.png (light and dark)

pr1_averages  the dead band: nginx's integer average vs fixed point, same samples
pr1_bucket    bucket=5ms with integer vs fixed-point averages (results/sim_pr1_bucket.json)
pr2_real      real nginx, bucket=5ms smooth=3s vs stock least_time, per cell with 95 % CI
pr2_params    real nginx, each parameter alone and both together
"""
import glob
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "bench", "linux"))
import charts                                          # noqa: E402
from lbsim import LeastTimePatch, ewma                 # noqa: E402
from report_pr import paired_ratio                     # noqa: E402

RES = os.path.join(ROOT, "results")
OUT = os.path.join(RES, "figs")


def averages_series(n=2000, seed=1):
    """A server answering in about 2 ms (exponential, measured in whole ms as nginx does),
    with one response in a hundred taking 40 ms. Both averages see the same samples."""
    rng = random.Random(seed)
    lt = LeastTimePatch()
    a_int = a_fp = 0
    pts_int, pts_fp, samples = [], [], []
    for i in range(1, n + 1):
        v = 40 if rng.random() < 0.01 else int(rng.expovariate(1 / 2.0))
        samples.append(v)
        a_int = ewma(a_int, v)
        a_fp = lt.avg(a_fp, v, 0.05)
        if i % 10 == 0 or i == 1:
            pts_int.append((i, a_int))
            pts_fp.append((i, a_fp / lt.SCALE))
    return pts_int, pts_fp, sum(samples) / len(samples)


def fig_pr1_averages(t):
    pts_int, pts_fp, actual = averages_series()
    return charts.line_chart(
        t, "least_time's average for a server answering in about 2 ms",
        ["The same responses (one in a hundred takes 40 ms), averaged as nginx does today and with",
         "this change. A response moves the integer average only if it differs by 10 ms or more."],
        [("current: whole milliseconds", pts_int, 1, "stuck at 10 ms"),
         ("this change: 1/64 ms", pts_fp, 0, None)],
        "responses", "average (ms)", ref=(actual, f"actual mean {actual:.1f} ms"))


def pr1_bucket_rows():
    rows = json.load(open(os.path.join(RES, "sim_pr1_bucket.json")))
    by = {}
    for r in rows:
        by.setdefault((r["scenario"], r["util"], r["balancer"]), {})[r["seed"]] = r["p99"]
    cells = [("fast3", 0.5, "3 servers, 2/6/15 ms, 50 % load"),
             ("fast3", 0.7, "3 servers, 2/6/15 ms"),
             ("hetero8", 0.7, "8 servers of unequal speed"),
             ("big16", 0.7, "16 servers of unequal speed"),
             ("lognorm_het", 0.7, "8 unequal servers, lognormal")]
    out = []
    for sc, u, label in cells:
        base = by[(sc, u, "lt")]
        a = (paired_ratio(by[(sc, u, "lt_b5")], base)[0] - 1) * 100
        b = (paired_ratio(by[(sc, u, "p12_b5")], base)[0] - 1) * 100
        out.append((label, a, b))
    return out


def fig_pr1_bucket(t):
    return charts.dumbbell(
        t, "A 5 ms bucket only pays off with this change",
        ["p99 change vs stock least_time with bucket=5ms (the follow-up pull request);",
         "simulation, 70 % load unless noted, 10 paired seeds × 200,000 requests"],
        pr1_bucket_rows(), ("integer averages", "this change"), "p99 change vs stock least_time")


def linux_by():
    from report_linux import disturbed, metric
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
        g, lo, hi, _ = paired_ratio(by[(w, sc, u, "p123_b5_s3")], by[(w, sc, u, "lt")])
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
    for name, fn in (("pr1_averages", fig_pr1_averages), ("pr1_bucket", fig_pr1_bucket),
                     ("pr2_real", fig_pr2_real), ("pr2_params", fig_pr2_params)):
        charts.render(fn, name, OUT)
        print(name)
    for label, a, b in pr1_bucket_rows():
        print(f"  pr1_bucket {label}: integer {a:+.0f} %, this change {b:+.0f} %")


if __name__ == "__main__":
    main()
