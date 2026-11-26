#!/usr/bin/env python3
"""Exploration #3: several worker processes, with and without `zone`.

Without `zone`, each nginx worker process keeps its own copy of the
balancer state: its own count of requests in progress per server and its own
average response times. A worker only sees the requests it handled itself, so
"least connections" and "least time" are decided on a fraction of the
picture. With `zone`, all workers share one state in shared memory.

The simulator sends each arrival to a uniformly random worker (the kernel's
reuseport hashing spreads connections roughly this way). The real benchmark
(bench/linux/zone_matrix.sh) also covers the default without reuseport.

  python3 sim/explore/zone_workers.py run      # 10 seeds x 200k
  python3 sim/explore/zone_workers.py report   # results/zone_workers_sim.md
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from common import fmt, ratio, run_jobs                # noqa: E402

SCEN = ["fast3", "hetero8", "homog8", "bimodal4_het", "slowdown"]
UTILS = (0.7, 0.85)
BALS = ["least_conn", "lt", "p123_b5_s3", "rr"]
CONFIGS = [("1 worker", 1, True), ("4 workers + zone", 4, True),
           ("4 workers, no zone", 4, False), ("8 workers, no zone", 8, False)]


def jobs():
    return [dict(scenario=sc, util=u, balancer=b, seed=k, n=200_000, workers=w, shared=sh,
                 config=name)
            for sc in SCEN for u in UTILS for b in BALS for name, w, sh in CONFIGS
            for k in range(1, 11)]


def report():
    rows = common.load("zone_workers_sim.json")
    by = {}
    for r in rows:
        by.setdefault((r["scenario"], r["util"], r["balancer"], r["config"]), {})[r["seed"]] = \
            common.p99_of(r)
    md = ["# Exploration #3 (simulation): worker processes with and without `zone`", "",
          "10 paired seeds × 200,000 requests. Each arrival goes to a random worker. "
          "p99 change relative to the same balancer with 1 worker (where `zone` makes no "
          "difference); 95 % CI. For slowdown, p99 is over the 20 s slow window.", ""]
    for b in BALS:
        md += [f"## {b}", "", "| scenario | load | " + " | ".join(c[0] for c in CONFIGS[1:]) + " |",
               "| --- | --- |" + " --- |" * (len(CONFIGS) - 1)]
        for sc in SCEN:
            for u in UTILS:
                base = by[(sc, u, b, "1 worker")]
                md.append(f"| {sc} | {u} | " + " | ".join(
                    fmt(ratio(by[(sc, u, b, c[0])], base)) for c in CONFIGS[1:]) + " |")
        md.append("")
    md += ["## Absolute p99 (ms), 4 workers", "",
           "| scenario | load | " + " | ".join(f"{b}: zone / no zone" for b in BALS) + " |",
           "| --- | --- |" + " --- |" * len(BALS)]
    for sc in SCEN:
        for u in UTILS:
            cells = [f"{common.mean(by[(sc, u, b, '4 workers + zone')].values()):.0f} / "
                     f"{common.mean(by[(sc, u, b, '4 workers, no zone')].values()):.0f}" for b in BALS]
            md.append(f"| {sc} | {u} | " + " | ".join(cells) + " |")
    md.append("")
    out = os.path.join(common.RESULTS, "zone_workers_sim.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run_jobs(jobs(), "zone_workers_sim.json")
        report()
    else:
        report()
