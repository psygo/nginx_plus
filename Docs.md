# least_time evidence

Evidence for two proposed changes to the nginx `least_time` upstream balancer:

1. Fixed-point averages, a bug fix. Averages are kept in whole milliseconds, so they can be off by up to 10 ms, and fast servers converge to about 10 ms.
2. The `bucket=time` and `smooth=time` parameters of `least_time`.

The code lives on other branches of this fork:

| branch | content |
| --- | --- |
| `lt-fixed-point` | change 1 (one commit on nginx master) |
| `lt-smooth` | change 1, then `bucket`, then `smooth` (three commits) |
| `lt-bucket` | change 1, then `bucket` (intermediate, used by the benchmarks) |
| `lt-trace` | experiment-only prototype with trace logging and compile-time knobs |
| `least-time-evidence` | only the files described here, no nginx code |
| `least-time-work` | `lt-smooth` plus these files: everything in one place |

## Pull requests

| PR | branch | status | description |
| --- | --- | --- | --- |
| [nginx/nginx#2048](https://github.com/nginx/nginx/pull/2048): fixed rounding of least_time averages | `lt-fixed-point` | open, F5 CLA signed (2026-11-26) | [pr/PR1.md](pr/PR1.md) |
| [nginx/nginx#2050](https://github.com/nginx/nginx/pull/2050): `bucket` and `smooth` parameters of `least_time` | `lt-smooth` | open; depends on #2048 (#2049 was opened from `least-time-work` by mistake and closed) | [pr/PR2.md](pr/PR2.md) |
| PR 2 tests (nginx/nginx-tests) | `lt-bucket` on [psygo/nginx-tests](https://github.com/psygo/nginx-tests/tree/lt-bucket) | not opened yet | `patches/tests-pr2-*` |
| PR 2 docs (nginx/nginx.org) | `least-time-bucket-smooth` on [psygo/nginx.org](https://github.com/psygo/nginx.org/tree/least-time-bucket-smooth) | not opened yet | `patches/docs-pr2-*` |

Explorations, opened inside this fork against `least-time-work` (not sent to nginx). Their simulations are in nginx_sim; #2, #4, #5 and #6 were simulation only and are closed, and #1 and #3 keep their real-nginx and C parts:

| PR | branch | verdict |
| --- | --- | --- |
| [#1](https://github.com/psygo/nginx_plus/pull/1): worker processes with and without `zone` | `explore/zone-workers` | docs note drafted (local nginx.org branch) |
| [#2](https://github.com/psygo/nginx_plus/pull/2): learn how response time grows with load | `explore/speed-estimator` | candidate: −9.5 % p99 vs PR 2 (hinge, configured worker count); worst +15 % |
| [#3](https://github.com/psygo/nginx_plus/pull/3): routing by request cost, learned from response times | `explore/cost-routing` | strongest candidate: −15 to −50 % p99 vs PR 2 with no cost hint in the simulator (worst −8 %); C prototype confirmed on real nginx, −25 to −54 % (1 worker), −41 % with 4 workers sharing a zone, −41 % in stream |
| [#4](https://github.com/psygo/nginx_plus/pull/4): admission control and queueing | `explore/admission-control` | existing features; docs note at most |
| [#5](https://github.com/psygo/nginx_plus/pull/5): learning-augmented balancing with a floor | `explore/learning-augmented` | research only |
| [#6](https://github.com/psygo/nginx_plus/pull/6): unbiased `smooth=` weighting | `explore/smooth-weighting` | rejected; PR 2 unchanged |

The simulator, every simulation experiment and all their results are in [psygo/nginx_sim](https://github.com/psygo/nginx_sim): the evidence for the two pull requests in `research/pr`, the explorations in `research/explore`, with an index and verdicts in [research/Docs.md](https://github.com/psygo/nginx_sim/blob/main/research/Docs.md). This repository keeps the nginx code, the real-nginx benchmark and its results, the pull request texts and the decision log of rejected ideas.

## Reports

- [sim_pr.md](https://github.com/psygo/nginx_sim/blob/main/research/results/sim_pr.md) (in nginx_sim): simulation, 12 scenarios × 4 loads, 20 paired seeds × 300,000 requests per cell, plus a bucket × smooth sweep (10 seeds × 200,000).
- [validation.md](https://github.com/psygo/nginx_sim/blob/main/research/results/validation.md) (in nginx_sim): the simulator checked against exact queueing-theory formulas (also at the end of `sim_pr.md`).
- [results/rejected_ideas.md](results/rejected_ideas.md): ideas that were tried and not proposed (power of two choices, a service-rate rule, smaller tweaks), with data and reasons.
- [results/linux_pr.md](results/linux_pr.md): real nginx on Linux, 18 configurations × 6 variants × 5 paired repetitions, with a simulator-vs-real comparison.
- [pr/PR1.md](pr/PR1.md), [pr/PR2.md](pr/PR2.md): pull request descriptions; [pr/](pr/) also holds the commit messages.
- [results/figs/](results/figs/): charts of the real-nginx results, each in a light and a dark version (PNG for GitHub, SVG source), drawn with nginx_sim's `charts` from stored results: `python3 bench/linux/figs_pr2.py` for PR 2; the exploration branches' reports draw their own. The simulation charts, PR 1's among them, are in nginx_sim.
- [PLAN.md](PLAN.md): working notes, with the earlier hypotheses (H1 to H6) and prototype measurements.

## Layout

| path | content |
| --- | --- |
| `bench/linux/` | real-nginx benchmark: Dockerfile, Go backend and open-loop load generator, `run.py`, `matrix.sh`, `report_linux.py` (with the simulator-vs-real comparison), `figs_pr2.py`; `nginx_sim_path.py` finds the nginx_sim checkout the reports read |
| `bench/*.py`, `bench/exp_*.sh` | earlier macOS harness (Python backends, `-O0` builds), superseded by `bench/linux/` |
| `build/mkvariant.sh` | builds `lt-trace` variants for the macOS harness |
| `patches/` | the three nginx patches, the nginx-tests change and the nginx.org docs change |
| `results/` | raw results (`*.json`, `*.jsonl`), reports and figures |

## Reproducing

### Simulation

In [psygo/nginx_sim](https://github.com/psygo/nginx_sim), from its root (Python 3, no dependencies):

    python3 -m research.pr.experiments pr --seeds 20 --n 300000
    python3 -m research.pr.experiments sweep --seeds 10 --n 200000
    python3 validate.py              # about a minute
    python3 -m research.pr.report_pr

Jobs run in parallel on all cores (`--jobs` to change it). The reports here that compare with the simulator (`report_linux.py` and the exploration branches' reports) read nginx_sim's results and use its charts: clone it next to this repository, or set `NGINX_SIM` to its path.

### Real nginx (Linux)

The benchmark expects a workspace where this branch sits next to the nginx sources:

    git clone -b least-time-evidence https://github.com/psygo/nginx_plus.git work
    git clone https://github.com/psygo/nginx_sim.git
    cd work
    git clone https://github.com/psygo/nginx_plus.git nginx
    git -C nginx worktree add ../nginx-fp     lt-fixed-point
    git -C nginx worktree add ../nginx-bucket lt-bucket
    git -C nginx worktree add ../nginx-smooth lt-smooth

`nginx/` must have `master` (the stock build is taken from it with `git archive`). Then:

    docker build -t ltbench bench/linux
    docker run -d --name ltbench -v "$PWD:/w" ltbench sleep infinity
    docker exec ltbench sh /w/bench/linux/build.sh
    docker exec ltbench sh /w/bench/linux/matrix.sh      # about 6.5 h
    python3 bench/linux/report_linux.py

The runs used Docker Desktop on an Apple M2 Max with 12 vCPUs. CPU sets are nginx 0-3, backends 4-7 and load generator 8-11, so the VM needs at least 12 CPUs. Keep the host idle while the matrix runs.

### nginx-tests

The test changes are in `patches/tests-pr2-least_time-bucket-smooth.patch`. Apply it to nginx/nginx-tests, build `lt-smooth` with default modules plus `--with-stream`, and run:

    TEST_NGINX_BINARY=/path/to/nginx prove upstream*.t stream_upstream*.t

When running as root, add `TEST_NGINX_GLOBALS="user root;"`.
