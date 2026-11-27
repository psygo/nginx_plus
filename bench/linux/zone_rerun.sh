#!/bin/sh
# Re-run the disturbed runs of zone_matrix.sh (same seeds). Inside the container.
set -e
D=$(cd "$(dirname "$0")" && pwd)
cd "$D"
python3 run.py --scenarios homog8 --utils 0.85 --variants lt --reps 1 --rep-start 3 --workers 1 --out ../../results/linux_zone.jsonl
python3 run.py --scenarios fast3 --utils 0.85 --variants least_conn --reps 1 --rep-start 4 --workers 4 --zone --out ../../results/linux_zone.jsonl
python3 run.py --scenarios hetero8 --utils 0.85 --variants p123_b5_s3 --reps 1 --rep-start 4 --workers 4 --zone --out ../../results/linux_zone.jsonl
python3 run.py --scenarios homog8 --utils 0.85 --variants least_conn --reps 1 --rep-start 4 --workers 4 --zone --out ../../results/linux_zone.jsonl
python3 run.py --scenarios hetero8 --utils 0.85 --variants least_conn --reps 1 --rep-start 4 --workers 4 --out ../../results/linux_zone.jsonl
python3 run.py --scenarios hetero8 --utils 0.85 --variants p123_b5_s3 --reps 1 --rep-start 4 --workers 4 --out ../../results/linux_zone.jsonl
python3 run.py --scenarios hetero8 --utils 0.85 --variants least_conn --reps 1 --rep-start 4 --workers 4 --no-reuseport --out ../../results/linux_zone.jsonl
python3 run.py --scenarios hetero8 --utils 0.85 --variants lt --reps 1 --rep-start 5 --workers 4 --no-reuseport --out ../../results/linux_zone.jsonl
python3 run.py --scenarios hetero8 --utils 0.85 --variants p123_b5_s3 --reps 1 --rep-start 5 --workers 4 --no-reuseport --out ../../results/linux_zone.jsonl
python3 run.py --scenarios homog8 --utils 0.85 --variants least_conn --reps 1 --rep-start 5 --workers 4 --no-reuseport --out ../../results/linux_zone.jsonl
python3 run.py --scenarios hetero8 --utils 0.85 --variants p123_b5_s3 --reps 1 --rep-start 5 --workers 1 --out ../../results/linux_zone.jsonl
