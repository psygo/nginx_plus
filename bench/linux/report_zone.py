"""Report for bench/linux/zone_matrix.sh: worker processes with and without `zone`.

Reads results/linux_zone.jsonl, writes results/zone_workers_real.md.
Ratios are paired by repetition (same arrivals and costs).

  python3 bench/linux/report_zone.py
"""
import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES = os.path.join(ROOT, "results")
LABEL = {"least_conn": "least_conn", "lt": "least_time (stock)",
         "p123_b5_s3": "least_time bucket=5ms smooth=3s"}


def cfg(r):
    if r["workers"] == 1:
        return "1 worker"
    if r.get("zone"):
        return "4 workers + zone"
    return "4 workers, no zone" + ("" if r.get("reuseport", True) else ", no reuseport")


def disturbed(rows, key):
    """Runs the load generator could not pace: send lag p99 over 2 ms and over 3x the median
    lag of the other runs of the same cell. Some cells have a systematic lag of a few ms
    (slow3: about 7.7 ms in every run, at most 2 % of p99, identical for every variant);
    a disturbance shows up as one run far above its siblings."""
    import statistics
    cells = {}
    for r in rows:
        cells.setdefault(key(r), []).append(r["send_lag_p99"])
    med = {k: statistics.median(v) for k, v in cells.items()}
    return [r for r in rows if r["send_lag_p99"] > 2.0 and r["send_lag_p99"] > 3 * med[key(r)]]


CONFIGS = ["1 worker", "4 workers + zone", "4 workers, no zone",
           "4 workers, no zone, no reuseport"]


def ratio(a, b):
    reps = sorted(set(a) & set(b))
    logs = [math.log(a[k] / b[k]) for k in reps]
    n = len(logs)
    m = sum(logs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in logs) / (n - 1)) if n > 1 else 0
    t = {2: 12.71, 3: 4.303, 4: 3.182, 5: 2.776}.get(n, 2.0)
    h = t * sd / math.sqrt(n)
    return f"{(math.exp(m) - 1) * 100:+.0f} % [{(math.exp(m - h) - 1) * 100:+.0f}, {(math.exp(m + h) - 1) * 100:+.0f}]"


def main():
    rows = [json.loads(x) for x in open(os.path.join(RES, "linux_zone.jsonl")) if x.strip()]
    bad = disturbed(rows, lambda r: (r["scenario"], r["util"]))
    ids = {id(r) for r in bad}
    rows = [r for r in rows if id(r) not in ids]   # re-runs appear later in the file and win
    by, share = {}, {}
    for r in rows:
        k = (r["scenario"], r["util"], r["variant"], cfg(r))
        by.setdefault(k, {})[r["rep"]] = r["p99"]
        s = share.setdefault(k, {})
        for p, c in r["by_port"].items():
            s[p] = s.get(p, 0) + c
    scen = sorted({k[0] for k in by})
    md = ["# Worker processes with and without `zone` (real nginx, Linux)", "",
          "Same harness as `linux_pr.md`: open-loop Poisson load, FCFS Go backends, paired "
          "repetitions (identical arrivals and costs). nginx on 4 CPUs. Configurations, "
          "interleaved by repetition:", "",
          "- 1 worker (reference: one process sees everything);",
          "- 4 workers with `zone` (shared state), `reuseport`;",
          "- 4 workers without `zone`, `reuseport` (the kernel spreads connections);",
          "- 4 workers without `zone`, plain `listen` (nginx's default).", "",
          "The load generator keeps connections alive, as browsers and most clients do, so "
          "a connection stays with the worker that accepted it.", "",
          f"Disturbed runs excluded (send lag p99 over 2 ms and over 3x the cell median): {len(bad)}; "
          "each was re-run with the same seed, and a run still disturbed after its re-run is left "
          "out (that cell has one repetition fewer). The disturbances are pauses of the load "
          "generator itself (send lag up to 230 ms), not of nginx: with raw per-request timings, a "
          "slow second shows the load generator sending ~100 ms late.", "",
          "## p99 change vs 1 worker", "",
          "| scenario | load | balancer | " + " | ".join(CONFIGS[1:]) + " |",
          "| --- | --- | --- |" + " --- |" * 3]
    for sc in scen:
        for u in sorted({k[1] for k in by if k[0] == sc}):
            for v in LABEL:
                base = by.get((sc, u, v, "1 worker"))
                if not base:
                    continue
                cells = [ratio(by[(sc, u, v, c)], base) if (sc, u, v, c) in by else ""
                         for c in CONFIGS[1:]]
                md.append(f"| {sc} | {u} | {LABEL[v]} | " + " | ".join(cells) + " |")
    md += ["", "## Absolute p99 (ms, mean over repetitions) and share of requests per backend", ""]
    for sc in scen:
        for u in sorted({k[1] for k in by if k[0] == sc}):
            md += [f"### {sc} @ {u}", "", "| balancer | config | p99 | share (%) |",
                   "| --- | --- | --- | --- |"]
            for v in LABEL:
                for c in CONFIGS:
                    k = (sc, u, v, c)
                    if k not in by:
                        continue
                    p99 = sum(by[k].values()) / len(by[k])
                    s = share[k]
                    tot = sum(s.values()) or 1
                    sh = "/".join(f"{100 * s[p] / tot:.0f}" for p in sorted(s))
                    md.append(f"| {LABEL[v]} | {c} | {p99:.1f} | {sh} |")
            md.append("")
    out = os.path.join(RES, "zone_workers_real.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)


if __name__ == "__main__":
    main()
