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

    def test_slope_floor_keeps_connections_in_the_estimate(self):
        # On a CPU-sharing server in overload, response time can *fall* with the connections
        # seen at dispatch (requests sent early sit through the flood that follows). The
        # fitted slope then clamps to 0, the estimate stops depending on connections, and
        # every request goes to one server (results/speed_estimator.md, slowdown_ps).
        samples = [(n, 50.0 - 5.0 * n) for n in list(range(9)) * 20]
        bal, p = LinearDelay(smooth=1000.0, lam=1e-9), PeerState(0)
        feed(bal, p, samples)
        self.assertEqual(bal.coef(p)[1], 0.0)                 # the defect
        bal, p = LinearDelay(smooth=1000.0, lam=1e-9, floor=0.25), PeerState(0)
        feed(bal, p, samples)
        a, b = bal.coef(p)
        self.assertGreater(a, 0.0)
        self.assertAlmostEqual(b, 0.25 * a, places=6)
        mean_n, mean_rt = bal._s(p)[0], bal._s(p)[1]          # the estimator's weighted means
        self.assertAlmostEqual(a + b * mean_n, mean_rt, places=6)   # still fits the mean

    def test_slope_floor_leaves_a_multiworker_line_alone(self):
        # 4 workers: rt ~ S + S/4 * n, so b = a/4 must stay learnable with floor 0.2
        bal, p = LinearDelay(smooth=1000.0, lam=1e-9, floor=0.2), PeerState(0)
        feed(bal, p, [(n, 8.0 + 2.0 * n) for n in list(range(10)) * 50])
        a, b = bal.coef(p)
        self.assertAlmostEqual(a, 8.0, places=4)
        self.assertAlmostEqual(b, 2.0, places=4)

    def test_average_load_regressor(self):
        # A is sent to an empty server at t=0; B at t=10, C at t=20; A completes at t=30.
        # Other requests in progress during A's life: 0, then 1, then 2, 10 ms each, so the
        # average is 1.0, while the count at dispatch is 0.
        for regressor, expected in (("dispatch", 0.0), ("avg", 1.0)):
            bal, p = LinearDelay(smooth=1000.0, lam=1e-9, regressor=regressor), PeerState(0)
            reqs = [Request(i, t, 0, 1.0) for i, t in enumerate((0.0, 10.0, 20.0))]
            for r in reqs:
                r.dispatch = r.arrival
                bal.on_dispatch(None, p, r, r.arrival)
            bal.on_done(None, p, reqs[0], 30.0, 30)
            self.assertAlmostEqual(bal._s(p)[0], expected, places=9, msg=regressor)
            self.assertEqual(p.conns, 2)

    @staticmethod
    def late_mean_after_overload(bal, seed, servers=2, workers=1):
        """CPU-sharing servers at 90 % load; server 0 runs at 1/4 speed from 5 to 8 s, which
        overloads the group. A balancer that keeps working drains the backlog within about
        8 s; returns the mean latency of requests sent in the last 10 s of a 30 s run."""
        import experiments
        from lbsim import Backend, Sim, exp_workload
        bks = [Backend(i, workers, 1.0, "ps") for i in range(servers)]
        wl = exp_workload(10.0)
        rate = experiments.rate_for(0.9, bks, wl)
        ev = [(5000.0, lambda s, now: s.backends[0].set_speed(s, 0.25, now)),
              (8000.0, lambda s, now: s.backends[0].set_speed(s, 1.0, now))]
        sim = Sim(bks, bal, wl, rate, int(rate * 30), seed=seed, events=ev, warmup=0.0)
        sim.run()
        late = [x[1] for x in sim.done if x[0] > 20000.0]
        return sum(late) / len(late)

    def test_slope_floor_prevents_herding_after_overload(self):
        kw = dict(smooth=10000.0, lam=4.0, tol=0.05, weighting="dispatch")
        for seed in (1, 2):
            # without the floor the slope collapses and requests herd onto one server at a
            # time; the backlog never drains (seconds of latency 12 s after the overload)
            self.assertGreater(self.late_mean_after_overload(LinearDelay(**kw), seed), 1000.0)
            self.assertLess(self.late_mean_after_overload(LinearDelay(floor=0.25, **kw), seed), 100.0)
        self.assertLess(self.late_mean_after_overload(LinearDelay(floor=0.25, **kw), 1, 4, 4), 50.0)

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
