#!/bin/sh
# Real-nginx matrix for the least_work prototype (inside the container, idle host):
# 4 cost-routing scenarios x 2 loads x 4 variants x 5 paired repetitions.
set -e
cd /w/wt/cost-routing/bench/linux
python3 run.py --scenarios cr_bimodal_het4,cr_bimodal_het8,cr_api3_het4,cr_api3_het8 \
    --utils 0.7,0.85 --reps 5 --variants least_conn,lt,p123_b5_s3,least_work \
    --out /w/wt/cost-routing/results/linux_lw.jsonl
