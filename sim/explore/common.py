"""Shared helpers for the exploration branches (sim/explore/*).

Importing this module:
  - adds robustness scenarios to experiments.scen() (backends that share their
    CPU between requests, backends with several workers, and a mixed group),
  - adds reference balancers to experiments.BAL ("sed_true"),
so that experiments.run_job() can run them. Import it at the top level of the
experiment script: with the "spawn" start method (macOS), worker processes
re-run the main script's top level, which re-applies the additions.
"""
import json
import math
import os
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.dirname(HERE)
RESULTS = os.path.join(os.path.dirname(SIM), "results")
sys.path.insert(0, SIM)

import experiments                                     # noqa: E402
from lbsim import Backend, RoundRobin, exp_workload    # noqa: E402

SPEEDS8 = [2, 2, 1, 1, 1, 1, 0.5, 0.5]
SPEEDS3 = [1.0, 1 / 3, 2 / 15]                          # fast3: 2/6/15 ms


def _extra(name):
    """Robustness scenarios. Capacity of a backend is workers x speed."""
    if name == "hetero8_ps":     # hetero8, processor sharing
        return dict(backends=lambda: [Backend(i, 1, s, "ps") for i, s in enumerate(SPEEDS8)],
                    workload=lambda: exp_workload(10.0))
    if name == "hetero8_c4":     # hetero8, 4 workers per backend, same speed per worker
        return dict(backends=lambda: [Backend(i, 4, s) for i, s in enumerate(SPEEDS8)],
                    workload=lambda: exp_workload(10.0))
    if name == "fast3_ps":
        return dict(backends=lambda: [Backend(i, 1, s, "ps") for i, s in enumerate(SPEEDS3)],
                    workload=lambda: exp_workload(2.0))
    if name == "fast3_c4":       # fast3 capacities, but 4 workers at 1/4 the speed each
        return dict(backends=lambda: [Backend(i, 4, s / 4) for i, s in enumerate(SPEEDS3)],
                    workload=lambda: exp_workload(2.0))
    if name == "mixed4":         # every kind of backend in one group
        def bks():
            return [Backend(0, 1, 2.0, "fcfs"), Backend(1, 1, 1.0, "ps"),
                    Backend(2, 4, 0.25, "fcfs"), Backend(3, 4, 0.125, "ps")]
        return dict(backends=bks, workload=lambda: exp_workload(10.0))
    return None


EXTRA_DESC = {
    "hetero8_ps": "hetero8 with processor-sharing backends",
    "hetero8_c4": "hetero8 with 4 workers per backend",
    "fast3_ps": "fast3 with processor-sharing backends",
    "fast3_c4": "fast3 capacities, 4 workers per backend at ¼ the speed",
    "mixed4": "4 backends of capacity 2,1,1,½: FCFS, PS, FCFS×4 workers, PS×4 workers",
}

_orig_scen = experiments.scen


def _scen(name, sigma=0.0):
    return _extra(name) or _orig_scen(name, sigma)


experiments.scen = _scen


class SEDTrue(RoundRobin):
    """Shortest expected delay with the TRUE capacities: (conns + 1) / capacity.
    A reference, not a balancer nginx can implement: it knows server speeds."""
    name = "sed_true"

    def select(self, sim, peers, req, now):
        def cap(p):
            b = sim.backends[p.idx]
            return b.c * b.speed
        return min(peers, key=lambda p: ((p.conns + 1) / cap(p), sim.rng.random()))


experiments.BAL["sed_true"] = lambda: SEDTrue()


class PerWorker:
    """One balancer instance per worker process, so the state a balancer keeps in itself
    (rather than in PeerState) stays private to each worker, as in nginx when the
    balancer has no shared memory. PeerState (conns, weights) follows Sim(shared=...):
    shared=False is an upstream without `zone`, shared=True an upstream with `zone`
    (nginx's own peer state shared) whose balancer still learns per worker."""

    def __init__(self, factory):
        self.factory = factory
        self.inst = {}
        self.name = factory().name + " per worker"

    def _b(self, req):
        b = self.inst.get(req.worker)
        if b is None:
            b = self.inst[req.worker] = self.factory()
        return b

    def select(self, sim, peers, req, now):
        return self._b(req).select(sim, peers, req, now)

    def on_dispatch(self, sim, p, req, now):
        self._b(req).on_dispatch(sim, p, req, now)

    def on_done(self, sim, p, req, now, rt):
        self._b(req).on_done(sim, p, req, now, rt)


def register(name, factory):
    experiments.BAL[name] = factory


def p99_of(r):
    """p99 over the slow window for slowdown scenarios, else overall."""
    if "windows" in r:
        return r["windows"]["during"]["p99"]
    return r["p99"]


def _map_progress(fn, jobs, procs, label):
    """pool.imap keeps job order; prints "label: done/total (elapsed)" to stderr about every 5 %."""
    import time
    t0, rows, total = time.time(), [], len(jobs)
    step = max(1, total // 20)
    with Pool(procs) as pool:
        for i, r in enumerate(pool.imap(fn, jobs, chunksize=1), 1):
            rows.append(r)
            if i % step == 0 or i == total:
                el = time.time() - t0
                eta = el / i * (total - i)
                print(f"{label}: {i}/{total} ({el / 60:.1f} min, eta {eta / 60:.1f} min)",
                      file=sys.stderr, flush=True)
    return rows


def run_jobs(jobs, out, procs=None, label=None):
    """Run experiments.run_job over jobs in parallel; write JSON to results/out."""
    rows = _map_progress(experiments.run_job, jobs, procs, label or out)
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, out), "w") as f:
        json.dump(rows, f)
    return rows


def load(out):
    with open(os.path.join(RESULTS, out)) as f:
        return json.load(f)


def index(rows, metric=p99_of):
    """{(scenario, util, balancer): {seed: value}}"""
    by = {}
    for r in rows:
        by.setdefault((r["scenario"], r["util"], r["balancer"]), {})[r["seed"]] = metric(r)
    return by


def t95(n):
    return {2: 12.71, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 8: 2.365,
            10: 2.262, 20: 2.093}.get(n, 1.96)


def ratio(a, b):
    """Geometric-mean ratio a/b over common seeds, with 95 % CI: (g, lo, hi)."""
    seeds = sorted(set(a) & set(b))
    logs = [math.log(a[s] / b[s]) for s in seeds]
    n = len(logs)
    m = sum(logs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in logs) / (n - 1)) if n > 1 else 0.0
    h = t95(n) * sd / math.sqrt(n)
    return math.exp(m), math.exp(m - h), math.exp(m + h)


def fmt(r):
    g, lo, hi = r
    return f"{(g - 1) * 100:+.0f} % [{(lo - 1) * 100:+.0f}, {(hi - 1) * 100:+.0f}]"


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs)


# --------------------------------------------------------------------------
# Extended runner: per-class latency, rejections, pluggable Sim subclass
# --------------------------------------------------------------------------

from lbsim import Sim                                   # noqa: E402

SIMS = {"default": Sim}
SCEN_HOOKS = []          # functions name, sigma -> scenario dict or None


def add_scenarios(fn):
    SCEN_HOOKS.append(fn)
    return fn


def _scen_hooked(name, sigma=0.0):
    for fn in SCEN_HOOKS:
        s = fn(name, sigma)
        if s:
            return s
    return _scen(name, sigma)


experiments.scen = _scen_hooked


def _quantile(xs, q):
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def run_job_ext(job):
    """experiments.run_job plus: job["sim"] picks a Sim subclass from SIMS;
    results gain per-class p50/p99 ("by_class") and "rejected" (fraction)."""
    sc = experiments.scen(job["scenario"], job.get("sigma", 0.0))
    bks = sc["backends"]()
    wl = sc["workload"]()
    rate = experiments.rate_for(job["util"], bks, wl)
    events = []
    windows = None
    if sc.get("slowdown"):
        dur = job["n"] / rate * 1000.0
        t1, t2 = dur * 0.4, dur * 0.4 + 20000.0
        events = [(t1, lambda sim, now: sim.backends[0].set_speed(sim, 0.25, now)),
                  (t2, lambda sim, now: sim.backends[0].set_speed(sim, 1.0, now))]
        windows = {"during": (t1, t2)}
    cls = SIMS[job.get("sim", "default")]
    sim = cls(bks, experiments.get_bal(job["balancer"]), wl, rate, job["n"],
              workers=job.get("workers", 1), shared=job.get("shared", True),
              weights=job.get("weights"), seed=job["seed"], events=events,
              **job.get("sim_kw", {}))
    res = sim.run()
    res["measured"] = res["n"]          # res.update(job) below replaces "n" by the requested count
    d = sorted(sim.done)
    d = d[int(len(d) * sim.warmup):]
    byc = {}
    for x in d:
        byc.setdefault(x[3], []).append(x[1])
    res["by_class"] = {str(c): {"n": len(v), "p50": _quantile(sorted(v), 0.5),
                                "p99": _quantile(sorted(v), 0.99)}
                       for c, v in byc.items()}
    if job.get("split"):
        # requests per class and server (after warm-up), and the server speeds
        sp = {}
        for x in d:
            c = sp.setdefault(str(x[3]), {})
            c[str(x[2])] = c.get(str(x[2]), 0) + 1
        res["by_class_peer"] = sp
        res["speeds"] = [b.speed for b in sim.backends]
    res["rejected"] = getattr(sim, "rejected", 0) / job["n"]
    if windows:
        sim.warmup = 0.0
        res["windows"] = {k: sim.summary(v) for k, v in windows.items()}
    res.update(job)
    res["rate"] = rate
    return res


def run_jobs_ext(jobs, out, procs=None, label=None):
    rows = _map_progress(run_job_ext, jobs, procs, label or out)
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, out), "w") as f:
        json.dump(rows, f)
    return rows
