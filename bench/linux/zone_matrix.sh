#!/bin/sh
# Multiple worker processes with and without `zone` (and with and without
# `reuseport`), interleaved by repetition so slow drift affects all configs alike.
# Run inside the container:  sh /w/wt/zone-workers/bench/linux/zone_matrix.sh
set -e
D=$(cd "$(dirname "$0")" && pwd)
R=$D/../../results
OUT=$R/linux_zone.jsonl
COMMON="--scenarios fast3,hetero8,homog8 --utils 0.85 --variants least_conn,lt,p123_b5_s3 --reps 1"
cd "$D"
for rep in 1 2 3 4 5; do
  python3 run.py $COMMON --rep-start $rep --workers 4 --zone --out $OUT
  python3 run.py $COMMON --rep-start $rep --workers 4 --out $OUT
  python3 run.py $COMMON --rep-start $rep --workers 4 --no-reuseport --out $OUT
  python3 run.py $COMMON --rep-start $rep --workers 1 --out $OUT
done
