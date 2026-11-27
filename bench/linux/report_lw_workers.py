"""Report for bench/linux/lw_workers_matrix.sh: least_work with 4 worker processes.

Reads results/linux_lw_workers.jsonl (and results/cost_workers4.json for the simulator's
prediction of the same cells), writes results/least_work_workers.md and
results/figs/least_work_workers-{light,dark}.png.

Every configuration is compared with PR 2's least_time behind a zone, the configuration
nginx recommends for several workers. Ratios are paired by repetition (same arrivals,
routes and costs in both passes).

  python3 bench/linux/report_lw_workers.py
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_lw import RES, ROOT, SCEN, disturbed, fmt, ratio  # noqa: E402

REF = ("p123_b5_s3", True)
# (variant, zone) on real nginx, (balancer, shared) in the simulator, label
CONFIGS = [(("least_conn", False), ("least_conn", False), "least_conn, no zone"),
           (("least_conn", True), ("least_conn", True), "least_conn, zone"),
           (("p123_b5_s3", False), ("p123_b5_s3", False), "PR 2, no zone"),
           (("least_work", False), ("least_work_c_pw", False), "least_work, no zone"),
           (("least_work_old", True), ("least_work_c_pw", True),
            "least_work learning per worker, zone"),
           (("least_work", True), ("least_work_c", True), "least_work, state in the zone")]


def geo(rs):
    return math.exp(sum(math.log(r) for r in rs) / len(rs))


def main():
    rows = [json.loads(x) for x in open(os.path.join(RES, "linux_lw_workers.jsonl")) if x.strip()]
    bad = disturbed(rows, lambda r: (r["scenario"], r["util"]))
    ids = {id(r) for r in bad}
    # disturbed runs are re-run with the same seed (linux_lw_workers_rerun.jsonl); the
    # latest re-run that passes the same test, against the cell's median, replaces them
    key = lambda r: (r["scenario"], r["util"], r["variant"], r["zone"], r["rep"])
    med = {}
    for r in rows:
        med.setdefault((r["scenario"], r["util"]), []).append(r["send_lag_p99"])
    med = {k: sorted(v)[len(v) // 2] for k, v in med.items()}
    p = os.path.join(RES, "linux_lw_workers_rerun.jsonl")
    rerun = {}
    for r in ([json.loads(x) for x in open(p) if x.strip()] if os.path.exists(p) else []):
        if not (r["send_lag_p99"] > 2.0 and r["send_lag_p99"] > 3 * med[(r["scenario"], r["util"])]):
            rerun[key(r)] = r
    replaced = [rerun[key(r)] for r in bad if key(r) in rerun]
    rows = [r for r in rows if id(r) not in ids] + replaced
    bad = [r for r in bad if key(r) not in rerun]
    by = {}
    for r in rows:
        by.setdefault((r["scenario"], r["util"], r["variant"], r["zone"]), {})[r["rep"]] = r["p99"]
    cells = sorted({(k[0], k[1]) for k in by}, key=lambda c: (list(SCEN).index(c[0]), c[1]))

    sim = {}
    p = os.path.join(RES, "cost_workers4.json")
    for r in (json.load(open(p)) if os.path.exists(p) else []):
        if (r["scenario"], r["util"]) in cells:
            sim.setdefault((r["scenario"], r["util"], r["balancer"], r["shared"]), {})[r["seed"]] = r["p99"]

    def real_r(c, cfg):
        a, b = by.get(c + cfg), by.get(c + REF)
        return ratio(a, b) if a and b else None

    def sim_r(c, cfg):
        a, b = sim.get(c + cfg), sim.get(c + ("p123_b5_s3", True))
        return ratio(a, b) if a and b else None

    lag = sorted(r["send_lag_p99"] for r in rows)
    md = ["# least_work with 4 worker processes on real nginx (Linux)", "",
          "Same harness as `least_work_real.md`, with `worker_processes 4` and `reuseport` "
          "(each load generator connection stays on one worker). Two passes over the same "
          "arrivals: without `zone`, and with `zone u 1m;` in the upstream. "
          "`least_work` keeps its learned state in the zone when there is one; "
          "`least_work learning per worker` is the previous prototype, which learned per "
          "worker even with a zone (nginx's own peer state, such as connection counts, "
          "is shared by the zone either way). 5 paired repetitions per cell.", "",
          f"Load generator send lag p99: median {lag[len(lag) // 2]:.2f} ms, max {lag[-1]:.2f} ms. "
          f"Errors: {sum(r['errors'] for r in rows)} in {len(rows)} runs. Disturbed runs (send "
          f"lag p99 over 2 ms and over 3x the cell median) re-run with the same seed and replaced: "
          f"{len(replaced)} (`linux_lw_workers_rerun.jsonl`); still excluded: {len(bad)}.", "",
          "SUMMARY", "",
          "## p99 change vs PR 2 with zone, paired by repetition (95 % CI)", "",
          "| configuration | " + " | ".join(f"{sc[3:]} {u}" for sc, u in cells)
          + " | geometric mean | simulator |",
          "| --- |" + " --- |" * (len(cells) + 2)]
    gm_real, gm_sim, labels = [], [], []
    for cfg, scfg, name in CONFIGS:
        rs = [real_r(c, cfg) for c in cells]
        ss = [sim_r(c, scfg) for c in cells]
        if not any(rs):
            continue
        g = geo([r[0] for r in rs if r])
        gs = geo([s[0] for s in ss if s]) if all(ss) else None
        labels.append(name)
        gm_real.append((g - 1) * 100)
        gm_sim.append((gs - 1) * 100 if gs else 0.0)
        md.append(f"| {name} | " + " | ".join(fmt(r) if r else "" for r in rs)
                  + f" | {(g - 1) * 100:+.0f} % | " + (f"{(gs - 1) * 100:+.0f} %" if gs else "") + " |")
    md += ["", "Simulator and real nginx agree on the configurations that share state through "
           "the zone, least_work in the zone included. For those that learn per worker, the "
           "simulator is more pessimistic than real nginx (least_work without shared state: "
           "predicted about even with PR 2 with zone, measured about 10 % better). One "
           "difference that could explain it: the simulator sends each request to a random "
           "worker, while on real nginx every load generator connection stays with one worker "
           "and the connection pool reuses its most recent connections, so the traffic per "
           "worker may be uneven. These runs did not record which worker served a request.",
           "", "## Absolute p99 (ms, mean over repetitions)", "",
           "| configuration | " + " | ".join(f"{sc[3:]} {u}" for sc, u in cells) + " |",
           "| --- |" + " --- |" * len(cells)]
    for cfg, _, name in [(REF, None, "PR 2, zone")] + CONFIGS:
        md.append(f"| {name} | " + " | ".join(
            f"{sum(by[c + cfg].values()) / len(by[c + cfg]):.0f}" if c + cfg in by else ""
            for c in cells) + " |")
    md += ["", "Scenarios:", ""] + [f"- `{k[3:]}`: {SCEN[k]}" for k in sorted({c[0] for c in cells})] + [""]

    i_new = labels.index("least_work, state in the zone") if "least_work, state in the zone" in labels else None
    if i_new is not None:
        new = [real_r(c, ("least_work", True)) for c in cells]
        md[md.index("SUMMARY")] = (
            f"**Summary.** With 4 workers, least_work with its state in the zone lowers p99 "
            f"against PR 2 with zone by {(1 - max(r[0] for r in new)) * 100:.0f} to "
            f"{(1 - min(r[0] for r in new)) * 100:.0f} % (geometric mean "
            f"{gm_real[i_new]:+.0f} %, simulator {gm_sim[i_new]:+.0f} %); "
            f"{sum(1 for r in new if r[2] < 1)} of {len(new)} intervals exclude no change.")
    else:
        md.remove("SUMMARY")
    out = os.path.join(RES, "least_work_workers.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)

    sys.path.insert(0, os.path.join(ROOT, "sim"))
    import charts

    def fig(t):
        return charts.grouped_bars(
            t, "least_work with 4 worker processes",
            ["p99 change vs PR 2's least_time with zone, geometric mean over 2 scenarios x 2 loads,",
             "Linux -O2 builds, 5 paired repetitions; and the simulator's prediction."],
            labels, [("simulator", gm_sim, 1), ("real nginx", gm_real, 0)], "p99 change vs PR 2 with zone",
            label_series=1, label_w=250)
    charts.render(fig, "least_work_workers", os.path.join(RES, "figs"))


if __name__ == "__main__":
    main()
