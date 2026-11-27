"""Locate the simulator, github.com/psygo/nginx_sim: its package (charts, statistics)
and its results (research/results), which the reports compare with real nginx.

$NGINX_SIM if set, else a checkout named nginx_sim next to this repository or next to
one of the directories above it. Importing this module puts the checkout on sys.path,
so that `import nginx_sim` and `import research` work.
"""
import os
import sys


def _find():
    env = os.environ.get("NGINX_SIM")
    if env:
        return os.path.abspath(env)
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        cand = os.path.join(d, "nginx_sim")
        if os.path.isfile(os.path.join(cand, "nginx_sim", "__init__.py")):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            sys.exit("nginx_sim not found: clone https://github.com/psygo/nginx_sim next to "
                     "this repository, or set NGINX_SIM to its path")
        d = parent


ROOT = _find()
RESULTS = os.path.join(ROOT, "research", "results")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
