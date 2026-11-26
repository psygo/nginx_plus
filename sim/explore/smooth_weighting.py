#!/usr/bin/env python3
"""Bias in the `smooth` weighting of patches/0003, and an unbiased alternative.

patches/0003 weights each response by w = min(1, max(0.01, elapsed / smooth)), where
`elapsed` is the time since the server's previous response. On a busy one-at-a-time
server, the gap before a response is roughly that response's own service time, so slow
responses get more weight than fast ones and the average is biased upward (an inspection
paradox). Measured on slow3 at 70 % load: +14 % on the busy 20 ms server and +36-41 % on
the moderately used 60 ms one (sim/explore/test_smooth_weighting.py).

LeastTimeDispatchGap takes `elapsed` as the time between the previous request sent to the
server and this one, recorded when the request is sent. It does not depend on how long
the request takes, so the average is unbiased. In C this needs no new shared field: the
per-peer timestamp of patch 3 records the last dispatch instead of the last response,
and the gap travels in the per-request balancer data (ngx_http_upstream_lt_peer_data_t).

  python3 sim/explore/smooth_weighting.py run      # 10 seeds x 200k
  python3 sim/explore/smooth_weighting.py report   # results/smooth_weighting.md
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from common import EXTRA_DESC, fmt, index, load, mean, ratio, run_jobs  # noqa: E402
import experiments                                     # noqa: E402
from lbsim import LeastTimePatch                       # noqa: E402


class LeastTimeDispatchGap(LeastTimePatch):
    def __init__(self, inflight=False, bucket=20, smooth=None):
        super().__init__(inflight=inflight, bucket=bucket, smooth=smooth)
        self.name = self.name.replace("[patched]", "[dispatch-gap weight]")

    def gap_weight(self, gap):
        if gap is None or gap >= self.smooth:
            return 1.0
        return max(gap / self.smooth, 0.01)

    def on_dispatch(self, sim, p, req, now):
        cur = int(now)
        req.lt_state = None if p.ew_last is None else cur - p.ew_last
        p.ew_last = cur
        super().on_dispatch(sim, p, req, now)

    def on_done(self, sim, p, req, now, rt):
        if not self.smooth:
            return super().on_done(sim, p, req, now, rt)
        rt = int(rt)
        w = self.gap_weight(req.lt_state)
        if self.inflight:
            p.header_time = self.avg(p.header_time, rt, w)
            self._inflight_done(p, req, now, rt)
            p.response_time = self.avg(p.response_time, rt, 0.05)
        else:
            p.response_time = self.avg(p.response_time, rt, w)
            p.header_time = self.avg(p.header_time, rt, w)
        p.conns -= 1


class LeastTimeTypicalGap(LeastTimePatch):
    """Weight from the server's typical gap between responses (a fixed-weight average of
    earlier gaps, 1/16 per response), not from the gap just before this response, so the
    weight is independent of the sample it weights. A gap of at least `smooth` still
    replaces the average (idle server). In C: one more per-peer field than patch 3."""
    GAP_W = 1 / 16

    def __init__(self, inflight=False, bucket=20, smooth=None):
        super().__init__(inflight=inflight, bucket=bucket, smooth=smooth)
        self.name = self.name.replace("[patched]", "[typical-gap weight]")

    def weight_for(self, p, gap):
        if gap is None or gap >= self.smooth:
            return 1.0
        typical = p.svc if p.svc else gap
        return max(min(typical / self.smooth, 1.0), 0.01)

    def weight(self, p, now):
        if not self.smooth:
            return 0.05
        cur = int(now)
        gap = None if p.ew_last is None else cur - p.ew_last
        w = self.weight_for(p, gap)
        if gap is not None:
            p.svc = float(gap) if not p.svc else p.svc + (gap - p.svc) * self.GAP_W
        p.ew_last = cur
        return w


_orig_get_bal = experiments.get_bal


def _get_bal(name):
    """Adds "pd_b<bucket>_s<smooth ms|0>[_i]": LeastTimeDispatchGap."""
    if name.startswith(("pd_", "pt_")):
        cls = LeastTimeDispatchGap if name.startswith("pd_") else LeastTimeTypicalGap
        parts = name[3:].split("_")
        b, s = int(parts[0][1:]), int(parts[1][1:])
        return cls(bucket=b, smooth=s or None, inflight="i" in parts[2:])
    return _orig_get_bal(name)


experiments.get_bal = _get_bal

PR_SCEN = ["fast3", "slow3", "hetero8", "big16", "lognorm_het", "bimodal4",
           "bimodal4_het", "slowdown", "slowdown_ps", "homog8", "fast3_homog", "lognorm4"]
ROBUST = ["hetero8_ps", "hetero8_c4", "fast3_ps", "fast3_c4", "mixed4", "slowdown_c4"]
UTILS = (0.5, 0.7, 0.85, 0.95)
BALS = ["lt", "least_conn", "p123_b5_s3", "pd_b5_s3000", "pt_b5_s3000",
        "pb5_s10000", "pd_b5_s10000", "pt_b5_s10000"]


def jobs():
    return [dict(scenario=sc, util=u, balancer=b, seed=k, n=200_000)
            for sc in PR_SCEN + ROBUST for u in UTILS for b in BALS for k in range(1, 11)]


def report():
    by = index(load("smooth_weighting.json"))
    cols = [("pd_b5_s3000", "p123_b5_s3", "dispatch-gap vs patch 3 (smooth=3s)"),
            ("pd_b5_s10000", "pb5_s10000", "dispatch-gap vs patch 3 (smooth=10s)"),
            ("pt_b5_s3000", "p123_b5_s3", "typical-gap vs patch 3 (smooth=3s)"),
            ("pt_b5_s10000", "pb5_s10000", "typical-gap vs patch 3 (smooth=10s)"),
            ("pd_b5_s3000", "lt", "dispatch-gap smooth=3s vs stock"),
            ("p123_b5_s3", "lt", "patch 3 smooth=3s vs stock"),
            ("pt_b5_s3000", "lt", "typical-gap smooth=3s vs stock"),
            ("pd_b5_s3000", "least_conn", "dispatch-gap smooth=3s vs least_conn")]
    md = ["# `smooth` weighting: patch 3 vs two unbiased weightings", "",
          "See the docstring of `sim/explore/smooth_weighting.py`. 10 paired seeds × 200,000 "
          "requests; p99 ratios, geometric mean with 95 % CI (slowdown scenarios: p99 over the "
          "slow window). All variants use bucket=5ms.", "",
          "| scenario | load | " + " | ".join(c[2] for c in cols) + " |",
          "| --- | --- |" + " --- |" * len(cols)]
    summ = {c[2]: [] for c in cols}
    for sc in PR_SCEN + ROBUST:
        for u in UTILS:
            cells = []
            for a, b, label in cols:
                r = ratio(by[(sc, u, a)], by[(sc, u, b)])
                summ[label].append((r[0], f"{sc}@{u}"))
                cells.append(fmt(r))
            md.append(f"| {sc} | {u} | " + " | ".join(cells) + " |")
    md += ["", "## Summary over all cells", "", "| comparison | geometric mean | best | worst |",
           "| --- | --- | --- | --- |"]
    for label, rs in summ.items():
        g = math.exp(mean(math.log(r) for r, _ in rs))
        lo, hi = min(rs), max(rs)
        md.append(f"| {label} | {(g - 1) * 100:+.1f} % | {(lo[0] - 1) * 100:+.0f} % ({lo[1]}) | "
                  f"{(hi[0] - 1) * 100:+.0f} % ({hi[1]}) |")
    md += ["", "Robustness scenarios:", ""] + [f"- `{k}`: {v}" for k, v in EXTRA_DESC.items()] + [""]
    out = os.path.join(common.RESULTS, "smooth_weighting.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_jobs(jobs(), "smooth_weighting.json")
        report()
    else:
        report()
