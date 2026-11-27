# least_work prototype on real nginx (Linux)

Same harness as `linux_pr.md`: nginx master `-O2` (with `cmodule/ngx_http_upstream_least_work_module` built in for `least_work`), one worker, open-loop Poisson load, FCFS Go backends, paired repetitions (identical arrivals, routes and costs). Each request class is its own URL route (`/r0`, `/r1`, ...); `least_work key=$uri` learns a cost per route. 5 s warm-up plus 30 s measured per run.

Load generator send lag p99: median 0.05 ms, max 3.49 ms. Errors: 0 in 160 runs. Disturbed runs excluded (send lag p99 over 2 ms and over 3x the cell median): 0.

**Summary.** least_work lowers p99 against PR 2's least_time in all 8 cells, by 25 to 54 % (geometric mean -38 %); in 7 of 8 cells the 95 % interval excludes no change. The simulator's prediction for the same cells is within the measured interval in 8 of 8 cells. Both the cheapest and the costliest class get faster in every cell (point estimates: cheapest -75 to -30 %, costliest -32 to -18 %).

## p99 change, paired by repetition (95 % CI)

| scenario | load | least_work vs PR 2 | least_work vs stock least_time | least_work vs least_conn | simulator: least_work vs PR 2 |
| --- | --- | --- | --- | --- | --- |
| bimodal_het4 | 0.7 | -25 % [-30, -19] | -36 % [-42, -30] | -40 % [-50, -28] | -29 % [-30, -27] |
| bimodal_het4 | 0.85 | -30 % [-40, -17] | -37 % [-51, -20] | -43 % [-53, -30] | -24 % [-26, -22] |
| bimodal_het8 | 0.7 | -36 % [-41, -30] | -44 % [-50, -37] | -45 % [-49, -41] | -33 % [-34, -32] |
| bimodal_het8 | 0.85 | -27 % [-37, -14] | -42 % [-52, -30] | -45 % [-54, -36] | -36 % [-38, -35] |
| api3_het4 | 0.7 | -46 % [-56, -35] | -61 % [-74, -40] | -51 % [-57, -43] | -39 % [-41, -36] |
| api3_het4 | 0.85 | -28 % [-58, +21] | -41 % [-63, -6] | -36 % [-60, +2] | -34 % [-37, -30] |
| api3_het8 | 0.7 | -54 % [-62, -44] | -59 % [-65, -52] | -52 % [-58, -46] | -54 % [-57, -51] |
| api3_het8 | 0.85 | -49 % [-60, -36] | -55 % [-66, -40] | -54 % [-59, -48] | -45 % [-48, -43] |

## p99 by request class: least_work vs PR 2

| scenario | load | cheapest class | costliest class |
| --- | --- | --- | --- |
| bimodal_het4 | 0.7 | -30 % [-37, -22] | -32 % [-37, -25] |
| bimodal_het4 | 0.85 | -35 % [-52, -12] | -32 % [-43, -18] |
| bimodal_het8 | 0.7 | -72 % [-83, -53] | -18 % [-25, -12] |
| bimodal_het8 | 0.85 | -40 % [-58, -15] | -18 % [-24, -11] |
| api3_het4 | 0.7 | -54 % [-61, -45] | -25 % [-42, -2] |
| api3_het4 | 0.85 | -31 % [-62, +25] | -32 % [-46, -13] |
| api3_het8 | 0.7 | -75 % [-85, -60] | -22 % [-42, +4] |
| api3_het8 | 0.85 | -60 % [-72, -43] | -29 % [-41, -14] |

## Absolute p99 (ms, mean over repetitions)

| scenario | load | least_conn | least_time (stock) | least_time bucket=5ms smooth=3s (PR 2) | least_work (prototype) |
| --- | --- | --- | --- | --- | --- |
| bimodal_het4 | 0.7 | 586 | 569 | 492 | 364 |
| bimodal_het4 | 0.85 | 1030 | 962 | 855 | 613 |
| bimodal_het8 | 0.7 | 346 | 341 | 299 | 190 |
| bimodal_het8 | 0.85 | 582 | 548 | 433 | 328 |
| api3_het4 | 0.7 | 1188 | 1552 | 1134 | 589 |
| api3_het4 | 0.85 | 2030 | 2205 | 1866 | 1369 |
| api3_het8 | 0.7 | 796 | 937 | 822 | 385 |
| api3_het8 | 0.85 | 1245 | 1237 | 1142 | 584 |

## Where requests go: share of all requests on the fastest / slowest servers

least_work gives the costly routes to the fast servers and the cheap ones to the slow servers, so by count the slowest servers take the most requests. Real nginx (from the backend ports) against the simulator (`cost_routing.py split`, which also has the split by class in `cost_split.json`).

| scenario | load | PR 2, real | PR 2, simulator | least_work, real | least_work, simulator |
| --- | --- | --- | --- | --- | --- |
| bimodal_het4 | 0.7 | 48 % / 8 % | 48 % / 9 % | 24 % / 25 % | 24 % / 26 % |
| bimodal_het4 | 0.85 | 45 % / 9 % | 46 % / 10 % | 19 % / 37 % | 20 % / 35 % |
| bimodal_het8 | 0.7 | 52 % / 7 % | 52 % / 7 % | 25 % / 18 % | 24 % / 20 % |
| bimodal_het8 | 0.85 | 46 % / 8 % | 47 % / 9 % | 18 % / 33 % | 18 % / 34 % |
| api3_het4 | 0.7 | 48 % / 9 % | 48 % / 9 % | 20 % / 32 % | 20 % / 30 % |
| api3_het4 | 0.85 | 45 % / 12 % | 45 % / 10 % | 16 % / 33 % | 18 % / 37 % |
| api3_het8 | 0.7 | 51 % / 8 % | 50 % / 8 % | 19 % / 26 % | 19 % / 28 % |
| api3_het8 | 0.85 | 48 % / 10 % | 47 % / 10 % | 15 % / 36 % | 15 % / 39 % |

## Repeatability check

least_work lost to PR 2 in 1 of 40 paired repetitions (`api3_het4` at 0.85, repetition 3: 2247 ms against 1509 ms). That repetition was run again twice per variant with the same seed, so with identical arrivals, routes and costs (`linux_lw_rerun.jsonl`): least_work 1148 and 1245 ms, PR 2 2019 and 2281 ms. The loss did not repeat. Over the three runs of the same input, the p99 of a 30 s run spread by 1.5x (PR 2) and 2.0x (least_work) at this load, which is why the cell's interval is wide. The tables keep the original runs (none was disturbed, so none is replaced).

Scenarios:

- `bimodal_het4`: 4 servers 2,1,1,½; 90 % 2 ms / 10 % 80 ms
- `bimodal_het8`: 8 servers 2,2,1,1,1,1,½,½; 90 % 2 ms / 10 % 80 ms
- `api3_het4`: 4 servers 2,1,1,½; 70 % 1 ms / 25 % 10 ms / 5 % 200 ms
- `api3_het8`: 8 servers 2,2,1,1,1,1,½,½; 70 % 1 / 25 % 10 / 5 % 200 ms
