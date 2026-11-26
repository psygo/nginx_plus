#!/usr/bin/env python3
"""Exploration #4: admission control and queueing at nginx.

Open-source nginx has one admission knob: `server ... max_conns=K`. A server
with K requests in progress is skipped; when every server is full the request
fails at once (502, "no live upstreams"). NGINX Plus also has `queue N
timeout=T`: requests that find every server full wait in nginx, and go to the
first server that frees a slot.

Queueing theory has a clear prediction here. Committing a request to one
server's queue early ("early binding") makes it wait behind whatever that
server got, even if another server frees up first. Holding requests in one
shared queue and dispatching on a free slot ("late binding") is how an M/M/c
system works, which is the best a dispatcher without request-size knowledge can
do. With max_conns equal to each backend's concurrency, nginx's queue turns the
group into exactly that.

Policies (all with patched least_time, bucket=5ms smooth=3s, choosing among
servers below max_conns):
  none        no limit (today's default)
  reject:K    max_conns=K, fail when every server is full (open-source nginx)
  queue:K:T   max_conns=K, wait in nginx up to T ms (NGINX Plus `queue`)

Metrics: p99 of served requests, and the fraction rejected or timed out.

  python3 sim/explore/admission.py run      # 10 seeds x 200k
  python3 sim/explore/admission.py report   # results/admission.md
"""
import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from common import SIMS, fmt, ratio, run_jobs_ext      # noqa: E402
from lbsim import Request, Sim                         # noqa: E402
import heapq                                           # noqa: E402


class AdmissionSim(Sim):
    def __init__(self, *a, policy="none", max_conns=0, timeout=0.0, **kw):
        super().__init__(*a, **kw)
        self.policy, self.max_conns, self.timeout = policy, max_conns, timeout
        self.rejected = 0
        self.q = deque()

    def _eligible(self, peers):
        if not self.max_conns:
            return peers
        return [p for p in peers if p.conns < self.max_conns]

    def _dispatch(self, x, peers, cand, now):
        p = self.balancer.select(self, cand, x, now)
        x.peer = p.idx
        x.dispatch = now
        self.balancer.on_dispatch(self, p, x, now)
        self.backends[p.idx].arrive(self, x, now)

    def complete(self, req, now):
        super().complete(req, now)
        peers = self.states[0]
        while self.q:
            cand = self._eligible(peers)
            if not cand:
                break
            x = self.q.popleft()
            if now - x.arrival > self.timeout:
                self.rejected += 1
                continue
            self._dispatch(x, peers, cand, now)

    def run(self):
        t = 0.0
        for rid in range(self.n):
            t += self.rng.expovariate(self.rate / 1000.0)
            cls, cost, est = self.workload(self.rng)
            r = Request(rid, t, cls, cost)
            r.est_cost = est
            self.push(t, "arrive", r)
        while self.heap:
            now, _, kind, x = heapq.heappop(self.heap)
            if kind == "arrive":
                x.worker = 0
                peers = self.states[0]
                cand = self._eligible(peers)
                if not cand:
                    if self.policy == "queue":
                        self.q.append(x)
                    else:
                        self.rejected += 1
                    continue
                self._dispatch(x, peers, cand, now)
            elif kind == "done":
                self.backends[x.peer].fcfs_done(self, x, now)
                self.complete(x, now)
            elif kind == "ps":
                b, gen = x
                b.ps_tick(self, gen, now)
            elif kind == "event":
                x(self, now)
        self.rejected += len(self.q)
        return self.summary()


SIMS["admission"] = AdmissionSim

SCEN = {"hetero8": 1, "hetero8_ps": 1, "hetero8_c4": 4, "bimodal4_het": 1, "slowdown": 1}
UTILS = (0.85, 0.95, 1.05)


def policies(c):
    """Policies for backends with concurrency c (workers per backend)."""
    P = [("none", {})]
    for k in (c, 2 * c, 4 * c):
        P.append((f"reject:{k}", dict(policy="reject", max_conns=k)))
    for k in (c, 2 * c):
        for t in (500, 60_000):
            P.append((f"queue:{k}:{t}", dict(policy="queue", max_conns=k, timeout=float(t))))
    return P


def jobs():
    J = []
    for sc, c in SCEN.items():
        for u in UTILS:
            for name, kw in policies(c):
                for k in range(1, 11):
                    J.append(dict(scenario=sc, util=u, balancer="p123_b5_s3", seed=k,
                                  n=200_000, sim="admission", sim_kw=kw, policy_name=name))
    return J


def report():
    rows = common.load("admission.json")
    by, rej = {}, {}
    for r in rows:
        k = (r["scenario"], r["util"], r["policy_name"])
        by.setdefault(k, {})[r["seed"]] = common.p99_of(r)
        rej.setdefault(k, []).append(r["rejected"])
    md = ["# Exploration #4: admission control and queueing at nginx", "",
          "See the docstring of `sim/explore/admission.py`. 10 paired seeds × 200,000 "
          "requests, patched least_time (`bucket=5ms smooth=3s`) in every case. "
          "`reject:K` is `max_conns=K` in open-source nginx; `queue:K:T` is NGINX Plus "
          "`queue` with `max_conns=K` and a timeout of T ms. For slowdown, p99 is over the "
          "20 s slow window. Load 1.05 is overload: more requests than the servers can "
          "handle.", ""]
    for sc, c in SCEN.items():
        md += [f"## {sc} (backend concurrency {c})", "",
               "| policy | " + " | ".join(f"load {u}: p99 ms / rejected" for u in UTILS) + " |",
               "| --- |" + " --- |" * len(UTILS)]
        for name, _ in policies(c):
            cells = []
            for u in UTILS:
                k = (sc, u, name)
                p = common.mean(by[k].values())
                cells.append(f"{p:,.0f} / {100 * common.mean(rej[k]):.2f} %")
            md.append(f"| `{name}` | " + " | ".join(cells) + " |")
        md += ["", "p99 change vs no limit:", "",
               "| policy | " + " | ".join(f"load {u}" for u in UTILS) + " |",
               "| --- |" + " --- |" * len(UTILS)]
        for name, _ in policies(c)[1:]:
            md.append(f"| `{name}` | " + " | ".join(
                fmt(ratio(by[(sc, u, name)], by[(sc, u, "none")])) for u in UTILS) + " |")
        md.append("")
    out = os.path.join(common.RESULTS, "admission.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


def figs():
    """results/figs/admission_hetero8-{light,dark}.png"""
    import charts
    rows = common.load("admission.json")
    by, rej = {}, {}
    for r in rows:
        k = (r["scenario"], r["util"], r["policy_name"])
        by.setdefault(k, {})[r["seed"]] = r["p99"]
        rej.setdefault(k, []).append(r["rejected"])
    pols = ["reject:1", "reject:2", "reject:4", "queue:1:500", "queue:2:500"]
    sc = "hetero8"

    def label(p):
        share = 100 * sum(rej[(sc, 0.95, p)]) / len(rej[(sc, 0.95, p)])
        return f"{p} · rejects {share:.1f} %" if share >= 0.05 else f"{p} · rejects none"
    series = [(f"{int(u * 100)} % load", [(ratio(by[(sc, u, p)], by[(sc, u, "none")])[0] - 1) * 100
                                          for p in pols], slot)
              for u, slot in ((0.85, 1), (0.95, 0))]

    def fig(t):
        return charts.grouped_bars(
            t, "Holding requests back shortens the tail, at a price",
            ["8 servers of unequal speed, patched least_time; p99 of the requests served, vs no limit.",
             "reject:K is max_conns=K; queue:K:T waits up to T ms (NGINX Plus). Share rejected at 95 % load."],
            [label(p) for p in pols], series, "p99 change vs no limit", label_series=1, label_w=260)
    charts.render(fig, "admission_hetero8", os.path.join(common.RESULTS, "figs"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_jobs_ext(jobs(), "admission.json")
        report()
    elif cmd == "figs":
        figs()
    elif cmd == "report":
        report()
    elif cmd == "smoke":
        for sc in ("hetero8", "hetero8_c4"):
            for name, kw in policies(SCEN[sc]):
                r = common.run_job_ext(dict(scenario=sc, util=0.95, balancer="p123_b5_s3", seed=1,
                                            n=20000, sim="admission", sim_kw=kw))
                print(f"{sc:11s} {name:16s} p99 {r['p99']:8.1f} rejected {100 * r['rejected']:.2f} %")
