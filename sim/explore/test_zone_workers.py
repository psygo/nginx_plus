"""Tests for the zone/workers helpers.  python3 -m unittest discover -s sim/explore"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "bench", "linux"))
import common                                          # noqa: E402
import report_zone                                     # noqa: E402


class TestZone(unittest.TestCase):
    def test_config_labels(self):
        self.assertEqual(report_zone.cfg(dict(workers=1)), "1 worker")
        self.assertEqual(report_zone.cfg(dict(workers=4, zone=True)), "4 workers + zone")
        self.assertEqual(report_zone.cfg(dict(workers=4, zone=False, reuseport=True)),
                         "4 workers, no zone")
        self.assertEqual(report_zone.cfg(dict(workers=4, zone=False, reuseport=False)),
                         "4 workers, no zone, no reuseport")

    def test_private_state_per_worker_in_sim(self):
        # without zone each worker has its own peer state; with zone there is one
        r1 = common.experiments.run_job(dict(scenario="homog8", util=0.5, balancer="least_conn",
                                             seed=1, n=5000, workers=4, shared=False))
        self.assertEqual(r1["workers"], 4)
        from lbsim import Backend, LeastConn, Sim
        s = Sim([Backend(i) for i in range(2)], LeastConn(),
                common.experiments.exp_workload(10.0), 50.0, 10, workers=4, shared=False)
        self.assertEqual(len(s.states), 4)
        s = Sim([Backend(i) for i in range(2)], LeastConn(),
                common.experiments.exp_workload(10.0), 50.0, 10, workers=4, shared=True)
        self.assertEqual(len(s.states), 1)


if __name__ == "__main__":
    unittest.main()
