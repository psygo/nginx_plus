"""Tests for cost_routing.py.  python3 -m unittest discover -s sim/explore"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from cost_routing import SITA, WORKLOADS, classes_workload  # noqa: E402
import experiments                                     # noqa: E402
from lbsim import Sim                                  # noqa: E402


def sim_for(scenario, bal, n=20000, util=0.7, seed=1):
    sc = experiments.scen(scenario)
    bks, wl = sc["backends"](), sc["workload"]()
    return Sim(bks, bal, wl, experiments.rate_for(util, bks, wl), n, seed=seed)


class TestWorkload(unittest.TestCase):
    def draw(self, sigma, mis, n=20000):
        w = classes_workload(WORKLOADS["api3"], sigma, mis)
        rng = random.Random(7)
        return [w(rng) for _ in range(n)]

    def test_costs_paired_across_hint_quality(self):
        base = self.draw(0.0, 0.0)
        for sigma, mis in ((0.5, 0.0), (1.0, 0.0), (0.0, 0.1)):
            other = self.draw(sigma, mis)
            self.assertEqual([x[:2] for x in base], [x[:2] for x in other])

    def test_exact_hint_is_class_mean(self):
        means = [m for _, m in WORKLOADS["api3"]]
        self.assertTrue(all(est == means[c] for c, _, est in self.draw(0.0, 0.0)))

    def test_misclassification_rate(self):
        means = [m for _, m in WORKLOADS["api3"]]
        xs = self.draw(0.0, 0.1, n=50000)
        rate = sum(est != means[c] for c, _, est in xs) / len(xs)
        self.assertAlmostEqual(rate, 0.1, delta=0.01)


class TestSITA(unittest.TestCase):
    def test_group_sizes_follow_work_share(self):
        # bimodal: long class is 8 of 9.8 work units (82 %) -> 3 of 4 equal servers
        s = sim_for("cr_bimodal_eq4", SITA(), n=10)
        s.balancer._setup(s)
        self.assertEqual(sorted(s.balancer.assign.values()).count("long"), 3)

    def test_each_class_stays_in_its_group(self):
        bal = SITA()
        s = sim_for("cr_api3_het8", bal)
        s.run()
        for _, _, peer, cls in s.done:
            want = "long" if cls == bal.long_c else "short"
            self.assertEqual(bal.assign[peer], want)


if __name__ == "__main__":
    unittest.main()
