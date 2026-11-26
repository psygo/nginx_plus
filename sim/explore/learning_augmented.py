#!/usr/bin/env python3
"""Learning-augmented load balancing: a learned cost model, with a floor.

Idea (from the discussion of AI-driven balancing): let a learned model improve
routing, but only inside choices the classic algorithm already considers good,
so that a bad model cannot do much harm. In the literature this is a
learning-augmented algorithm ("algorithms with predictions"): good when the
predictions are good (consistency), bounded loss when they are bad (robustness).

Requests have features a proxy can see: a route (think URL pattern) and a size
(think Content-Length or a query parameter). True cost = route base cost x
size^gamma(route) x hidden noise. The model is trained offline on a separate
trace and frozen as a lookup table (route, size bucket) -> mean cost, so it is
deterministic at run time: the same request always gets the same prediction.

Predictors, all fed the same requests:
  model     the trained table
  route     route mean only (a hand-written map in nginx config could do this)
  noisy     model x lognormal noise, sigma = 1 (a poor model)
  random    a random route's mean (no information)
  inverted  the mean of the route with the opposite cost rank (adversarial)

Balancers, all on top of patched least_time (bucket=5ms smooth=3s):
  p123          the floor: least_time alone
  aug:E         shortlist = servers whose least_time score is within (1+E) of
                the best; among them, the least predicted outstanding work per
                unit of learned capacity. E=0: only least_time's own ties.
  ai_only       the same rule over all servers (no floor)
  oracle        true costs and speeds (ceiling)

  python3 sim/explore/learning_augmented.py run      # 10 seeds x 200k
  python3 sim/explore/learning_augmented.py report   # results/learning_augmented.md
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from common import add_scenarios, fmt, ratio, register, run_jobs_ext  # noqa: E402
from lbsim import Backend, LeastTimePatch              # noqa: E402

# route: (probability, base cost ms, size exponent gamma)
ROUTES = [(0.35, 1.0, 0.0), (0.25, 2.0, 0.3), (0.15, 5.0, 0.0),
          (0.13, 10.0, 0.5), (0.09, 30.0, 0.5), (0.03, 100.0, 0.5)]
SIZE_SIGMA = 0.7          # log-size spread
NOISE_SIGMA = 0.4         # cost variation no feature explains
NBUCKETS = 8
GROUPS = {"eq4": ([1, 1, 1, 1], "fcfs"), "het4": ([2, 1, 1, 0.5], "fcfs"),
          "het8": ([2, 2, 1, 1, 1, 1, 0.5, 0.5], "fcfs"),
          "het8ps": ([2, 2, 1, 1, 1, 1, 0.5, 0.5], "ps")}
PREDICTORS = ["model", "route", "noisy", "random", "inverted"]


def _route_mean(r):
    p, base, g = ROUTES[r]
    # E[size^g] for lognormal size = exp(g^2 sigma^2 / 2); E[noise] = exp(sigma^2/2)
    return base * math.exp(g * g * SIZE_SIGMA ** 2 / 2) * math.exp(NOISE_SIGMA ** 2 / 2)


def _bucket(log_size):
    z = log_size / SIZE_SIGMA                          # standard normal
    return max(0, min(NBUCKETS - 1, int((z + 2.0) / 4.0 * NBUCKETS)))


def _draw(rng):
    r = rng.choices(range(len(ROUTES)), [x[0] for x in ROUTES])[0]
    ls = rng.gauss(0, SIZE_SIGMA)
    noise = rng.gauss(0, NOISE_SIGMA)
    _, base, g = ROUTES[r]
    return r, ls, base * math.exp(g * ls + noise)


_MODEL = None


def model():
    """Train the lookup table on an independent trace (seed 999, 100k requests)."""
    global _MODEL
    if _MODEL is None:
        rng = random.Random(999)
        s, n = {}, {}
        for _ in range(100_000):
            r, ls, c = _draw(rng)
            k = (r, _bucket(ls))
            s[k] = s.get(k, 0.0) + c
            n[k] = n.get(k, 0) + 1
        _MODEL = {k: s[k] / n[k] for k in s}
    return _MODEL


def workload(pred):
    table = model()
    means = [_route_mean(r) for r in range(len(ROUTES))]
    order = sorted(range(len(ROUTES)), key=lambda r: means[r])
    inverse = {order[i]: order[-1 - i] for i in range(len(order))}
    prng = random.Random(4242)

    def w(rng):
        r, ls, c = _draw(rng)                          # same stream for every predictor
        if pred == "model":
            est = table.get((r, _bucket(ls)), means[r])
        elif pred == "route":
            est = means[r]
        elif pred == "noisy":
            est = table.get((r, _bucket(ls)), means[r]) * math.exp(prng.gauss(0, 1.0))
        elif pred == "random":
            est = means[prng.randrange(len(ROUTES))]
        elif pred == "inverted":
            est = means[inverse[r]]
        else:
            raise KeyError(pred)
        return r, c, est
    w.mean = sum(p * _route_mean(r) for r, (p, _, _) in enumerate(ROUTES))
    return w


@add_scenarios
def _ai(name, sigma):
    if not name.startswith("ai_"):
        return None
    _, grp, pred = name.split("_")
    caps, disc = GROUPS[grp]
    return dict(backends=lambda: [Backend(i, 1, c, disc) for i, c in enumerate(caps)],
                workload=lambda: workload(pred))


class Augmented(LeastTimePatch):
    def __init__(self, eps=0.0, floor=True, alpha=0.05):
        super().__init__(bucket=5, smooth=3000)
        self.eps, self.floor, self.alpha = eps, floor, alpha
        self.name = f"augmented eps={eps}" if floor else "ai_only"

    def select(self, sim, peers, req, now):
        if self.floor:
            etas = [self.eta(sim, p, now, self.bucket) / p.weight for p in peers]
            best = min(etas)
            cand = [p for p, e in zip(peers, etas) if e <= best * (1 + self.eps)]
        else:
            cand = peers
        return min(cand, key=lambda p: ((p.work + req.est_cost) / (p.speed_est or 1.0),
                                        p.conns, sim.rng.random()))

    def on_dispatch(self, sim, p, req, now):
        req.n_at_dispatch = p.work
        p.work += req.est_cost
        super().on_dispatch(sim, p, req, now)

    def on_done(self, sim, p, req, now, rt):
        p.work -= req.est_cost
        sp = (req.n_at_dispatch + req.est_cost) / max(1.0, rt)
        p.speed_est = sp if not p.speed_est else (1 - self.alpha) * p.speed_est + self.alpha * sp
        super().on_done(sim, p, req, now, rt)


EPS = (0.0, 0.25, 1.0)
for _e in EPS:
    register(f"aug_{_e:g}", lambda e=_e: Augmented(eps=e))
register("ai_only", lambda: Augmented(floor=False))
BALS = [f"aug_{e:g}" for e in EPS] + ["ai_only"]
UTILS = (0.7, 0.85)


def jobs():
    J = []
    for g in GROUPS:
        for u in UTILS:
            for k in range(1, 11):
                for b in ("p123_b5_s3", "least_conn", "oracle"):
                    J.append(dict(scenario=f"ai_{g}_model", util=u, balancer=b, seed=k, n=200_000))
                for pred in PREDICTORS:
                    for b in BALS:
                        J.append(dict(scenario=f"ai_{g}_{pred}", util=u, balancer=b, seed=k,
                                      n=200_000))
    return J


def report():
    rows = common.load("learning_augmented.json")
    by = {}
    for r in rows:
        g, pred = r["scenario"].split("_")[1:]
        b = r["balancer"]
        if b in ("p123_b5_s3", "least_conn", "oracle"):
            pred = "-"
        by.setdefault((g, r["util"], b, pred), {})[r["seed"]] = r["p99"]
    md = ["# Learning-augmented balancing: a learned cost model with a floor", "",
          "See the docstring of `sim/explore/learning_augmented.py`. 10 paired seeds × "
          "200,000 requests; every predictor sees the same requests. All figures are p99 "
          "change vs patched least_time (`bucket=5ms smooth=3s`), the floor.", ""]
    md += ["## References", "", "| group | load | least_conn | oracle |", "| --- | --- | --- | --- |"]
    for g in GROUPS:
        for u in UTILS:
            base = by[(g, u, "p123_b5_s3", "-")]
            md.append(f"| {g} | {u} | {fmt(ratio(by[(g, u, 'least_conn', '-')], base))} | "
                      f"{fmt(ratio(by[(g, u, 'oracle', '-')], base))} |")
    for pred in PREDICTORS:
        md += ["", f"## Predictor: {pred}", "",
               "| group | load | " + " | ".join(f"`{b}`" for b in BALS) + " |",
               "| --- | --- |" + " --- |" * len(BALS)]
        for g in GROUPS:
            for u in UTILS:
                base = by[(g, u, "p123_b5_s3", "-")]
                md.append(f"| {g} | {u} | " + " | ".join(
                    fmt(ratio(by[(g, u, b, pred)], base)) for b in BALS) + " |")
    # worst case per balancer across everything
    md += ["", "## Worst case per balancer (all groups, loads, predictors)", "",
           "| balancer | best cell | worst cell |", "| --- | --- | --- |"]
    for b in BALS:
        rs = []
        for g in GROUPS:
            for u in UTILS:
                for pred in PREDICTORS:
                    rs.append((ratio(by[(g, u, b, pred)], by[(g, u, "p123_b5_s3", "-")])[0], g, u, pred))
        lo, hi = min(rs), max(rs)
        md.append(f"| `{b}` | {(lo[0] - 1) * 100:+.0f} % ({lo[1]}@{lo[2]}, {lo[3]}) | "
                  f"{(hi[0] - 1) * 100:+.0f} % ({hi[1]}@{hi[2]}, {hi[3]}) |")
    md.append("")
    out = os.path.join(common.RESULTS, "learning_augmented.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_jobs_ext(jobs(), "learning_augmented.json")
        report()
    elif cmd == "report":
        report()
    elif cmd == "smoke":
        print("model buckets:", len(model()))
        for pred in PREDICTORS:
            for b in ["least_conn", "p123_b5_s3"] + BALS:
                r = common.run_job_ext(dict(scenario=f"ai_het4_{pred}", util=0.85, balancer=b,
                                            seed=1, n=20000))
                print(f"{pred:9s} {b:12s} p99 {r['p99']:8.1f}")
