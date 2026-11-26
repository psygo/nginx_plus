# Worker processes with and without `zone` (real nginx, Linux)

Same harness as `linux_pr.md`: open-loop Poisson load, FCFS Go backends, paired repetitions (identical arrivals and costs). nginx on 4 CPUs. Configurations, interleaved by repetition:

- 1 worker (reference: one process sees everything);
- 4 workers with `zone` (shared state), `reuseport`;
- 4 workers without `zone`, `reuseport` (the kernel spreads connections);
- 4 workers without `zone`, plain `listen` (nginx's default).

The load generator keeps connections alive, as browsers and most clients do, so a connection stays with the worker that accepted it.

Disturbed runs excluded (send lag p99 over 2 ms and over 3x the cell median): 12; each was re-run with the same seed, and a run still disturbed after its re-run is left out (that cell has one repetition fewer). The disturbances are pauses of the load generator itself (send lag up to 230 ms), not of nginx: with raw per-request timings, a slow second shows the load generator sending ~100 ms late.

## p99 change vs 1 worker

| scenario | load | balancer | 4 workers + zone | 4 workers, no zone | 4 workers, no zone, no reuseport |
| --- | --- | --- | --- | --- | --- |
| fast3 | 0.85 | least_conn | +2 % [+1, +3] | +28 % [+21, +36] | +23 % [+15, +31] |
| fast3 | 0.85 | least_time (stock) | -0 % [-7, +6] | -7 % [-11, -2] | -6 % [-13, +0] |
| fast3 | 0.85 | least_time bucket=5ms smooth=3s | -5 % [-18, +10] | -3 % [-23, +22] | -11 % [-19, -2] |
| hetero8 | 0.85 | least_conn | -8 % [-27, +17] | +32 % [+2, +71] | +36 % [-0, +85] |
| hetero8 | 0.85 | least_time (stock) | -9 % [-29, +18] | -1 % [-25, +32] | -2 % [-26, +30] |
| hetero8 | 0.85 | least_time bucket=5ms smooth=3s | -2 % [-7, +4] | +16 % [+9, +23] | +20 % [-14, +67] |
| homog8 | 0.85 | least_conn | -1 % [-3, +2] | +23 % [+15, +32] | +16 % [+14, +19] |
| homog8 | 0.85 | least_time (stock) | -1 % [-6, +6] | +11 % [+4, +18] | +12 % [+9, +15] |
| homog8 | 0.85 | least_time bucket=5ms smooth=3s | +1 % [-1, +4] | +14 % [+10, +17] | +11 % [+8, +14] |

## Absolute p99 (ms, mean over repetitions) and share of requests per backend

### fast3 @ 0.85

| balancer | config | p99 | share (%) |
| --- | --- | --- | --- |
| least_conn | 1 worker | 136.4 | 65/25/10 |
| least_conn | 4 workers + zone | 138.9 | 64/25/10 |
| least_conn | 4 workers, no zone | 174.9 | 64/26/11 |
| least_conn | 4 workers, no zone, no reuseport | 167.1 | 64/25/11 |
| least_time (stock) | 1 worker | 88.5 | 67/24/9 |
| least_time (stock) | 4 workers + zone | 88.2 | 67/24/9 |
| least_time (stock) | 4 workers, no zone | 82.7 | 70/23/7 |
| least_time (stock) | 4 workers, no zone, no reuseport | 83.0 | 70/23/7 |
| least_time bucket=5ms smooth=3s | 1 worker | 81.5 | 70/23/8 |
| least_time bucket=5ms smooth=3s | 4 workers + zone | 77.1 | 69/23/8 |
| least_time bucket=5ms smooth=3s | 4 workers, no zone | 79.6 | 73/22/6 |
| least_time bucket=5ms smooth=3s | 4 workers, no zone, no reuseport | 72.6 | 72/22/6 |

### hetero8 @ 0.85

| balancer | config | p99 | share (%) |
| --- | --- | --- | --- |
| least_conn | 1 worker | 108.2 | 21/21/12/12/11/12/6/6 |
| least_conn | 4 workers + zone | 97.9 | 21/21/11/12/12/12/6/6 |
| least_conn | 4 workers, no zone | 139.9 | 20/20/12/12/12/12/6/6 |
| least_conn | 4 workers, no zone, no reuseport | 145.7 | 20/20/12/12/12/12/6/6 |
| least_time (stock) | 1 worker | 91.1 | 23/23/11/11/11/11/5/5 |
| least_time (stock) | 4 workers + zone | 81.8 | 23/23/11/11/11/11/5/5 |
| least_time (stock) | 4 workers, no zone | 89.0 | 24/25/11/11/11/11/4/4 |
| least_time (stock) | 4 workers, no zone, no reuseport | 87.6 | 25/25/11/11/11/11/4/4 |
| least_time bucket=5ms smooth=3s | 1 worker | 72.8 | 24/23/11/11/11/11/4/4 |
| least_time bucket=5ms smooth=3s | 4 workers + zone | 71.4 | 23/23/11/11/11/11/4/4 |
| least_time bucket=5ms smooth=3s | 4 workers, no zone | 84.5 | 25/25/11/11/11/11/3/3 |
| least_time bucket=5ms smooth=3s | 4 workers, no zone, no reuseport | 89.0 | 25/25/11/11/11/11/3/3 |

### homog8 @ 0.85

| balancer | config | p99 | share (%) |
| --- | --- | --- | --- |
| least_conn | 1 worker | 73.7 | 13/12/12/12/13/13/12/13 |
| least_conn | 4 workers + zone | 73.0 | 12/13/12/13/12/13/13/13 |
| least_conn | 4 workers, no zone | 90.7 | 12/13/12/12/12/13/13/12 |
| least_conn | 4 workers, no zone, no reuseport | 85.8 | 12/12/13/13/13/13/12/12 |
| least_time (stock) | 1 worker | 81.3 | 13/13/13/12/12/13/13/12 |
| least_time (stock) | 4 workers + zone | 80.8 | 13/12/13/12/13/13/12/13 |
| least_time (stock) | 4 workers, no zone | 89.9 | 13/12/12/12/12/13/13/12 |
| least_time (stock) | 4 workers, no zone, no reuseport | 91.0 | 12/13/13/13/13/13/12/12 |
| least_time bucket=5ms smooth=3s | 1 worker | 73.0 | 13/12/12/13/13/12/12/13 |
| least_time bucket=5ms smooth=3s | 4 workers + zone | 73.8 | 12/13/12/13/12/13/13/13 |
| least_time bucket=5ms smooth=3s | 4 workers, no zone | 83.0 | 13/13/12/13/12/13/12/13 |
| least_time bucket=5ms smooth=3s | 4 workers, no zone, no reuseport | 80.9 | 13/13/13/12/13/12/12/12 |
