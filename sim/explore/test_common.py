"""Tests for sim/explore/common.py.  python3 -m unittest discover -s sim/explore"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from lbsim import Backend, PeerState                   # noqa: E402


class FakeSim:
    def __init__(self, backends, seed=1):
        import random
        self.backends = backends
        self.rng = random.Random(seed)


class TestCommon(unittest.TestCase):
    def test_sed_true_picks_lowest_conns_per_capacity(self):
        sim = FakeSim([Backend(0, 1, 2.0), Backend(1, 4, 0.25), Backend(2, 1, 1.0)])
        peers = [PeerState(i) for i in range(3)]
        peers[0].conns, peers[1].conns, peers[2].conns = 3, 0, 1
        # (conns+1)/capacity: 4/2 = 2, 1/1 = 1, 2/1 = 2
        self.assertIs(common.SEDTrue().select(sim, peers, None, 0.0), peers[1])

    def test_robustness_scenarios_capacities(self):
        caps = [b.c * b.speed for b in common.experiments.scen("mixed4")["backends"]()]
        self.assertEqual(caps, [2.0, 1.0, 1.0, 0.5])
        discs = [b.discipline for b in common.experiments.scen("hetero8_ps")["backends"]()]
        self.assertEqual(set(discs), {"ps"})
        self.assertEqual({b.c for b in common.experiments.scen("hetero8_c4")["backends"]()}, {4})

    def test_run_job_ext_class_counts_add_up(self):
        r = common.run_job_ext(dict(scenario="bimodal4", util=0.5, balancer="least_conn",
                                    seed=1, n=5000))
        self.assertEqual(r["n"], 5000)                  # requested
        self.assertEqual(r["measured"], 4000)           # after discarding the first 20 %
        self.assertEqual(sum(v["n"] for v in r["by_class"].values()), r["measured"])
        self.assertEqual(set(r["by_class"]), {"0", "1"})
        self.assertEqual(r["rejected"], 0)


    def test_run_jobs_keeps_job_order_and_reports_progress(self):
        import io
        import contextlib
        jobs = [dict(scenario="homog8", util=u, balancer="least_conn", seed=1, n=2000)
                for u in (0.3, 0.5, 0.7, 0.9)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = common.run_jobs(jobs, "test_run_jobs.json", procs=2, label="t")
        os.remove(os.path.join(common.RESULTS, "test_run_jobs.json"))
        self.assertEqual([r["util"] for r in rows], [0.3, 0.5, 0.7, 0.9])
        want = [common.experiments.run_job(j)["p99"] for j in jobs]
        self.assertEqual([r["p99"] for r in rows], want)
        self.assertIn("t: 4/4", err.getvalue())


if __name__ == "__main__":
    unittest.main()
