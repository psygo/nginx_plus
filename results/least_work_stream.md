# least_work stream module on real nginx (Linux)

Same harness as `least_work_real.md`, through a stream proxy instead of an http one: nginx master `-O2` with `cmodule/ngx_stream_upstream_least_work_module`, one worker, a `stream` server listening on one port per request class, and one TCP session per request (the load generator opens a new connection for each). `least_work key=$server_port` learns a cost per port, that is per class, from session times. PR 2's least_time runs as `least_time first_byte bucket=5ms smooth=3s`, the stream counterpart of `header` in the http runs. 5 paired repetitions, 5 s warm-up plus 30 s measured.

Load generator send lag p99: median 0.07 ms, max 1.96 ms. Errors: 0 in 60 runs. Disturbed runs (send lag p99 over 2 ms and over 3x the cell median) re-run with the same seed and replaced: 4; still excluded: 0.

**Summary.** The stream module lowers p99 against PR 2's least_time in all 4 cells, by 27 to 55 % (geometric mean -41 %); in 4 of 4 cells the 95 % interval excludes no change.

## p99 change, paired by repetition (95 % CI)

| scenario | load | least_work vs PR 2 | least_work vs least_conn | simulator: least_work vs PR 2 |
| --- | --- | --- | --- | --- |
| bimodal_het4 | 0.7 | -27 % [-37, -15] | -37 % [-47, -27] | -29 % [-30, -27] |
| bimodal_het4 | 0.85 | -29 % [-32, -26] | -42 % [-50, -34] | -24 % [-26, -22] |
| api3_het8 | 0.7 | -55 % [-58, -51] | -57 % [-60, -53] | -54 % [-57, -51] |
| api3_het8 | 0.85 | -49 % [-57, -41] | -56 % [-61, -51] | -45 % [-48, -43] |

## p99 by request class: least_work vs PR 2

| scenario | load | cheapest class | costliest class |
| --- | --- | --- | --- |
| bimodal_het4 | 0.7 | -36 % [-51, -16] | -24 % [-33, -15] |
| bimodal_het4 | 0.85 | -32 % [-39, -25] | -22 % [-33, -10] |
| api3_het8 | 0.7 | -80 % [-86, -71] | -14 % [-25, -1] |
| api3_het8 | 0.85 | -61 % [-69, -51] | -30 % [-40, -19] |

## Absolute p99 (ms, mean over repetitions)

| scenario | load | least_conn | PR 2 | least_work |
| --- | --- | --- | --- | --- |
| bimodal_het4 | 0.7 | 576 | 517 | 369 |
| bimodal_het4 | 0.85 | 1021 | 818 | 587 |
| api3_het8 | 0.7 | 795 | 758 | 344 |
| api3_het8 | 0.85 | 1224 | 1036 | 540 |

Scenarios:

- `api3_het8`: 8 servers 2,2,1,1,1,1,½,½; 70 % 1 / 25 % 10 / 5 % 200 ms
- `bimodal_het4`: 4 servers 2,1,1,½; 90 % 2 ms / 10 % 80 ms
