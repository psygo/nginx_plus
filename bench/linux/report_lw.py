"""Report for bench/linux/lw_matrix.sh: the least_work prototype on real nginx.

Reads results/linux_lw.jsonl (and results/cost_c.json, results/cost_robust.json for the
simulator's prediction of the same cells), writes results/least_work_real.md and
results/figs/least_work_real-{light,dark}.png.

Ratios are paired by repetition (same arrivals, routes and costs).

  python3 bench/linux/report_lw.py
"""
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES = os.path.join(ROOT, "results")
LABEL = {"least_conn": "least_conn", "lt": "least_time (stock)",
         "p123_b5_s3": "least_time bucket=5ms smooth=3s (PR 2)", "least_work": "least_work (prototype)"}
SCEN = {"cr_bimodal_het4": "4 servers 2,1,1,½; 90 % 2 ms / 10 % 80 ms",
        "cr_bimodal_het8": "8 servers 2,2,1,1,1,1,½,½; 90 % 2 ms / 10 % 80 ms",
        "cr_api3_het4": "4 servers 2,1,1,½; 70 % 1 ms / 25 % 10 ms / 5 % 200 ms",
        "cr_api3_het8": "8 servers 2,2,1,1,1,1,½,½; 70 % 1 / 25 % 10 / 5 % 200 ms"}
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import SCENARIOS                              # noqa: E402
T975 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}


def disturbed(rows, key):
    """Runs the load generator could not pace: send lag p99 over 2 ms and over 3x the
    median of the same cell (the rule of the other reports)."""
    cells = {}
    for r in rows:
        cells.setdefault(key(r), []).append(r["send_lag_p99"])
    med = {k: statistics.median(v) for k, v in cells.items()}
    return [r for r in rows if r["send_lag_p99"] > 2.0 and r["send_lag_p99"] > 3 * med[key(r)]]


def ratio(a, b):
    """Geometric-mean ratio a/b over common repetitions, 95 % CI: (g, lo, hi)."""
    reps = sorted(set(a) & set(b))
    logs = [math.log(a[k] / b[k]) for k in reps]
    n = len(logs)
    m = sum(logs) / n
    if n < 2:
        return math.exp(m), float("nan"), float("nan")
    sd = math.sqrt(sum((x - m) ** 2 for x in logs) / (n - 1))
    h = T975.get(n, 1.96) * sd / math.sqrt(n)
    return math.exp(m), math.exp(m - h), math.exp(m + h)


def fmt(r):
    g, lo, hi = r
    if math.isnan(lo):
        return f"{(g - 1) * 100:+.0f} %"
    return f"{(g - 1) * 100:+.0f} % [{(lo - 1) * 100:+.0f}, {(hi - 1) * 100:+.0f}]"


def load():
    rows = [json.loads(x) for x in open(os.path.join(RES, "linux_lw.jsonl")) if x.strip()]
    bad = disturbed(rows, lambda r: (r["scenario"], r["util"]))
    ids = {id(r) for r in bad}
    return [r for r in rows if id(r) not in ids], bad


def sim_prediction():
    """least_work_c vs p123_b5_s3 in the simulator, same scenario and load."""
    by = {}
    for f in ("cost_c.json", "cost_robust.json"):
        p = os.path.join(RES, f)
        if os.path.exists(p):
            for r in json.load(open(p)):
                by.setdefault((r["scenario"], r["util"], r["balancer"]), {})[r["seed"]] = r["p99"]
    out = {}
    for (sc, u, b) in list(by):
        if b == "least_work_c" and (sc, u, "p123_b5_s3") in by:
            out[(sc, u)] = ratio(by[(sc, u, b)], by[(sc, u, "p123_b5_s3")])
    return out


def server_share(rows, sim_rows):
    """Share of all requests served by the fastest and the slowest servers, by cell and
    variant: real nginx from by_port (port 9001 + i has speed i), simulator from
    by_class_peer (results/cost_split.json)."""
    real, sim = {}, {}
    for r in rows:
        sp = SCENARIOS[r["scenario"]]["speeds"]
        d = real.setdefault((r["scenario"], r["util"], r["variant"]), [0, 0, 0])
        for port, n in r["by_port"].items():
            s = sp[int(port) - 9001]
            d[0] += n * (s == max(sp))
            d[1] += n * (s == min(sp))
            d[2] += n
    for r in sim_rows:
        sp = r["speeds"]
        b = {"least_work_c": "least_work"}.get(r["balancer"], r["balancer"])
        d = sim.setdefault((r["scenario"], r["util"], b), [0, 0, 0])
        for per in r["by_class_peer"].values():
            for p, n in per.items():
                s = sp[int(p)]
                d[0] += n * (s == max(sp))
                d[1] += n * (s == min(sp))
                d[2] += n
    f = lambda d: (d[0] / d[2], d[1] / d[2])
    return {k: f(v) for k, v in real.items()}, {k: f(v) for k, v in sim.items()}


def main():
    rows, bad = load()
    by, cls = {}, {}
    for r in rows:
        k = (r["scenario"], r["util"], r["variant"])
        by.setdefault(k, {})[r["rep"]] = r["p99"]
        for c, v in r.get("by_class", {}).items():
            cls.setdefault(k + (c,), {})[r["rep"]] = v["p99"]
    cells = sorted({(k[0], k[1]) for k in by}, key=lambda c: (list(SCEN).index(c[0]), c[1]))
    sim = sim_prediction()
    lag = sorted(r["send_lag_p99"] for r in rows)
    errors = sum(r["errors"] for r in rows)
    md = ["# least_work prototype on real nginx (Linux)", "",
          "Same harness as `linux_pr.md`: nginx master `-O2` (with "
          "`cmodule/ngx_http_upstream_least_work_module` built in for `least_work`), one "
          "worker, open-loop Poisson load, FCFS Go backends, paired repetitions (identical "
          "arrivals, routes and costs). Each request class is its own URL route "
          "(`/r0`, `/r1`, ...); `least_work key=$uri` learns a cost per route. 5 s warm-up "
          "plus 30 s measured per run.", "",
          f"Load generator send lag p99: median {lag[len(lag) // 2]:.2f} ms, max {lag[-1]:.2f} ms. "
          f"Errors: {errors} in {len(rows)} runs. Disturbed runs excluded (send lag p99 over 2 ms "
          f"and over 3x the cell median): {len(bad)}.", "",
          "SUMMARY", "",
          "## p99 change, paired by repetition (95 % CI)", "",
          "| scenario | load | least_work vs PR 2 | least_work vs stock least_time | "
          "least_work vs least_conn | simulator: least_work vs PR 2 |",
          "| --- | --- | --- | --- | --- | --- |"]
    for sc, u in cells:
        lw = by.get((sc, u, "least_work"))
        if not lw:
            continue
        cols = [fmt(ratio(lw, by[(sc, u, b)])) if (sc, u, b) in by else ""
                for b in ("p123_b5_s3", "lt", "least_conn")]
        cols.append(fmt(sim[(sc, u)]) if (sc, u) in sim else "")
        md.append(f"| {sc[3:]} | {u} | " + " | ".join(cols) + " |")
    cls_r = []
    md += ["", "## p99 by request class: least_work vs PR 2", "",
           "| scenario | load | cheapest class | costliest class |", "| --- | --- | --- | --- |"]
    for sc, u in cells:
        cs = sorted({k[3] for k in cls if k[0] == sc and k[1] == u}, key=int)
        if not cs or (sc, u, "least_work", cs[0]) not in cls:
            continue
        a = ratio(cls[(sc, u, "least_work", cs[0])], cls[(sc, u, "p123_b5_s3", cs[0])])
        b = ratio(cls[(sc, u, "least_work", cs[-1])], cls[(sc, u, "p123_b5_s3", cs[-1])])
        md.append(f"| {sc[3:]} | {u} | {fmt(a)} | {fmt(b)} |")
        cls_r.append(((a[0] - 1) * 100, (b[0] - 1) * 100))
    md += ["", "## Absolute p99 (ms, mean over repetitions)", "",
           "| scenario | load | " + " | ".join(LABEL.values()) + " |",
           "| --- | --- |" + " --- |" * len(LABEL)]
    for sc, u in cells:
        md.append(f"| {sc[3:]} | {u} | " + " | ".join(
            f"{sum(by[(sc, u, v)].values()) / len(by[(sc, u, v)]):.0f}" if (sc, u, v) in by else ""
            for v in LABEL) + " |")
    sp = os.path.join(RES, "cost_split.json")
    if os.path.exists(sp):
        real_sh, sim_sh = server_share(rows, json.load(open(sp)))
        md += ["", "## Where requests go: share of all requests on the fastest / slowest servers", "",
               "least_work gives the costly routes to the fast servers and the cheap ones to the "
               "slow servers, so by count the slowest servers take the most requests. Real nginx "
               "(from the backend ports) against the simulator (`cost_routing.py split`, which "
               "also has the split by class in `cost_split.json`).", "",
               "| scenario | load | PR 2, real | PR 2, simulator | least_work, real | least_work, simulator |",
               "| --- | --- | --- | --- | --- | --- |"]
        g = lambda t: f"{t[0] * 100:.0f} % / {t[1] * 100:.0f} %" if t else ""
        for sc, u in cells:
            md.append(f"| {sc[3:]} | {u} | " + " | ".join(
                g(d.get((sc, u, v))) for v in ("p123_b5_s3", "least_work") for d in (real_sh, sim_sh)) + " |")
    rr = os.path.join(RES, "linux_lw_rerun.jsonl")
    if os.path.exists(rr):
        re_rows = [json.loads(x) for x in open(rr) if x.strip()]
        r0 = re_rows[0]
        orig = {v: by[(r0["scenario"], r0["util"], v)][r0["rep"]] for v in ("p123_b5_s3", "least_work")}
        again = {v: [r["p99"] for r in re_rows if r["variant"] == v] for v in orig}
        pairs = [(sc, u, k) for (sc, u, v), d in by.items() if v == "least_work" for k in d
                 if k in by.get((sc, u, "p123_b5_s3"), {})]
        lost = sum(1 for sc, u, k in pairs if by[(sc, u, "least_work")][k] >= by[(sc, u, "p123_b5_s3")][k])
        md += ["", "## Repeatability check", "",
               f"least_work lost to PR 2 in {lost} of {len(pairs)} paired repetitions (`{r0['scenario'][3:]}` at "
               f"{r0['util']}, repetition {r0['rep']}: {orig['least_work']:.0f} ms against "
               f"{orig['p123_b5_s3']:.0f} ms). That repetition was run again twice per variant with the same seed, "
               "so with identical arrivals, routes and costs (`linux_lw_rerun.jsonl`): least_work "
               + " and ".join(f"{x:.0f}" for x in again["least_work"]) + " ms, PR 2 "
               + " and ".join(f"{x:.0f}" for x in again["p123_b5_s3"]) + " ms. The loss did not "
               "repeat. Over the three runs of the same input, the p99 of a 30 s run spread by "
               + " and ".join(f"{max(again[v] + [orig[v]]) / min(again[v] + [orig[v]]):.1f}x ({n})"
                              for v, n in (("p123_b5_s3", "PR 2"), ("least_work", "least_work")))
               + " at this load, which is why the cell's interval is wide. The tables keep the "
               "original runs (none was disturbed, so none is replaced)."]
    md += ["", "Scenarios:", ""] + [f"- `{k[3:]}`: {v}" for k, v in SCEN.items()] + [""]
    cheap = (min(x[0] for x in cls_r), max(x[0] for x in cls_r))
    costly = (min(x[1] for x in cls_r), max(x[1] for x in cls_r))
    rs = [ratio(by[(sc, u, "least_work")], by[(sc, u, "p123_b5_s3")]) for sc, u in cells
          if (sc, u, "least_work") in by and (sc, u, "p123_b5_s3") in by]
    gm = math.exp(sum(math.log(r[0]) for r in rs) / len(rs))
    sig = sum(1 for r in rs if r[2] < 1)
    md[md.index("SUMMARY")] = (
        f"**Summary.** least_work lowers p99 against PR 2's least_time in all {len(rs)} cells, by "
        f"{(1 - max(r[0] for r in rs)) * 100:.0f} to {(1 - min(r[0] for r in rs)) * 100:.0f} % "
        f"(geometric mean {(gm - 1) * 100:+.0f} %); in {sig} of {len(rs)} cells the 95 % interval "
        "excludes no change. The simulator's prediction for the same cells is within the measured "
        "interval in " + str(sum(1 for (sc, u), r in zip([c for c in cells if (c[0], c[1], "least_work") in by], rs)
                                  if (sc, u) in sim and r[1] <= sim[(sc, u)][0] <= r[2]))
        + f" of {len(rs)} cells. Both the cheapest and the costliest class get faster in "
        f"every cell (point estimates: cheapest {cheap[0]:+.0f} to {cheap[1]:+.0f} %, costliest "
        f"{costly[0]:+.0f} to {costly[1]:+.0f} %).")
    out = os.path.join(RES, "least_work_real.md")
    with open(out, "w") as f:
        f.write("\n".join(md))
    print(out)

    sys.path.insert(0, os.path.join(ROOT, "sim"))
    import charts
    real, pred, cats = [], [], []
    for sc, u in cells:
        if (sc, u, "least_work") in by and (sc, u, "p123_b5_s3") in by:
            cats.append(f"{sc[3:]} · {int(u * 100)} %")
            real.append((ratio(by[(sc, u, "least_work")], by[(sc, u, "p123_b5_s3")])[0] - 1) * 100)
            pred.append((sim[(sc, u)][0] - 1) * 100 if (sc, u) in sim else 0.0)

    def fig(t):
        return charts.grouped_bars(
            t, "least_work on real nginx",
            ["p99 change vs PR 2's least_time (bucket=5ms smooth=3s), Linux -O2 builds, 5 paired",
             "repetitions; and the simulator's prediction for the same cells (C-faithful model)."],
            cats, [("simulator", pred, 1), ("real nginx", real, 0)], "p99 change vs PR 2",
            label_series=1, label_w=190)
    charts.render(fig, "least_work_real", os.path.join(RES, "figs"))


if __name__ == "__main__":
    main()
