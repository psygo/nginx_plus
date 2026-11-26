"""Tests for learning_augmented.py.  python3 -m unittest discover -s sim/explore"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402,F401
import learning_augmented as la                        # noqa: E402
import experiments                                     # noqa: E402
from lbsim import Sim                                  # noqa: E402


class TestPredictors(unittest.TestCase):
    def draw(self, pred, n=20000):
        w = la.workload(pred)
        rng = random.Random(5)
        return [w(rng) for _ in range(n)]

    def test_same_requests_for_every_predictor(self):
        base = [x[:2] for x in self.draw("model")]
        for pred in la.PREDICTORS[1:]:
            self.assertEqual(base, [x[:2] for x in self.draw(pred)], msg=pred)

    def test_model_recovers_route_means(self):
        # routes without a size effect (gamma = 0): every bucket estimates the route mean
        table = la.model()
        for r, (_, _, g) in enumerate(la.ROUTES):
            if g == 0.0:
                vals = [v for (rr, _), v in table.items() if rr == r]
                for v in vals:
                    self.assertLess(abs(v / la._route_mean(r) - 1), 0.15, msg=f"route {r}")

    def test_model_beats_route_mean(self):
        xs = self.draw("model", 50000)
        ys = self.draw("route", 50000)
        err = lambda zs: sum((est - c) ** 2 for _, c, est in zs) / len(zs)  # noqa: E731
        self.assertLess(err(xs), err(ys))

    def test_inverted_swaps_cheapest_and_costliest(self):
        means = [la._route_mean(r) for r in range(len(la.ROUTES))]
        lo, hi = min(range(len(means)), key=means.__getitem__), max(range(len(means)), key=means.__getitem__)
        for r, _, est in self.draw("inverted", 5000):
            if r == lo:
                self.assertEqual(est, means[hi])
            if r == hi:
                self.assertEqual(est, means[lo])


class Checked(la.Augmented):
    """Asserts the floor: the choice is within (1 + eps) of least_time's best score."""
    def select(self, sim, peers, req, now):
        etas = {p.idx: self.eta(sim, p, now, self.bucket) / p.weight for p in peers}
        p = super().select(sim, peers, req, now)
        best = min(etas.values())
        assert etas[p.idx] <= best * (1 + self.eps) + 1e-9, (etas, p.idx, self.eps)
        self.checked = getattr(self, "checked", 0) + 1
        return p


class TestFloor(unittest.TestCase):
    def test_choice_stays_in_least_time_shortlist(self):
        for eps in (0.0, 0.25, 1.0):
            for pred in ("model", "inverted"):
                bal = Checked(eps=eps)
                sc = experiments.scen(f"ai_het4_{pred}")
                bks, wl = sc["backends"](), sc["workload"]()
                Sim(bks, bal, wl, experiments.rate_for(0.85, bks, wl), 10000, seed=2).run()
                self.assertEqual(bal.checked, 10000)


if __name__ == "__main__":
    unittest.main()
