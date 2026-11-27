"""Report for bench/linux/lw_stream_matrix.sh: the stream least_work module on real nginx.

Reads results/linux_lw_stream.jsonl (re-runs of disturbed runs, same seed, from
results/linux_lw_stream_rerun.jsonl), and nginx_sim's cost_c.json and cost_robust.json
for the simulator's prediction (the model is the same as for http, with sessions for
requests). Writes results/least_work_stream.md and results/figs/least_work_stream-*.png.

  python3 bench/linux/report_lw_stream.py
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_lw import RES, SCEN, disturbed, sim_prediction  # noqa: E402
from nginx_sim.stats import fmt_ratio, paired_ratio  # noqa: E402

LW, PR2, LC = "stream_least_work", "stream_p123_b5_s3", "stream_least_conn"


def load():
    rows = [json.loads(x) for x in open(os.path.join(RES, "linux_lw_stream.jsonl")) if x.strip()]
    bad = disturbed(rows, lambda r: (r["scenario"], r["util"]))
    key = lambda r: (r["scenario"], r["util"], r["variant"], r["rep"])
    med = {}
    for r in rows:
        med.setdefault((r["scenario"], r["util"]), []).append(r["send_lag_p99"])
    med = {k: sorted(v)[len(v) // 2] for k, v in med.items()}
    p = os.path.join(RES, "linux_lw_stream_rerun.jsonl")
    rerun = {}
    for r in ([json.loads(x) for x in open(p) if x.strip()] if os.path.exists(p) else []):
        if not (r["send_lag_p99"] > 2.0 and r["send_lag_p99"] > 3 * med[(r["scenario"], r["util"])]):
            rerun[key(r)] = r
    ids = {id(r) for r in bad}
    replaced = [rerun[key(r)] for r in bad if key(r) in rerun]
    rows = [r for r in rows if id(r) not in ids] + replaced
    return rows, replaced, [r for r in bad if key(r) not in rerun]


def main():
    rows, replaced, bad = load()
    by, cls = {}, {}
    for r in rows:
        k = (r["scenario"], r["util"], r["variant"])
        by.setdefault(k, {})[r["rep"]] = r["p99"]
        for c, v in r.get("by_class", {}).items():
            cls.setdefault(k + (c,), {})[r["rep"]] = v["p99"]
    cells = sorted({(k[0], k[1]) for k in by}, key=lambda c: (list(SCEN).index(c[0]), c[1]))
    sim = sim_prediction()
    lag = sorted(r["send_lag_p99"] for r in rows)
    rs = [paired_ratio(by[c + (LW,)], by[c + (PR2,)]) for c in cells]
    gm = math.exp(sum(math.log(r[0]) for r in rs) / len(rs))
    md = ["# least_work stream module on real nginx (Linux)", "",
          "Same harness as `least_work_real.md`, through a stream proxy instead of an http one: "
          "nginx master `-O2` with `cmodule/ngx_stream_upstream_least_work_module`, one worker, "
          "a `stream` server listening on one port per request class, and one TCP session per "
          "request (the load generator opens a new connection for each). `least_work "
          "key=$server_port` learns a cost per port, that is per class, from session times. "
          "PR 2's least_time runs as `least_time first_byte bucket=5ms smooth=3s`, the stream "
          "counterpart of `header` in the http runs. 5 paired repetitions, 5 s warm-up plus "
          "30 s measured.", "",
          f"Load generator send lag p99: median {lag[len(lag) // 2]:.2f} ms, max {lag[-1]:.2f} ms. "
          f"Errors: {sum(r['errors'] for r in rows)} in {len(rows)} runs. Disturbed runs (send "
          f"lag p99 over 2 ms and over 3x the cell median) re-run with the same seed and "
          f"replaced: {len(replaced)}; still excluded: {len(bad)}.", "",
          f"**Summary.** The stream module lowers p99 against PR 2's least_time in all "
          f"{len(rs)} cells, by {(1 - max(r[0] for r in rs)) * 100:.0f} to "
          f"{(1 - min(r[0] for r in rs)) * 100:.0f} % (geometric mean {(gm - 1) * 100:+.0f} %); "
          f"in {sum(1 for r in rs if r[2] < 1)} of {len(rs)} cells the 95 % interval excludes "
          "no change." if all(r[0] < 1 for r in rs) else
          f"**Summary.** Geometric mean p99 change against PR 2's least_time: "
          f"{(gm - 1) * 100:+.0f} %, by cell from {(min(r[0] for r in rs) - 1) * 100:+.0f} to "
          f"{(max(r[0] for r in rs) - 1) * 100:+.0f} %.", "",
          "## p99 change, paired by repetition (95 % CI)", "",
          "| scenario | load | least_work vs PR 2 | least_work vs least_conn | "
          "simulator: least_work vs PR 2 |", "| --- | --- | --- | --- | --- |"]
    for (sc, u), r in zip(cells, rs):
        md.append(f"| {sc[3:]} | {u} | {fmt_ratio(r)} | {fmt_ratio(paired_ratio(by[(sc, u, LW)], by[(sc, u, LC)]))} | "
                  + (fmt_ratio(sim[(sc, u)]) if (sc, u) in sim else "") + " |")
    md += ["", "## p99 by request class: least_work vs PR 2", "",
           "| scenario | load | cheapest class | costliest class |", "| --- | --- | --- | --- |"]
    for sc, u in cells:
        cs = sorted({k[3] for k in cls if k[0] == sc and k[1] == u}, key=int)
        a = paired_ratio(cls[(sc, u, LW, cs[0])], cls[(sc, u, PR2, cs[0])])
        b = paired_ratio(cls[(sc, u, LW, cs[-1])], cls[(sc, u, PR2, cs[-1])])
        md.append(f"| {sc[3:]} | {u} | {fmt_ratio(a)} | {fmt_ratio(b)} |")
    md += ["", "## Absolute p99 (ms, mean over repetitions)", "",
           "| scenario | load | least_conn | PR 2 | least_work |", "| --- | --- | --- | --- | --- |"]
    for sc, u in cells:
        md.append(f"| {sc[3:]} | {u} | " + " | ".join(
            f"{sum(by[(sc, u, v)].values()) / len(by[(sc, u, v)]):.0f}" for v in (LC, PR2, LW)) + " |")
    md += ["", "Scenarios:", ""] + [f"- `{k[3:]}`: {SCEN[k]}" for k in sorted({c[0] for c in cells})] + [""]
    out = os.path.join(RES, "least_work_stream.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)

    from nginx_sim import charts
    cats = [f"{sc[3:]} · {int(u * 100)} %" for sc, u in cells]
    real = [(r[0] - 1) * 100 for r in rs]
    pred = [(sim[c][0] - 1) * 100 if c in sim else 0.0 for c in cells]

    def fig(t):
        return charts.grouped_bars(
            t, "least_work stream module on real nginx",
            ["p99 change vs PR 2's least_time (first_byte bucket=5ms smooth=3s), stream proxy,",
             "one session per request, Linux -O2 builds, 5 paired repetitions; and the simulator."],
            cats, [("simulator", pred, 1), ("real nginx", real, 0)], "p99 change vs PR 2",
            label_series=1, label_w=190)
    charts.render(fig, "least_work_stream", os.path.join(RES, "figs"))


if __name__ == "__main__":
    main()
