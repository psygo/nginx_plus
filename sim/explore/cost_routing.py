#!/usr/bin/env python3
"""Exploration #2: route by request cost.

The headroom measurement (results/rejected_ideas.md, sim_headroom.json) showed
that a balancer knowing true server speeds is still 1.2-1.5x behind an oracle
that also knows each request's cost. Closing that gap needs knowledge of the
request, not better timing. This explores how much a cost hint is worth.

A request carries a class (think: URL pattern, mapped in nginx config) whose
mean cost is known; the per-request estimate is class mean x lognormal noise
(sigma), and a fraction of requests can be misclassified (estimate taken from
a random other class).

Balancers:
  least_conn, p123 (patched least_time, bucket=5ms smooth=3s)   baselines
  least_work_w     least outstanding estimated work / configured capacity
                   (capacities given as server weights, as an admin would)
  least_work_ls    same, but capacity learned from response times
  sita             two server groups chosen by the admin: the costliest class
                   on one, everything else on the other, sized by work share;
                   inside a group, fewest requests per unit of capacity.
                   This is what nginx can do today with two upstream blocks
                   and a location or map.
  oracle           knows true costs and speeds (ceiling)

Workloads: bimodal (90 % 2 ms / 10 % 80 ms) and api3 (70 % 1 ms, 25 % 10 ms,
5 % 200 ms); groups eq4 (4 equal), het4 (capacities 2,1,1,½), het8
(2,2,1,1,1,1,½,½). Scenario names: cr_<workload>_<group>[_m<percent misclassified>].

  python3 sim/explore/cost_routing.py run      # 10 seeds x 200k
  python3 sim/explore/cost_routing.py report   # results/cost_routing.md
"""
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from common import add_scenarios, mean, ratio, fmt, register, run_jobs_ext  # noqa: E402
from lbsim import Backend, LeastWork, RoundRobin       # noqa: E402

def _zipf50():
    """50 routes: popularity ~ 1/rank, mean cost log-uniform in 1-200 ms (fixed draw)."""
    r = random.Random(11)
    means = [math.exp(r.uniform(0.0, math.log(200.0))) for _ in range(50)]
    w = [1.0 / (i + 1) for i in range(50)]
    t = sum(w)
    return [(x / t, m) for x, m in zip(w, means)]


WORKLOADS = {"bimodal": [(0.9, 2.0), (0.1, 80.0)],
             "api3": [(0.70, 1.0), (0.25, 10.0), (0.05, 200.0)],
             "zipf50": _zipf50()}
GROUPS = {"eq4": [1, 1, 1, 1], "het4": [2, 1, 1, 0.5],
          "het8": [2, 2, 1, 1, 1, 1, 0.5, 0.5]}


def classes_workload(classes, sigma, mis, rng_seed=12345):
    """(class, true cost, estimate). Misclassification uses its own RNG so the
    arrival/cost stream stays identical across sigma/mis settings."""
    probs = [c[0] for c in classes]
    mrng = random.Random(rng_seed)

    def w(rng):
        i = rng.choices(range(len(classes)), probs)[0]
        m = classes[i][1]
        c = rng.expovariate(1.0 / m)
        noise = rng.gauss(0, 1)                 # always drawn: keeps streams paired
        j = i
        if mis and mrng.random() < mis:
            j = mrng.choice([k for k in range(len(classes)) if k != i])
        est = classes[j][1] * math.exp(sigma * noise)
        return i, c, est
    w.mean = sum(p * m for p, m in classes)
    w.classes = classes
    return w


@add_scenarios
def _cr(name, sigma):
    if not name.startswith("cr_"):
        return None
    parts = name.split("_")
    wl, grp = parts[1], parts[2]
    mis = 0.0
    for x in parts[3:]:
        if x.startswith("m") and x[1:].isdigit():
            mis = int(x[1:]) / 100
    caps = GROUPS[grp]
    kind = next((x for x in parts[3:] if x in ("ps", "c4", "mix")), "")

    def backends():
        # same capacity per server in every kind; c4 = 4 workers at 1/4 the speed;
        # mix = FCFS, PS, FCFS x4, PS x4 in turn
        out = []
        for i, c in enumerate(caps):
            k = kind if kind != "mix" else ("", "ps", "c4", "c4ps")[i % 4]
            w = 4 if k.startswith("c4") else 1
            out.append(Backend(i, w, c / w, "ps" if k.endswith("ps") else "fcfs"))
        return out
    return dict(backends=backends, workload=lambda: classes_workload(WORKLOADS[wl], sigma, mis))


class SITA(RoundRobin):
    """Costliest class on its own server group; groups sized by work share."""
    name = "sita"

    def __init__(self):
        self.assign = None

    def _setup(self, sim):
        classes = sim.workload.classes
        long_c = max(range(len(classes)), key=lambda i: classes[i][1])
        work = [p * m for p, m in classes]
        share_long = work[long_c] / sum(work)
        caps = [b.c * b.speed for b in sim.backends]
        total = sum(caps)
        target = {"long": share_long * total, "short": (1 - share_long) * total}
        got = {"long": 0.0, "short": 0.0}
        assign = {}
        for i in sorted(range(len(caps)), key=lambda i: -caps[i]):
            g = max(got, key=lambda g: target[g] - got[g])
            assign[i] = g
            got[g] += caps[i]
        for g in ("long", "short"):                     # each group needs a server
            if g not in assign.values():
                other = [i for i in assign if assign[i] != g]
                assign[min(other, key=lambda i: caps[i])] = g
        self.long_c, self.assign, self.caps = long_c, assign, caps

    def select(self, sim, peers, req, now):
        if self.assign is None:
            self._setup(sim)
        g = "long" if req.cls == self.long_c else "short"
        cand = [p for p in peers if self.assign[p.idx] == g]
        return min(cand, key=lambda p: ((p.conns + 1) / self.caps[p.idx], sim.rng.random()))


class LearnedCost(RoundRobin):
    """Least outstanding work, with route costs and server speeds learned from response
    times alone: no cost hint from the application and no configured capacity.

    The key is the request's route (in nginx: a variable such as $uri or a map of it):
    key="class" is the true class; key="hint" is the class a request looks like, which is
    wrong for the misclassified requests of the _m<percent> scenarios.

    Model: a request of route r takes kappa_r / v_p on server p. Only requests that found
    the server idle teach, since their response time is service time alone:
      kappa_r  <- mean of rt * v_p                       (cost at speed 1)
      v_p      =  mean of kappa_r / mean of rt           (ratio of means: stable, unlike a
                                                           mean of kappa_r / rt)
    with exponential averages of weight alpha. This has a consistent fixed point up to a
    common scale, which predictions kappa / v do not depend on. Outstanding work W_p is
    the sum of the predictions of the requests in progress on p; a request goes to the
    server with the least W_p + kappa_r / v_p (ties: fewest connections). Unknown routes
    get the average cost over all requests.

    Robustness (found on processor-sharing and 4-worker backends, where seeds ran away):
    - a server without a speed sample gets the geometric mean of the learned speeds, on
      their common scale (a default of 1.0 made such servers look 5-10x too fast);
    - a server's first sample moves its speed by about alpha, as any later one (setting it
      from one sample made a server look 36x too fast, and a flooded server is never idle
      again to correct it);
    - calibration: per server, the ratio of measured to predicted delay over every
      completion, idle or not, scales its predictions. A server slower than predicted
      gets less traffic even if it never teaches its speed again. It averages slowly
      (cal_alpha 0.002, about 500 responses): at 0.02 the factor was noisy enough to cost
      a third of the gain (results/cost_cal.json).
    Per server this needs 5 numbers; per route 1.
    """

    def __init__(self, alpha=0.02, key="class", cal_alpha=None):
        assert key in ("class", "hint")
        self.alpha, self.key_mode = alpha, key
        self.cal_alpha = alpha if cal_alpha is None else cal_alpha
        self.name = (f"learned_cost a={alpha:g}" + (" key=hint" if key == "hint" else "")
                     + (f" cal={self.cal_alpha:g}" if cal_alpha is not None else ""))
        self.kappa = {}          # route -> cost at speed 1, ms
        self.kappa_all = None    # average cost over all idle samples
        self.num, self.den = {}, {}   # per peer: averages of kappa_r and of rt
        self.work = {}           # per peer: predicted work in progress, ms
        self.gnum, self.gden = {}, {}  # per peer: averages of measured and predicted delay

    def key(self, req):
        return round(req.est_cost, 9) if self.key_mode == "hint" else req.cls

    def _default_speed(self):
        vs = [self.num[i] / self.den[i] for i in self.den if self.den[i] > 0]
        return math.exp(sum(math.log(v) for v in vs) / len(vs)) if vs else 1.0

    def speed(self, p):
        d = self.den.get(p.idx)
        return self.num[p.idx] / d if d else self._default_speed()

    def calibration(self, p):
        d = self.gden.get(p.idx)
        return self.gnum[p.idx] / d if d else 1.0

    def predict(self, p, route):
        k = self.kappa.get(route, self.kappa_all)
        return 0.0 if k is None else k / self.speed(p)

    def select(self, sim, peers, req, now):
        r = self.key(req)
        return min(peers, key=lambda p: (self.calibration(p) * (self.work.get(p.idx, 0.0)
                                                                + self.predict(p, r)),
                                         p.conns, p.idx))

    def on_dispatch(self, sim, p, req, now):
        r = self.key(req)
        pred = self.predict(p, r)
        req.lt_state = (r, pred, self.work.get(p.idx, 0.0) + pred)   # route, own, delay
        req.n_at_dispatch = p.conns
        self.work[p.idx] = self.work.get(p.idx, 0.0) + pred
        p.conns += 1

    def on_done(self, sim, p, req, now, rt):
        r, pred, delay = req.lt_state
        self.work[p.idx] -= pred
        p.conns -= 1
        if delay > 0:
            i, a = p.idx, self.cal_alpha
            if i not in self.gden:
                self.gnum[i] = self.gden[i] = delay
            self.gnum[i] += a * (rt - self.gnum[i])
            self.gden[i] += a * (delay - self.gden[i])
        if req.n_at_dispatch or rt <= 0:
            return
        a, v = self.alpha, self.speed(p)
        k_old = self.kappa.get(r)
        x = rt * v
        self.kappa[r] = x if k_old is None else k_old + a * (x - k_old)
        self.kappa_all = x if self.kappa_all is None else self.kappa_all + a * (x - self.kappa_all)
        if k_old is not None:
            if p.idx not in self.den:                   # start at the default speed
                self.num[p.idx], self.den[p.idx] = k_old, k_old / v
            self.num[p.idx] += a * (k_old - self.num[p.idx])
            self.den[p.idx] += a * (rt - self.den[p.idx])


register("learned_cost", lambda: LearnedCost(alpha=0.02, cal_alpha=0.002))
register("learned_cost_a05", lambda: LearnedCost(alpha=0.05, cal_alpha=0.002))
register("learned_cost_hint", lambda: LearnedCost(alpha=0.02, key="hint", cal_alpha=0.002))
for _c in (0.005, 0.002):
    register(f"learned_cost_c{_c:g}", lambda c=_c: LearnedCost(alpha=0.02, cal_alpha=c))
    register(f"learned_cost_hint_c{_c:g}", lambda c=_c: LearnedCost(alpha=0.02, key="hint", cal_alpha=c))
register("least_work_w", lambda: LeastWork())
register("least_work_ls", lambda: LeastWork(learn_speed=True))
register("sita", lambda: SITA())

BALS = ["least_conn", "p123_b5_s3", "least_work_w", "least_work_ls", "sita", "oracle"]
UTILS = (0.7, 0.85)


def scen_list():
    out = []
    for wl in ("bimodal", "api3"):
        for g in GROUPS:
            out.append(f"cr_{wl}_{g}")
    return out


def jobs():
    J = []
    for sc in scen_list():
        caps = GROUPS[sc.split("_")[2]]
        w8 = [int(c * 4) for c in caps]                 # integer weights = capacity
        for u in UTILS:
            for k in range(1, 11):
                for b in BALS:
                    kw = dict(weights=w8) if b == "least_work_w" else {}
                    J.append(dict(scenario=sc, util=u, balancer=b, seed=k, n=200_000, **kw))
                # prediction quality, on the cost-aware balancer with configured capacity
                for sigma in (0.5, 1.0):
                    J.append(dict(scenario=sc, util=u, balancer="least_work_w", seed=k,
                                  n=200_000, sigma=sigma, weights=w8, tag=f"s{sigma}"))
                J.append(dict(scenario=sc + "_m10", util=u, balancer="least_work_w", seed=k,
                              n=200_000, weights=w8, tag="m10"))
    return J


LEARNED = ["learned_cost", "learned_cost_a05"]


def jobs_learned():
    """No hint: costs learned per route. Same seeds as jobs(), so paired with its rows."""
    J = []
    for sc in scen_list():
        for u in UTILS:
            for k in range(1, 11):
                for b in LEARNED:
                    J.append(dict(scenario=sc, util=u, balancer=b, seed=k, n=200_000))
                J.append(dict(scenario=sc + "_m10", util=u, balancer="learned_cost_hint", seed=k,
                              n=200_000, tag="m10"))
    return J


ROBUST_SCEN = ([f"cr_{wl}_eq4" for wl in WORKLOADS]
               + [f"cr_{wl}_{g}{k}" for wl in WORKLOADS for g in ("het4", "het8")
                  for k in ("", "_ps", "_c4", "_mix")])
ROBUST_BALS = ["least_conn", "p123_b5_s3", "least_work_w", "learned_cost", "oracle"]


def jobs_robust():
    J = []
    for sc in ROBUST_SCEN:
        caps = GROUPS[sc.split("_")[2]]
        w8 = [int(c * 4) for c in caps]
        for u in UTILS:
            for k in range(1, 11):
                for b in ROBUST_BALS:
                    kw = dict(weights=w8) if b == "least_work_w" else {}
                    J.append(dict(scenario=sc, util=u, balancer=b, seed=k, n=200_000, **kw))
    return J


def report_robust(md):
    rows = common.load("cost_robust.json")
    by = {}
    for r in rows:
        by.setdefault((r["scenario"], r["util"], r["balancer"]), {})[r["seed"]] = r["p99"]
    cols = ["least_conn", "least_work_w", "learned_cost", "oracle"]
    md += ["## Robustness: many routes and other kinds of backend", "",
           "`zipf50`: 50 routes, popularity proportional to 1/rank (the rarest gets 0.2 % of the "
           "traffic), mean cost log-uniform between 1 and 200 ms. Backend kinds, at the same "
           "capacity per server: `_ps` processor sharing, `_c4` 4 workers at 1/4 speed, `_mix` "
           "FCFS, PS, FCFS x4 and PS x4 in turn. `least_work_w` is told the exact cost and "
           "capacity; `learned_cost` is told neither. p99 change vs patched least_time.", "",
           "| scenario | load | " + " | ".join(cols) + " |", "| --- | --- |" + " --- |" * len(cols)]
    summ = {}
    for sc in ROBUST_SCEN:
        for u in UTILS:
            base = by[(sc, u, "p123_b5_s3")]
            md.append(f"| {sc[3:]} | {u} | " + " | ".join(
                fmt(ratio(by[(sc, u, b)], base)) for b in cols) + " |")
            kind = next((k for k in ("_ps", "_c4", "_mix") if sc.endswith(k)), "fcfs")
            for b in ("least_work_w", "learned_cost"):
                summ.setdefault((b, kind), []).append((ratio(by[(sc, u, b)], base)[0], f"{sc[3:]}@{u}"))
    md += ["", "| balancer | backends | geometric mean | best | worst |", "| --- | --- | --- | --- | --- |"]
    for (b, kind), rs in summ.items():
        g = math.exp(mean(math.log(r) for r, _ in rs))
        lo, hi = min(rs), max(rs)
        md.append(f"| {b} | {kind.strip('_')} | {(g - 1) * 100:+.1f} % | {(lo[0] - 1) * 100:+.0f} % ({lo[1]}) | "
                  f"{(hi[0] - 1) * 100:+.0f} % ({hi[1]}) |")
    md.append("")


CAL_SCEN = (["cr_bimodal_het4", "cr_bimodal_het8", "cr_api3_het4", "cr_api3_het8", "cr_zipf50_het8"]
            + [f"cr_zipf50_{g}{k}" for g in ("het4", "het8") for k in ("_ps", "_c4", "_mix")]
            + ["cr_bimodal_het8_c4", "cr_api3_het8_c4", "cr_api3_het4_mix"])


def jobs_cal():
    """Calibration speed: 0.02 (= alpha) vs slower, on the clean cells and the ones that
    ran away before the fixes, plus the misclassified keys."""
    J = []
    for sc in CAL_SCEN:
        for u in UTILS:
            for k in range(1, 11):
                for c in ("", "_c0.005", "_c0.002"):
                    J.append(dict(scenario=sc, util=u, balancer="learned_cost" + c, seed=k, n=200_000))
                if sc in scen_list():
                    for c in ("", "_c0.005", "_c0.002"):
                        J.append(dict(scenario=sc + "_m10", util=u, balancer="learned_cost_hint" + c,
                                      seed=k, n=200_000, tag="m10"))
    return J


def report_learned(md, by, byc):
    md += ["## Learning the costs instead of being told (`learned_cost`)", "",
           "`least_work_w` above is told each request's expected cost (by the application) and "
           "each server's capacity (by the operator). `learned_cost` is told neither: it learns "
           "a cost per route and a speed per server from the response times of requests that "
           "found their server idle (see `LearnedCost`). The route is the request's class; "
           "with 10 % misclassified, the learned table itself is built from the wrong routes "
           "for those requests.", "",
           "| scenario | load | told: exact hint + capacity | learned, alpha 0.02 | "
           "learned, alpha 0.05 | told, 10 % misclassified | learned, 10 % misclassified | "
           "learned: cheap / costly class | oracle |",
           "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for sc in scen_list():
        ncls = len(WORKLOADS[sc.split("_")[1]])
        for u in UTILS:
            base = by[(sc, u, "p123_b5_s3")]
            cells = [fmt(ratio(by[(sc, u, b)], base)) for b in
                     ("least_work_w", "learned_cost", "learned_cost_a05", "least_work_w@m10",
                      "learned_cost_hint@m10")]
            cheap = ratio(byc[(sc, u, "learned_cost", "0")], byc[(sc, u, "p123_b5_s3", "0")])
            dear = ratio(byc[(sc, u, "learned_cost", str(ncls - 1))],
                         byc[(sc, u, "p123_b5_s3", str(ncls - 1))])
            cells.append(f"{(cheap[0] - 1) * 100:+.0f} % / {(dear[0] - 1) * 100:+.0f} %")
            cells.append(fmt(ratio(by[(sc, u, "oracle")], base)))
            md.append(f"| {sc[3:]} | {u} | " + " | ".join(cells) + " |")
    md.append("")


def key(r):
    sc = r["scenario"].replace("_m10", "")
    b = r["balancer"] + ("" if "tag" not in r else "@" + r["tag"])
    return sc, r["util"], b


def report():
    rows = common.load("cost_routing.json")
    learned = os.path.exists(os.path.join(common.RESULTS, "cost_learned.json"))
    if learned:
        rows = rows + common.load("cost_learned.json")
    by, byc = {}, {}
    for r in rows:
        k = key(r)
        by.setdefault(k, {})[r["seed"]] = r["p99"]
        for c, v in r["by_class"].items():
            byc.setdefault(k + (c,), {})[r["seed"]] = v["p99"]
    cols = ["least_conn", "least_work_w", "least_work_ls", "sita", "oracle"]
    md = ["# Exploration #2: routing by request cost", "",
          "See the docstring of `sim/explore/cost_routing.py` for the setup. "
          "10 paired seeds × 200,000 requests. All figures are p99 change vs patched "
          "least_time (`bucket=5ms smooth=3s`), geometric mean with 95 % CI.", "",
          "## Overall p99", "",
          "| scenario | load | " + " | ".join(cols) + " |",
          "| --- | --- |" + " --- |" * len(cols)]
    for sc in scen_list():
        for u in UTILS:
            base = by[(sc, u, "p123_b5_s3")]
            md.append(f"| {sc[3:]} | {u} | " + " | ".join(fmt(ratio(by[(sc, u, b)], base)) for b in cols) + " |")
    md += ["", "## p99 by request class (cheapest class / costliest class)", "",
           "Overall p99 is dominated by the costly class (5-10 % of requests). The cheap "
           "requests are most of the traffic, so their p99 is shown separately.", "",
           "| scenario | load | " + " | ".join(cols) + " |",
           "| --- | --- |" + " --- |" * len(cols)]
    for sc in scen_list():
        ncls = len(WORKLOADS[sc.split("_")[1]])
        for u in UTILS:
            cells = []
            for b in cols:
                cheap = ratio(byc[(sc, u, b, "0")], byc[(sc, u, "p123_b5_s3", "0")])
                dear = ratio(byc[(sc, u, b, str(ncls - 1))], byc[(sc, u, "p123_b5_s3", str(ncls - 1))])
                cells.append(f"{(cheap[0] - 1) * 100:+.0f} % / {(dear[0] - 1) * 100:+.0f} %")
            md.append(f"| {sc[3:]} | {u} | " + " | ".join(cells) + " |")
    md += ["", "## How good must the cost hint be? (`least_work_w`)", "",
           "| scenario | load | exact class mean | noise σ=0.5 | noise σ=1 | 10 % misclassified |",
           "| --- | --- | --- | --- | --- | --- |"]
    for sc in scen_list():
        for u in UTILS:
            base = by[(sc, u, "p123_b5_s3")]
            md.append(f"| {sc[3:]} | {u} | " + " | ".join(
                fmt(ratio(by[(sc, u, b)], base)) for b in
                ("least_work_w", "least_work_w@s0.5", "least_work_w@s1.0", "least_work_w@m10")) + " |")
    md.append("")
    if learned:
        report_learned(md, by, byc)
    if os.path.exists(os.path.join(common.RESULTS, "cost_robust.json")):
        report_robust(md)
    out = os.path.join(common.RESULTS, "cost_routing.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


def figs():
    """results/figs/cost_routing-{light,dark}.png"""
    import charts
    rows = common.load("cost_robust.json")
    by = {}
    for r in rows:
        by.setdefault((r["scenario"], r["util"], r["balancer"]), {})[r["seed"]] = r["p99"]
    kinds = [("fcfs", "one request at a time"), ("_ps", "processor sharing"),
             ("_c4", "4 workers per server"), ("_mix", "a mix of all three")]
    series = []
    for b, name, slot in (("least_work_w", "told each request's cost and each server's capacity", 1),
                          ("learned_cost", "learned from response times (no hint)", 0)):
        vals = []
        for k, _ in kinds:
            scs = [sc for sc in ROBUST_SCEN
                   if (sc.endswith(k) if k != "fcfs" else not sc.endswith(("_ps", "_c4", "_mix")))]
            rs = [ratio(by[(sc, u, b)], by[(sc, u, "p123_b5_s3")])[0] for sc in scs for u in UTILS]
            vals.append((math.exp(mean(math.log(r) for r in rs)) - 1) * 100)
        series.append((name, vals, slot))

    def fig(t):
        return charts.grouped_bars(
            t, "Routing by learned request cost",
            ["Least outstanding work, with route costs and server speeds learned from response times,",
             "vs being told them. p99 vs patched least_time; 3 workloads (up to 50 routes), 70 and 85 % load."],
            [k[1] for k in kinds], series, "p99 change vs patched least_time (geometric mean)",
            label_series=[0, 1], label_w=190)
    charts.render(fig, "cost_routing", os.path.join(common.RESULTS, "figs"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_jobs_ext(jobs(), "cost_routing.json")
        report()
    elif cmd == "robust":
        only = sys.argv[2:]          # e.g. "learned_cost": rerun those rows, keep the others
        J = [j for j in jobs_robust() if not only or j["balancer"] in only]
        if only:
            keep = [r for r in common.load("cost_robust.json") if r["balancer"] not in only]
            new = run_jobs_ext(J, "cost_robust_part.json", label="cost_robust")
            with open(os.path.join(common.RESULTS, "cost_robust.json"), "w") as f:
                json.dump(keep + new, f)
            os.remove(os.path.join(common.RESULTS, "cost_robust_part.json"))
        else:
            run_jobs_ext(J, "cost_robust.json", label="cost_robust")
        report()
    elif cmd == "cal":
        rows = run_jobs_ext(jobs_cal(), "cost_cal.json", label="cost_cal")
        by = {}
        for r in rows:
            by.setdefault((r["scenario"], r["util"], r["balancer"]), {})[r["seed"]] = r["p99"]
        base = {}
        for r in common.load("cost_robust.json") + common.load("cost_routing.json"):
            if r["balancer"] == "p123_b5_s3":
                base[(r["scenario"], r["util"])] = base.get((r["scenario"], r["util"]), {})
                base[(r["scenario"], r["util"])][r["seed"]] = r["p99"]
        for c in ("", "_c0.005", "_c0.002"):
            rs = [(ratio(v, base[(sc.replace("_m10", ""), u)])[0], f"{sc}@{u}")
                  for (sc, u, b), v in by.items() if b.endswith("cost" + c) or b.endswith("hint" + c)]
            g = math.exp(mean(math.log(r) for r, _ in rs))
            print(f"cal{c or '_0.02'}: geomean {(g - 1) * 100:+.1f} %, worst {(max(rs)[0] - 1) * 100:+.0f} % ({max(rs)[1]})")
    elif cmd == "learned":
        run_jobs_ext(jobs_learned(), "cost_learned.json", label="cost_learned")
        report()
    elif cmd == "figs":
        figs()
    elif cmd == "report":
        report()
    elif cmd == "smoke":
        for b in BALS:
            kw = dict(weights=[8, 4, 4, 2]) if b == "least_work_w" else {}
            r = common.run_job_ext(dict(scenario="cr_api3_het4", util=0.85, balancer=b,
                                        seed=1, n=20000, **kw))
            print(f"{b:14s} p99 {r['p99']:7.1f}  by class " +
                  " ".join(f"{c}:{v['p99']:.0f}" for c, v in sorted(r['by_class'].items())))
