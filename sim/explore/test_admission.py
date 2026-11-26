"""Tests for admission.py against Erlang B and Erlang C.
python3 -m unittest discover -s sim/explore"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
import admission                                       # noqa: E402,F401
from validate import mmc                               # noqa: E402


def erlang_b(c, a):
    b = 1.0
    for k in range(1, c + 1):
        b = a * b / (k + a * b)
    return b


def run(policy, util, n=200_000, **kw):
    """homog8: 8 identical one-at-a-time servers, exponential 10 ms."""
    r = common.run_job_ext(dict(scenario="homog8", util=util, balancer="p123_b5_s3", seed=11,
                                n=n, sim="admission", sim_kw=dict(policy=policy, **kw)))
    return r


class TestAdmission(unittest.TestCase):
    def test_no_limit_rejects_nothing(self):
        self.assertEqual(run("none", 0.85, n=20000)["rejected"], 0)

    def test_reject_at_one_per_server_is_erlang_b(self):
        # max_conns=1 and no queue: an M/M/8/8 loss system
        for util in (0.6, 0.85):
            got = run("reject", util, max_conns=1)["rejected"]
            want = erlang_b(8, 8 * util)
            self.assertLess(abs(got - want) / want, 0.06, msg=f"util={util}: {got:.4f} vs {want:.4f}")

    def test_queue_at_one_per_server_is_erlang_c(self):
        # max_conns=1 and an unlimited queue in nginx: M/M/8 with one shared queue
        for util in (0.7, 0.9):
            r = run("queue", util, max_conns=1, timeout=math.inf)
            want = mmc(8, util, 10.0)[0]
            self.assertEqual(r["rejected"], 0)
            self.assertLess(abs(r["mean"] - want) / want, 0.03,
                            msg=f"util={util}: {r['mean']:.2f} vs {want:.2f}")

    def test_queue_with_zero_timeout_behaves_like_reject(self):
        got = run("queue", 0.85, max_conns=1, timeout=0.0)["rejected"]
        want = erlang_b(8, 8 * 0.85)
        self.assertLess(abs(got - want) / want, 0.06)


if __name__ == "__main__":
    unittest.main()
