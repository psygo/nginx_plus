"""Tests for LinearDelay.  python3 -m unittest discover -s sim/explore"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402,F401
from speed_estimator import FT_MS, LinearDelay         # noqa: E402
from lbsim import PeerState, Request                   # noqa: E402


def feed(bal, p, samples, dt=1.0):
    """samples: list of (n_at_dispatch, rt); one completion every dt ms."""
    t = 0.0
    for n, rt in samples:
        req = Request(0, t, 0, 1.0)
        req.n_at_dispatch = n
        p.conns += 1
        t += dt
        bal.on_done(None, p, req, t, rt)
    return t


class TestLinearDelay(unittest.TestCase):
    def test_recovers_line_without_prior(self):
        bal, p = LinearDelay(smooth=1000.0, lam=1e-9), PeerState(0)
        feed(bal, p, [(n, 3.0 + 2.0 * n) for n in list(range(10)) * 50])
        a, b = bal.coef(p)
        self.assertAlmostEqual(a, 3.0, places=4)
        self.assertAlmostEqual(b, 2.0, places=4)

    def test_fcfs_line_is_unbiased_for_any_prior(self):
        # one-at-a-time server: rt = S (n + 1), so a = b = S, which is the prior's assumption
        for lam in (0.25, 1.0, 4.0, 100.0):
            bal, p = LinearDelay(smooth=1000.0, lam=lam), PeerState(0)
            feed(bal, p, [(n, 5.0 * (n + 1)) for n in list(range(6)) * 50])
            a, b = bal.coef(p)
            self.assertAlmostEqual(a, 5.0, places=6, msg=f"lam={lam}")
            self.assertAlmostEqual(b, 5.0, places=6, msg=f"lam={lam}")

    def test_no_variance_falls_back_to_service_rate_rule(self):
        bal, p = LinearDelay(smooth=1000.0, lam=1.0), PeerState(0)
        feed(bal, p, [(2, 12.0)] * 100)
        a, b = bal.coef(p)
        self.assertAlmostEqual(b, 12.0 / 3, places=6)       # mean rt / (1 + mean n)
        self.assertAlmostEqual(a + b * 2, 12.0, places=6)   # still predicts the observed rt

    def test_weight_floor_and_full_replacement(self):
        bal, p = LinearDelay(smooth=1000.0, wmin=0.01, lam=1e-9), PeerState(0)
        feed(bal, p, [(0, 10.0)])
        feed(bal, p, [(0, 20.0)], dt=0.0)            # same instant: weight is the floor
        self.assertAlmostEqual(bal.coef(p)[0], 10.0 * 0.99 + 20.0 * 0.01, places=6)
        s = bal._s(p)
        s[4] -= 5000.0                               # previous sample long ago: weight 1
        req = Request(0, 0, 0, 1.0)
        req.n_at_dispatch = 0
        p.conns += 1
        bal.on_done(None, p, req, s[4] + 5000.0, 40.0)
        self.assertAlmostEqual(bal.coef(p)[0], 40.0, places=6)

    def test_selects_lowest_prediction_and_decays_idle(self):
        bal = LinearDelay(smooth=1000.0, lam=1e-9)
        fast, slow = PeerState(0), PeerState(1)
        feed(bal, fast, [(n, 1.0 + 1.0 * n) for n in list(range(5)) * 20])
        feed(bal, slow, [(n, 9.0 + 3.0 * n) for n in list(range(5)) * 20])
        fast.conns, slow.conns = 4, 0                # 1 + 4 = 5  <  9
        self.assertIs(bal.select(None, [fast, slow], None, 100.0), fast)
        fast.conns = 9                               # 10 > 9
        self.assertIs(bal.select(None, [fast, slow], None, 100.0), slow)
        bal._s(slow)[5] = 0.0
        e0 = bal.predict(slow, 100.0)
        e1 = bal.predict(slow, FT_MS + 1000.0 + 1.0)  # idle past one decay step
        self.assertAlmostEqual(e1, e0 / 2, places=6)

    def mean_estimates(self, seed=3, **kw):
        """Time-weighted average of a + b (predicted rt with one request ahead) per server on
        slow3 (S = 20, 60, 150 ms), after the first 100 s. Each estimate counts for as long
        as it stays in effect; averaging at completion events instead would itself be biased
        for a rarely used server, which completes requests mostly when its estimate is low."""
        import experiments
        from lbsim import Sim
        sc = experiments.scen("slow3")
        bks, wl = sc["backends"](), sc["workload"]()
        bal = LinearDelay(smooth=3000.0, lam=1.0, **kw)
        hist = {0: [], 1: [], 2: []}
        orig = bal.on_done

        def on_done(sim, p, req, now, rt):
            orig(sim, p, req, now, rt)
            a, b = bal.coef(p)
            hist[p.idx].append((now, a + b))
        bal.on_done = on_done
        Sim(bks, bal, wl, experiments.rate_for(0.7, bks, wl), 60000, seed=seed).run()
        out = []
        for i in range(3):
            h = [x for x in hist[i] if x[0] > 100_000]
            out.append(sum((h[k + 1][0] - h[k][0]) * h[k][1] for k in range(len(h) - 1))
                       / (h[-1][0] - h[0][0]))
        return out

    def test_dispatch_gap_weighting_is_unbiased(self):
        # Weighting a sample by the time since the previous *completion* over-weights slow
        # requests (on a busy server that gap is the request's own service time). The gap
        # since the previous *dispatch* is known before the request runs, so no such bias.
        # Checked on the two servers with regular traffic; the rarely used one (about 2 % of
        # requests) is dominated by selection: it is picked when its estimate is low.
        for seed in (3, 4):
            avg = self.mean_estimates(seed=seed, weighting="dispatch")
            for S, a in zip((20.0, 60.0), avg):
                self.assertLess(abs(a - 2 * S) / (2 * S), 0.08, msg=f"seed {seed} S={S}: {a:.1f}")

    def test_completion_gap_weighting_is_biased_upward(self):
        # Documents the bias found while writing these tests: weighting by the time since the
        # previous completion over-estimates the busy 20 ms server by over 10 % and the 60 ms
        # one by over 30 %. patches/0003 (smooth=) weights the same way.
        avg = self.mean_estimates(weighting="completion")
        self.assertGreater(avg[0], 1.10 * 2 * 20.0)
        self.assertGreater(avg[1], 1.30 * 2 * 60.0)

if __name__ == "__main__":
    unittest.main()
