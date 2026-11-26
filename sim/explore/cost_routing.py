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
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from common import add_scenarios, mean, ratio, fmt, register, run_jobs_ext  # noqa: E402
from lbsim import Backend, LeastWork, RoundRobin       # noqa: E402

WORKLOADS = {"bimodal": [(0.9, 2.0), (0.1, 80.0)],
             "api3": [(0.70, 1.0), (0.25, 10.0), (0.05, 200.0)]}
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
        if x.startswith("m"):
            mis = int(x[1:]) / 100
    caps = GROUPS[grp]
    return dict(backends=lambda: [Backend(i, 1, c) for i, c in enumerate(caps)],
                workload=lambda: classes_workload(WORKLOADS[wl], sigma, mis))


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


register("least_work_w", lambda: LeastWork())
register("least_work_ls", lambda: LeastWork(learn_speed=True))
register("sita", lambda: SITA())

BALS = ["least_conn", "p123_b5_s3", "least_work_w", "least_work_ls", "sita", "oracle"]
UTILS = (0.7, 0.85)


def scen_list():
    out = []
    for wl in WORKLOADS:
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


def key(r):
    sc = r["scenario"].replace("_m10", "")
    b = r["balancer"] + ("" if "tag" not in r else "@" + r["tag"])
    return sc, r["util"], b


def report():
    rows = common.load("cost_routing.json")
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
    out = os.path.join(common.RESULTS, "cost_routing.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_jobs_ext(jobs(), "cost_routing.json")
        report()
    elif cmd == "report":
        report()
    elif cmd == "smoke":
        for b in BALS:
            kw = dict(weights=[8, 4, 4, 2]) if b == "least_work_w" else {}
            r = common.run_job_ext(dict(scenario="cr_api3_het4", util=0.85, balancer=b,
                                        seed=1, n=20000, **kw))
            print(f"{b:14s} p99 {r['p99']:7.1f}  by class " +
                  " ".join(f"{c}:{v['p99']:.0f}" for c, v in sorted(r['by_class'].items())))
