#!/bin/sh
# Real-nginx matrix for the stream least_work module (inside the container,
# idle host): a stream proxy with one listen port per request class and one
# session per request, 1 worker. 2 scenarios x 2 loads x 5 paired
# repetitions, against least_conn and PR 2's least_time (first_byte).
set -e
cd /w/wt/cost-routing/bench/linux
python3 run.py --scenarios cr_bimodal_het4,cr_api3_het8 --utils 0.7,0.85 --reps 5 \
    --variants stream_least_conn,stream_p123_b5_s3,stream_least_work \
    --out /w/wt/cost-routing/results/linux_lw_stream.jsonl
