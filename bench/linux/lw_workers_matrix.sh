#!/bin/sh
# Real-nginx matrix for least_work with 4 worker processes (inside the
# container, idle host): without zone, and with zone, where least_work keeps
# its learned state in the zone (least_work_old: the prototype that learned
# per worker even with zone). 2 scenarios x 2 loads x 5 paired repetitions.
set -e
cd /w/wt/cost-routing/bench/linux
OUT=/w/wt/cost-routing/results/linux_lw_workers.jsonl
S=cr_bimodal_het8,cr_api3_het8
python3 run.py --scenarios $S --utils 0.7,0.85 --reps 5 --workers 4 \
    --variants least_conn,p123_b5_s3,least_work --out $OUT
python3 run.py --scenarios $S --utils 0.7,0.85 --reps 5 --workers 4 --zone \
    --variants least_conn,p123_b5_s3,least_work_old,least_work --out $OUT
