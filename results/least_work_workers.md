# least_work with 4 worker processes on real nginx (Linux)

Same harness as `least_work_real.md`, with `worker_processes 4` and `reuseport` (each load generator connection stays on one worker). Two passes over the same arrivals: without `zone`, and with `zone u 1m;` in the upstream. `least_work` keeps its learned state in the zone when there is one; `least_work learning per worker` is the previous prototype, which learned per worker even with a zone (nginx's own peer state, such as connection counts, is shared by the zone either way). 5 paired repetitions per cell.

Load generator send lag p99: median 0.04 ms, max 1.60 ms. Errors: 0 in 140 runs. Disturbed runs (send lag p99 over 2 ms and over 3x the cell median) re-run with the same seed and replaced: 6 (`linux_lw_workers_rerun.jsonl`); still excluded: 0.

**Summary.** With 4 workers, least_work with its state in the zone lowers p99 against PR 2 with zone by 35 to 47 % (geometric mean -41 %, simulator -43 %); 4 of 4 intervals exclude no change.

## p99 change vs PR 2 with zone, paired by repetition (95 % CI)

| configuration | bimodal_het8 0.7 | bimodal_het8 0.85 | api3_het8 0.7 | api3_het8 0.85 | geometric mean | simulator |
| --- | --- | --- | --- | --- | --- | --- |
| least_conn, no zone | +45 % [+40, +51] | +29 % [+11, +49] | +27 % [+17, +37] | +21 % [+6, +38] | +30 % | +31 % |
| least_conn, zone | +9 % [+2, +17] | +23 % [+15, +30] | +11 % [-3, +27] | +9 % [-3, +24] | +13 % | +11 % |
| PR 2, no zone | -0 % [-7, +7] | +6 % [-1, +12] | +21 % [+13, +28] | -1 % [-17, +18] | +6 % | +14 % |
| least_work, no zone | -8 % [-17, +3] | -6 % [-10, -3] | -11 % [-21, +1] | -14 % [-19, -7] | -10 % | +1 % |
| least_work learning per worker, zone | -11 % [-15, -7] | -3 % [-7, +2] | -9 % [-20, +3] | -8 % [-18, +3] | -8 % | +3 % |
| least_work, state in the zone | -35 % [-46, -23] | -36 % [-41, -31] | -47 % [-49, -45] | -46 % [-49, -43] | -41 % | -43 % |

Simulator and real nginx agree on the configurations that share state through the zone, least_work in the zone included. For those that learn per worker, the simulator is more pessimistic than real nginx (least_work without shared state: predicted about even with PR 2 with zone, measured about 10 % better). One difference that could explain it: the simulator sends each request to a random worker, while on real nginx every load generator connection stays with one worker and the connection pool reuses its most recent connections, so the traffic per worker may be uneven. These runs did not record which worker served a request.

## Absolute p99 (ms, mean over repetitions)

| configuration | bimodal_het8 0.7 | bimodal_het8 0.85 | api3_het8 0.7 | api3_het8 0.85 |
| --- | --- | --- | --- | --- |
| PR 2, zone | 312 | 473 | 723 | 1092 |
| least_conn, no zone | 454 | 597 | 911 | 1298 |
| least_conn, zone | 340 | 582 | 795 | 1208 |
| PR 2, no zone | 311 | 496 | 876 | 1061 |
| least_work, no zone | 289 | 442 | 644 | 950 |
| least_work learning per worker, zone | 278 | 462 | 652 | 997 |
| least_work, state in the zone | 205 | 304 | 383 | 592 |

Scenarios:

- `api3_het8`: 8 servers 2,2,1,1,1,1,½,½; 70 % 1 / 25 % 10 / 5 % 200 ms
- `bimodal_het8`: 8 servers 2,2,1,1,1,1,½,½; 90 % 2 ms / 10 % 80 ms
