#!/usr/bin/env python3
"""Exploration #1: estimate how each backend's response time grows with load.

least_time scores a server as  average_response_time x (requests in progress + 1).
The average already contains queueing delay, so the wait is counted twice.
The service-rate rule (rejected_ideas.md, section 2) fixed that for backends
that serve one request at a time, but was badly wrong for backends that share
their CPU or have several workers.

LinearDelay learns, per server, a line

    response_time  ~  a + b * n          n = requests in progress when sent

by exponentially weighted least squares on completed requests, and sends a
request to the server with the lowest predicted response time a + b * conns.
For a one-at-a-time (FCFS) server, a ~ b ~ service time. For a CPU-sharing
server the line is similar. For a server with c workers, b ~ service time / c
and a ~ service time. So the same rule adapts to each kind of backend without
being told which kind it is.

When n hardly varies (light load) the slope is not identifiable, so b is pulled
toward a by a ridge prior of strength lam:

    b = (cov(n, rt) + lam * mean(rt)) / (var(n) + lam * (1 + mean(n)))

With no variance this is mean(rt) / (1 + mean(n)), the service-rate rule; with
plenty of variance it becomes the regression slope.

Samples are weighted as in the "smooth" patch: w = min(1, max(wmin, dt/smooth)).
Idle servers are made attractive again by halving the prediction per
fail_timeout + 1 seconds without a request, as nginx does with response times.

  python3 sim/explore/speed_estimator.py tune      # parameter sweep, 5 seeds x 100k
  python3 sim/explore/speed_estimator.py full      # full matrix, 10 seeds x 200k
  python3 sim/explore/speed_estimator.py report    # results/speed_estimator.md
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from common import EXTRA_DESC, fmt, index, load, mean, ratio, register, run_jobs  # noqa: E402
from lbsim import RoundRobin, smooth_wrr_among         # noqa: E402

FT_MS = 10_000                    # fail_timeout default, 10 s


class LinearDelay(RoundRobin):
    def __init__(self, smooth=3000.0, wmin=0.01, lam=1.0, tol=0.0, decay=True,
                 weighting="completion"):
        """weighting: the sample weight comes from the time since this server's previous
        "completion" (as patches/0003 does; biased toward slow requests on busy servers)
        or since its previous "dispatch" before this request was sent (unbiased)."""
        assert weighting in ("completion", "dispatch")
        self.smooth, self.wmin, self.lam, self.tol, self.decay = smooth, wmin, lam, tol, decay
        self.weighting = weighting
        self.name = f"linear_delay s={smooth:g} lam={lam:g} tol={tol:g} w={weighting}"
        self.st = {}

    def _s(self, p):
        s = self.st.get(p)
        if s is None:
            # [m_n, m_r, m_nn, m_nr, last_sample, last_dispatch, samples]
            s = self.st[p] = [0.0, 0.0, 0.0, 0.0, None, 0.0, 0]
        return s

    def coef(self, p):
        s = self._s(p)
        if not s[6]:
            return 0.0, 0.0
        mn, mr, mnn, mnr = s[0], s[1], s[2], s[3]
        var = max(0.0, mnn - mn * mn)
        cov = mnr - mn * mr
        b = (cov + self.lam * mr) / (var + self.lam * (1.0 + mn))
        b = max(0.0, b)
        a = max(0.0, mr - b * mn)
        return a, b

    def predict(self, p, now):
        a, b = self.coef(p)
        eta = a + b * p.conns
        if self.decay:
            idle = now - self._s(p)[5]
            if idle > FT_MS:
                eta *= 0.5 ** int(idle // (FT_MS + 1000))
        return eta / p.weight

    def select(self, sim, peers, req, now):
        etas = [self.predict(p, now) for p in peers]
        best = min(etas)
        lim = best * (1.0 + self.tol) + 1e-9
        tied = [p for p, e in zip(peers, etas) if e <= lim]
        if len(tied) == 1:
            return tied[0]
        return smooth_wrr_among(tied, 0, lambda p: True)

    def on_dispatch(self, sim, p, req, now):
        req.n_at_dispatch = p.conns
        p.conns += 1
        s = self._s(p)
        req.lt_state = now - s[5] if s[6] or s[4] is not None else None   # gap since previous dispatch
        s[5] = now

    def on_done(self, sim, p, req, now, rt):
        p.conns -= 1
        s = self._s(p)
        n, r = float(req.n_at_dispatch), float(rt)
        if self.weighting == "dispatch":
            dt = getattr(req, "lt_state", None)
        else:
            dt = None if s[4] is None else now - s[4]
        if s[4] is None or dt is None:
            w = 1.0
        else:
            w = 1.0 if dt >= self.smooth else max(dt / self.smooth, self.wmin)
        s[4] = now
        s[6] += 1
        k = 1.0 - w
        s[0] = k * s[0] + w * n
        s[1] = k * s[1] + w * r
        s[2] = k * s[2] + w * n * n
        s[3] = k * s[3] + w * n * r


def lin_name(smooth, lam, tol=0.0, weighting="completion"):
    return f"lin_s{int(smooth)}_l{lam:g}_t{tol:g}_{weighting[0]}"


for _s in (1000, 3000, 10000):
    for _l in (0.25, 1, 4):
        for _t in (0.0, 0.05):
            for _w in ("completion", "dispatch"):
                register(lin_name(_s, _l, _t, _w),
                         lambda s=_s, l=_l, t=_t, w=_w: LinearDelay(smooth=s, lam=l, tol=t, weighting=w))

PR_SCEN = ["fast3", "slow3", "hetero8", "big16", "lognorm_het", "bimodal4",
           "bimodal4_het", "slowdown", "slowdown_ps", "homog8", "fast3_homog", "lognorm4"]
ROBUST = ["hetero8_ps", "hetero8_c4", "fast3_ps", "fast3_c4", "mixed4", "slowdown_c4"]
TUNE_SCEN = ["fast3", "slow3", "hetero8", "bimodal4", "slowdown_ps", "homog8",
             "hetero8_ps", "hetero8_c4", "mixed4"]
BASE = ["least_conn", "lt", "p123_b5_s3", "sed", "sed_true"]


def jobs_tune():
    bals = BASE + [lin_name(s, l, t, w) for s in (1000, 3000, 10000) for l in (0.25, 1, 4)
                   for t in (0.0, 0.05) for w in ("completion", "dispatch")]
    return [dict(scenario=sc, util=u, balancer=b, seed=k, n=100_000)
            for sc in TUNE_SCEN for u in (0.7, 0.85) for b in bals for k in range(1, 6)]


def jobs_full(chosen):
    bals = BASE + ["oracle", chosen]
    return [dict(scenario=sc, util=u, balancer=b, seed=k, n=200_000)
            for sc in PR_SCEN + ROBUST for u in (0.5, 0.7, 0.85, 0.95) for b in bals
            for k in range(1, 11)]


def pick(rows):
    """Best linear variant: lowest geometric-mean p99 ratio vs p123_b5_s3, ties to worst case."""
    by = index(rows)
    cells = sorted({(s, u) for s, u, _ in by})
    out = []
    for b in sorted({b for _, _, b in by if b.startswith("lin_")}):
        rs = [ratio(by[(s, u, b)], by[(s, u, "p123_b5_s3")])[0] for s, u in cells]
        g = mean(map(__import__("math").log, rs))
        out.append((__import__("math").exp(g), max(rs), b))
    out.sort()
    return out


def report():
    import math
    md = ["# Exploration #1: learning how response time grows with load", "",
          "See the docstring of `sim/explore/speed_estimator.py` for the method.", ""]
    tune = load("speed_tune.json")
    ranking = pick(tune)
    md += ["## Tuning (5 seeds × 100,000 requests; 9 scenarios at 70 % and 85 % load)", "",
           "Geometric mean and worst case of p99 relative to patched least_time "
           "(`bucket=5ms smooth=3s`). Lower is better.", "",
           "| variant | mean | worst |", "| --- | --- | --- |"]
    for g, w, b in ranking:
        md.append(f"| `{b}` | {(g - 1) * 100:+.1f} % | {(w - 1) * 100:+.0f} % |")
    md.append("")
    path = os.path.join(common.RESULTS, "speed_full.json")
    if os.path.exists(path):
        full = load("speed_full.json")
        by = index(full)
        chosen = next(b for _, _, b in by if b.startswith("lin_"))
        md += [f"## Full matrix: `{chosen}` (10 seeds × 200,000 requests)", "",
               "p99 change vs patched least_time (`p123`, bucket=5ms smooth=3s), "
               "geometric mean over paired seeds with 95 % CI. "
               "`sed_true` knows the true server capacities (not implementable); "
               "`sed` is the rejected service-rate rule.", "",
               "| scenario | load | stock lt | sed | linear (new) | sed_true | oracle |",
               "| --- | --- | --- | --- | --- | --- | --- |"]
        worst, logs = (None, 0), []
        for sc in PR_SCEN + ROBUST:
            for u in (0.5, 0.7, 0.85, 0.95):
                if (sc, u, chosen) not in by:
                    continue
                base = by[(sc, u, "p123_b5_s3")]
                cells = [fmt(ratio(by[(sc, u, b)], base)) for b in ("lt", "sed", chosen, "sed_true", "oracle")]
                r = ratio(by[(sc, u, chosen)], base)
                logs.append(math.log(r[0]))
                if r[0] > worst[1]:
                    worst = (f"{sc}@{u}", r[0])
                md.append(f"| {sc} | {u} | " + " | ".join(cells) + " |")
        md += ["", f"Geometric mean over all cells: {(math.exp(mean(logs)) - 1) * 100:+.1f} %; "
               f"worst cell: {worst[0]} {(worst[1] - 1) * 100:+.0f} %.", "",
               "Robustness scenarios:", ""]
        md += [f"- `{k}`: {v}" for k, v in EXTRA_DESC.items()] + [""]
    out = os.path.join(common.RESULTS, "speed_estimator.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tune"
    if cmd == "tune":
        rows = run_jobs(jobs_tune(), "speed_tune.json")
        for g, w, b in pick(rows)[:6]:
            print(f"{b:24s} mean {(g - 1) * 100:+.1f} %  worst {(w - 1) * 100:+.0f} %")
    elif cmd == "full":
        chosen = sys.argv[2] if len(sys.argv) > 2 else pick(load("speed_tune.json"))[0][2]
        print("full matrix with", chosen)
        run_jobs(jobs_full(chosen), "speed_full.json")
        report()
    elif cmd == "report":
        report()
    elif cmd == "smoke":
        import experiments
        for sc in ["fast3", "hetero8_ps", "mixed4", "slowdown_c4"]:
            for b in ["p123_b5_s3", "sed_true", lin_name(3000, 1), lin_name(3000, 1, 0, "dispatch")]:
                r = experiments.run_job(dict(scenario=sc, util=0.7, balancer=b, seed=1, n=20000))
                print(f"{sc:12s} {b:22s} p99 {common.p99_of(r):8.1f}")
