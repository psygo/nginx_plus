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

The slope can collapse. On a CPU-sharing server in overload, a request sent while
the server has few connections sits through the flood that follows, while one sent
at the peak sees the server drain: response time *falls* with the connections seen
at dispatch, the fitted slope clamps to 0, the prediction no longer depends on
connections, and every request goes to one server at a time while the others sit
idle with stale estimates. The backlog never drains (round 1 of the full matrix:
+642 % on slowdown_ps at 85 % load, runaways on hetero8_ps and fast3_ps at 95 %).
Two fixes are compared:

  floor      b >= floor * a, keeping the fitted mean (a + b * mean(n) = mean(rt)),
             so connections always count; floor <= 1/c keeps a c-worker line learnable
  regressor  "avg": n is the time-average number of other requests in progress over
             the request's life instead of the count at dispatch

  python3 sim/explore/speed_estimator.py tune      # round 1 sweep, 5 seeds x 100k
  python3 sim/explore/speed_estimator.py full      # round 1 full matrix, 10 seeds x 200k
  python3 sim/explore/speed_estimator.py tune2     # round 2: the fixes, adds 95 % load
  python3 sim/explore/speed_estimator.py full2     # round 2 choice on the full matrix
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
                 weighting="completion", floor=0.0, regressor="dispatch"):
        """weighting: the sample weight comes from the time since this server's previous
        "completion" (as patches/0003 does; biased toward slow requests on busy servers)
        or since its previous "dispatch" before this request was sent (unbiased).

        floor: lower bound on the slope as a fraction of the intercept, b >= floor * a, so
        connections always count. Without it the slope can collapse to 0 in overload.

        regressor: n is the number of other requests in progress at "dispatch", or their
        time "avg" over the request's life (what a CPU-sharing server's speed depends on)."""
        assert weighting in ("completion", "dispatch")
        assert regressor in ("dispatch", "avg")
        self.smooth, self.wmin, self.lam, self.tol, self.decay = smooth, wmin, lam, tol, decay
        self.weighting, self.floor, self.regressor = weighting, floor, regressor
        self.name = (f"linear_delay s={smooth:g} lam={lam:g} tol={tol:g} w={weighting} "
                     f"floor={floor:g} n={regressor}")
        self.st = {}

    def _s(self, p):
        s = self.st.get(p)
        if s is None:
            # [m_n, m_r, m_nn, m_nr, last_sample, last_dispatch, samples, area, area_t]
            # area: integral of connections over time, up to area_t
            s = self.st[p] = [0.0, 0.0, 0.0, 0.0, None, 0.0, 0, 0.0, 0.0]
        return s

    def _area(self, s, p, now):
        s[7] += p.conns * (now - s[8])
        s[8] = now
        return s[7]

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
        if b < self.floor * a:
            # keep the fitted mean: a + b * mn = mr with b = floor * a
            a = mr / (1.0 + self.floor * mn)
            b = self.floor * a
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
        s = self._s(p)
        area = self._area(s, p, now)
        req.n_at_dispatch = p.conns
        p.conns += 1
        gap = now - s[5] if s[6] or s[4] is not None else None   # gap since previous dispatch
        req.lt_state = (gap, area)
        s[5] = now

    def on_done(self, sim, p, req, now, rt):
        n, r, w = self._sample(p, req, now, rt)
        self._update(p, self._s(p), n, r, w)

    def _sample(self, p, req, now, rt):
        """Bookkeeping for a completed request; returns (n, rt, weight) of its sample."""
        s = self._s(p)
        area = self._area(s, p, now)
        p.conns -= 1
        gap, area0 = getattr(req, "lt_state", None) or (None, None)   # unit tests feed on_done only
        n, r = float(req.n_at_dispatch), float(rt)
        if self.regressor == "avg" and area0 is not None and now > req.dispatch:
            n = max(0.0, (area - area0) / (now - req.dispatch) - 1.0)   # others, not this request
        if self.weighting == "dispatch":
            dt = gap
        else:
            dt = None if s[4] is None else now - s[4]
        if s[4] is None or dt is None:
            w = 1.0
        else:
            w = 1.0 if dt >= self.smooth else max(dt / self.smooth, self.wmin)
        s[4] = now
        s[6] += 1
        return n, r, w

    def _update(self, p, s, n, r, w):
        k = 1.0 - w
        s[0] = k * s[0] + w * n
        s[1] = k * s[1] + w * r
        s[2] = k * s[2] + w * n * n
        s[3] = k * s[3] + w * n * r


class HingeDelay(LinearDelay):
    """rt ~ a + b * max(0, n + 1 - k): flat while one of the server's k workers is free,
    then b per extra request in progress. k = 1 is LinearDelay exactly.

    k: an int for every server; "oracle", the backend's true worker count, standing for a
    per-server setting the operator configures; or "learn", the candidate in KS whose line
    leaves the smallest weighted residual variance (ties go to the smaller k).

    The ridge prior assumes a k-worker server, rt = b * (k + x), and the floor is relative
    to that: b >= floor * a / k."""
    KS = (1, 2, 4, 8, 16)

    def __init__(self, k="learn", **kw):
        super().__init__(**kw)
        assert k in ("learn", "oracle") or (isinstance(k, int) and k >= 1)
        self.k = k
        self.name = self.name.replace("linear_delay", f"hinge_delay k={k}")
        self.h = {}          # per peer: {k: [m_x, m_xx, m_xr], "rr": m_rr}
        self.cfg = {}        # "oracle": worker count per peer

    def _ks(self, p):
        if self.k == "learn":
            return self.KS
        if self.k == "oracle":
            return (self.cfg.get(p, 1),)
        return (self.k,)

    def _h(self, p):
        h = self.h.get(p)
        if h is None:
            h = self.h[p] = {"rr": 0.0}
        return h

    def on_dispatch(self, sim, p, req, now):
        if self.k == "oracle" and sim is not None and p not in self.cfg:
            self.cfg[p] = sim.backends[p.idx].c
        super().on_dispatch(sim, p, req, now)

    def _update(self, p, s, n, r, w):
        super()._update(p, s, n, r, w)
        h, c = self._h(p), 1.0 - w
        h["rr"] = c * h["rr"] + w * r * r
        for k in self._ks(p):
            x = max(0.0, n + 1.0 - k)
            m = h.setdefault(k, [0.0, 0.0, 0.0])
            m[0] = c * m[0] + w * x
            m[1] = c * m[1] + w * x * x
            m[2] = c * m[2] + w * x * r

    def knee(self, p):
        ks = self._ks(p)
        if len(ks) == 1:
            return ks[0]
        h, mr = self._h(p), self._s(p)[1]
        var_r = max(0.0, h["rr"] - mr * mr)
        best, best_rv = ks[0], None
        for k in ks:
            m = h.get(k)
            if m is None:
                continue
            var_x = m[1] - m[0] * m[0]
            cov = m[2] - m[0] * mr
            rv = var_r - cov * cov / var_x if var_x > 1e-12 else var_r
            if best_rv is None or rv < best_rv - 1e-9 * (var_r + 1e-12):
                best, best_rv = k, rv
        return best

    def coef(self, p):
        s = self._s(p)
        k = self.knee(p)
        m = self._h(p).get(k)
        if not s[6] or m is None:
            return 0.0, 0.0
        mx, mr = m[0], s[1]
        var = max(0.0, m[1] - mx * mx)
        cov = m[2] - mx * mr
        b = max(0.0, (cov + self.lam * mr) / (var + self.lam * (k + mx)))
        a = max(0.0, mr - b * mx)
        if b < self.floor * a / k:
            a = mr / (1.0 + self.floor * mx / k)
            b = self.floor * a / k
        return a, b

    def predict(self, p, now):
        a, b = self.coef(p)
        eta = a + b * max(0, p.conns + 1 - self.knee(p))
        if self.decay:
            idle = now - self._s(p)[5]
            if idle > FT_MS:
                eta *= 0.5 ** int(idle // (FT_MS + 1000))
        return eta / p.weight


def lin_name(smooth, lam, tol=0.0, weighting="completion", floor=0.0, regressor="dispatch"):
    """Names of the first tuning run are unchanged; floor and regressor add suffixes."""
    name = f"lin_s{int(smooth)}_l{lam:g}_t{tol:g}_{weighting[0]}"
    if floor:
        name += f"_f{floor:g}"
    if regressor == "avg":
        name += "_avg"
    return name


for _s in (1000, 3000, 10000):
    for _l in (0.25, 1, 4):
        for _t in (0.0, 0.05):
            for _w in ("completion", "dispatch"):
                register(lin_name(_s, _l, _t, _w),
                         lambda s=_s, l=_l, t=_t, w=_w: LinearDelay(smooth=s, lam=l, tol=t, weighting=w))

# second round: the fixes for the slope collapse (dispatch-gap weighting, tol 0.05)
FIX_GRID = [(s, l, f, r) for s in (3000, 10000) for l in (1, 4) for f in (0.0, 0.1, 0.25, 0.5)
            for r in ("dispatch", "avg") if f or r == "avg"]
for _s, _l, _f, _r in FIX_GRID:
    register(lin_name(_s, _l, 0.05, "dispatch", _f, _r),
             lambda s=_s, l=_l, f=_f, r=_r: LinearDelay(smooth=s, lam=l, tol=0.05,
                                                        weighting="dispatch", floor=f, regressor=r))

def hin_name(k, lam, floor):
    """Round 3: HingeDelay with the round-2 settings (10 s memory, tol 0.05, dispatch-gap
    weighting, average-load regressor). k: "cfg" (configured worker count) or "learn"."""
    return f"hin_{k}_s10000_l{lam:g}_f{floor:g}_avg"


HINGE_GRID = [(k, l, f) for k in ("cfg", "learn") for l in (1, 4) for f in (0.25, 0.5, 1.0)]
for _k, _l, _f in HINGE_GRID:
    register(hin_name(_k, _l, _f),
             lambda k=_k, l=_l, f=_f: HingeDelay(k="oracle" if k == "cfg" else "learn", smooth=10000,
                                                 lam=l, tol=0.05, weighting="dispatch", floor=f,
                                                 regressor="avg"))

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


ROUND1 = lin_name(10000, 4, 0.05, "dispatch")          # chosen by the first tuning round


def jobs_tune2():
    """Second round, after the slope collapse: adds 95 % load, where it showed up."""
    bals = BASE + [ROUND1] + [lin_name(s, l, 0.05, "dispatch", f, r) for s, l, f, r in FIX_GRID]
    return [dict(scenario=sc, util=u, balancer=b, seed=k, n=100_000)
            for sc in TUNE_SCEN for u in (0.7, 0.85, 0.95) for b in bals for k in range(1, 6)]


def jobs_full2(chosen):
    """Only the new variant; the other balancers' rows are in speed_full.json (same seeds,
    so the same arrivals and costs: still paired)."""
    return [dict(scenario=sc, util=u, balancer=chosen, seed=k, n=200_000)
            for sc in PR_SCEN + ROBUST for u in (0.5, 0.7, 0.85, 0.95) for k in range(1, 11)]


ROUND2 = lin_name(10000, 1, 0.05, "dispatch", 0.25, "avg")   # chosen by round 2
TUNE3_SCEN = TUNE_SCEN + ["fast3_c4", "slowdown_c4"]


def jobs_tune3():
    """Round 3: the hinge model, on the round-2 scenarios plus the other 4-worker ones."""
    bals = BASE + [ROUND2] + [hin_name(k, l, f) for k, l, f in HINGE_GRID]
    return [dict(scenario=sc, util=u, balancer=b, seed=k, n=100_000)
            for sc in TUNE3_SCEN for u in (0.7, 0.85, 0.95) for b in bals for k in range(1, 6)]


def jobs_full3(names):
    return [dict(scenario=sc, util=u, balancer=b, seed=k, n=200_000)
            for sc in PR_SCEN + ROBUST for u in (0.5, 0.7, 0.85, 0.95) for b in names
            for k in range(1, 11)]


def pick3(rows, worst_max=1.15):
    """Best configured-k and best learned-k hinge variant, by the round-2 rule."""
    ranking = pick(rows, prefix=("hin_", "lin_"))
    out = {}
    for kind in ("hin_cfg_", "hin_learn_"):
        sub = [x for x in ranking if x[2].startswith(kind)]
        ok = [x for x in sub if x[1] <= worst_max]
        out[kind[4:-1]] = (ok or sorted(sub, key=lambda x: x[1]))[0][2]
    return out, ranking


def pick2(rows, worst_max=1.15):
    """Robust choice: best geometric mean among variants whose worst cell is at most +15 %
    vs p123_b5_s3; if none qualifies, the one with the best worst cell."""
    ranking = pick(rows)
    ok = [x for x in ranking if x[1] <= worst_max]
    return (ok or sorted(ranking, key=lambda x: x[1]))[0][2], ranking


def pick(rows, prefix=("lin_",)):
    """Best linear variant: lowest geometric-mean p99 ratio vs p123_b5_s3, ties to worst case."""
    by = index(rows)
    cells = sorted({(s, u) for s, u, _ in by})
    out = []
    for b in sorted({b for _, _, b in by if b.startswith(prefix)}):
        rs = [ratio(by[(s, u, b)], by[(s, u, "p123_b5_s3")])[0] for s, u in cells]
        g = mean(map(__import__("math").log, rs))
        out.append((__import__("math").exp(g), max(rs), b))
    out.sort()
    return out


def _tune_table(ranking, top=None):
    rows = ["| variant | mean | worst |", "| --- | --- | --- |"]
    for g, w, b in ranking[:top]:
        rows.append(f"| `{b}` | {(g - 1) * 100:+.1f} % | {(w - 1) * 100:+.0f} % |")
    return rows


def report():
    import math
    md = ["# Exploration #1: learning how response time grows with load", "",
          "See the docstring of `sim/explore/speed_estimator.py` for the method. All figures "
          "are p99 relative to patched least_time (`bucket=5ms smooth=3s`); lower is better.", "",
          "## Tuning, round 1 (5 seeds × 100,000 requests; 9 scenarios at 70 % and 85 % load)", ""]
    md += _tune_table(pick(load("speed_tune.json"))) + [""]
    chosen2 = None
    if os.path.exists(os.path.join(common.RESULTS, "speed_tune2.json")):
        chosen2, ranking2 = pick2(load("speed_tune2.json"))
        md += ["## Tuning, round 2: fixes for the slope collapse", "",
               f"The full matrix of round 1 found `{ROUND1}` diverging on CPU-sharing backends "
               "near saturation. In overload, a request sent while a server has few "
               "connections sits through the flood that follows, so response time falls with "
               "the connections seen at dispatch; the fitted slope clamps to 0, the estimate "
               "stops depending on connections, and requests herd onto one server at a time "
               "while the others sit idle with stale estimates. Two fixes: a slope floor "
               "`b >= floor * a` (`_f<floor>`), and fitting on the average number of "
               "connections over the request's life (`_avg`). Same 9 scenarios, now also at "
               "95 % load (5 seeds × 100,000). Chosen: the best mean among variants whose "
               f"worst cell is at most +15 %: `{chosen2}`.", ""]
        md += _tune_table(ranking2) + [""]
    chosen3 = {}
    if os.path.exists(os.path.join(common.RESULTS, "speed_tune3.json")):
        chosen3, ranking3 = pick3(load("speed_tune3.json"))
        md += ["## Tuning, round 3: a hinge instead of a line", "",
               "A server with k workers is not slowed by the first k requests in progress, so "
               "`HingeDelay` fits rt = a + b * max(0, n + 1 - k); k = 1 is the line of round 2. "
               "k is either configured per server (`hin_cfg`, the backend's worker count, as an "
               "operator would state it) or learned (`hin_learn`: of 1, 2, 4, 8, 16, the knee "
               "with the smallest residual variance). Round-2 settings otherwise; the floor is "
               "b >= floor * a / k. Scenarios: round 2's plus fast3_c4 and slowdown_c4, at "
               f"70, 85 and 95 % load. Chosen: `{chosen3.get('cfg')}` and `{chosen3.get('learn')}`.", ""]
        md += _tune_table(ranking3) + [""]
    if os.path.exists(os.path.join(common.RESULTS, "speed_full.json")):
        full = load("speed_full.json")
        if chosen2 and os.path.exists(os.path.join(common.RESULTS, "speed_full2.json")):
            full = full + load("speed_full2.json")
        if os.path.exists(os.path.join(common.RESULTS, "speed_full3.json")):
            full = full + load("speed_full3.json")
        by = index(full)
        lins = [ROUND1] + ([chosen2] if chosen2 and any(k[2] == chosen2 for k in by) else [])
        lins += [b for b in (chosen3.get("cfg"), chosen3.get("learn")) if b and any(k[2] == b for k in by)]
        cols = ["lt", "sed"] + lins + ["sed_true", "oracle"]
        head = {"lt": "stock lt", "sed": "sed", ROUND1: "linear, round 1",
                "sed_true": "sed_true", "oracle": "oracle"}
        if len(lins) > 1:
            head[lins[1]] = "linear + fix, round 2"
        if chosen3.get("cfg") in lins:
            head[chosen3["cfg"]] = "hinge, configured k"
        if chosen3.get("learn") in lins:
            head[chosen3["learn"]] = "hinge, learned k"
        md += ["## Full matrix (10 seeds × 200,000 requests)", "",
               "Geometric mean over paired seeds with 95 % CI. `sed_true` knows the true "
               "server capacities (not implementable); `sed` is the rejected service-rate "
               "rule. " + " ".join(f"`{head[b]}` is `{b}`." for b in lins), "",
               "| scenario | load | " + " | ".join(head[b] for b in cols) + " |",
               "| --- | --- |" + " --- |" * len(cols)]
        summ = {b: [] for b in lins}
        for sc in PR_SCEN + ROBUST:
            for u in (0.5, 0.7, 0.85, 0.95):
                if (sc, u, ROUND1) not in by:
                    continue
                base = by[(sc, u, "p123_b5_s3")]
                md.append(f"| {sc} | {u} | " + " | ".join(
                    fmt(ratio(by[(sc, u, b)], base)) if (sc, u, b) in by else "" for b in cols) + " |")
                for b in lins:
                    if (sc, u, b) in by:
                        summ[b].append((ratio(by[(sc, u, b)], base)[0], f"{sc}@{u}"))
        md.append("")
        for b in lins:
            rs = summ[b]
            g = math.exp(mean(math.log(r) for r, _ in rs))
            lo, hi = min(rs), max(rs)
            md.append(f"- `{b}`: geometric mean {(g - 1) * 100:+.1f} % over {len(rs)} cells; "
                      f"best {(lo[0] - 1) * 100:+.0f} % ({lo[1]}), worst {(hi[0] - 1) * 100:+.0f} % ({hi[1]}).")
        md += ["", "Robustness scenarios:", ""]
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
    elif cmd == "tune2":
        rows = run_jobs(jobs_tune2(), "speed_tune2.json", label="speed_tune2")
        chosen, ranking = pick2(rows)
        for g, w, b in ranking[:8]:
            print(f"{b:34s} mean {(g - 1) * 100:+.1f} %  worst {(w - 1) * 100:+.0f} %")
        print("chosen:", chosen)
    elif cmd == "full2":
        chosen = sys.argv[2] if len(sys.argv) > 2 else pick2(load("speed_tune2.json"))[0]
        print("full matrix with", chosen)
        run_jobs(jobs_full2(chosen), "speed_full2.json", label="speed_full2")
        report()
    elif cmd == "tune3":
        rows = run_jobs(jobs_tune3(), "speed_tune3.json", label="speed_tune3")
        chosen, ranking = pick3(rows)
        for g, w, b in ranking:
            print(f"{b:34s} mean {(g - 1) * 100:+.1f} %  worst {(w - 1) * 100:+.0f} %")
        print("chosen:", chosen)
    elif cmd == "full3":
        names = sys.argv[2:] or list(pick3(load("speed_tune3.json"))[0].values())
        print("full matrix with", names)
        run_jobs(jobs_full3(names), "speed_full3.json", label="speed_full3")
        report()
    elif cmd == "report":
        report()
    elif cmd == "smoke":
        import experiments
        for sc in ["fast3", "hetero8_ps", "mixed4", "slowdown_c4"]:
            for b in ["p123_b5_s3", "sed_true", lin_name(3000, 1), lin_name(3000, 1, 0, "dispatch")]:
                r = experiments.run_job(dict(scenario=sc, util=0.7, balancer=b, seed=1, n=20000))
                print(f"{sc:12s} {b:22s} p99 {common.p99_of(r):8.1f}")
