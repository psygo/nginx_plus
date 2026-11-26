"""Tests for smooth_weighting.py.  python3 -m unittest discover -s sim/explore

patches/0003 (smooth=) weights each response by the time since the server's previous
response. On a busy server that gap is roughly the response's own service time, so slow
responses get more weight and the average is biased upward (an inspection-paradox effect).
The alternative takes the gap between dispatches, known before the request runs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402,F401
import experiments                                     # noqa: E402
from lbsim import LeastTimePatch, Sim                  # noqa: E402
from smooth_weighting import LeastTimeDispatchGap, LeastTimeTypicalGap  # noqa: E402


def time_avg_bias(bal, scenario="slow3", util=0.7, seed=3, n=60000):
    """Per server: time-weighted average of the balancer's average response time, divided by
    the true mean response time of the requests it got, minus 1. After the first 100 s."""
    sc = experiments.scen(scenario)
    bks, wl = sc["backends"](), sc["workload"]()
    hist, rts = {}, {}
    orig = bal.on_done

    def on_done(sim, p, req, now, rt):
        orig(sim, p, req, now, rt)
        hist.setdefault(p.idx, []).append((now, p.header_time / 64))
        if req.arrival > 100_000:
            rts.setdefault(p.idx, []).append(rt)
    bal.on_done = on_done
    Sim(bks, bal, wl, experiments.rate_for(util, bks, wl), n, seed=seed).run()
    out = {}
    for i, h in hist.items():
        h = [x for x in h if x[0] > 100_000]
        tw = sum((h[k + 1][0] - h[k][0]) * h[k][1] for k in range(len(h) - 1)) / (h[-1][0] - h[0][0])
        out[i] = tw / (sum(rts[i]) / len(rts[i])) - 1
    return out


class TestSmoothWeighting(unittest.TestCase):
    # Measured biases (time-weighted average vs true mean rt), slow3 at 70 %, servers
    # 20/60/150 ms: fixed 0.05 -2/+2/+7 %; patch 3 +7/+24/+32 %; dispatch gap -8/-14/-18 %.

    def test_fixed_weight_is_unbiased(self):
        b = time_avg_bias(LeastTimePatch(bucket=5))
        for i in (0, 1):
            self.assertLess(abs(b[i]), 0.04, msg=b)

    def test_patch3_weighting_over_estimates(self):
        b = time_avg_bias(LeastTimePatch(bucket=5, smooth=3000))
        self.assertGreater(b[1], 0.15, msg=b)          # 60 ms server

    def test_dispatch_gap_weighting_under_estimates(self):
        # the gap before dispatch correlates with load at dispatch: a request sent soon
        # after the previous one finds a busier server (longer rt) and gets less weight
        b = time_avg_bias(LeastTimeDispatchGap(bucket=5, smooth=3000))
        self.assertLess(b[1], -0.05, msg=b)

    def test_typical_gap_weighting_is_unbiased(self):
        # weight from the average of earlier gaps: independent of the sample it weights
        for seed in (3, 4):
            b = time_avg_bias(LeastTimeTypicalGap(bucket=5, smooth=3000), seed=seed)
            for i in (0, 1):
                self.assertLess(abs(b[i]), 0.05, msg=f"seed {seed}: {b}")

    def test_same_as_patch3_without_smooth(self):
        # without smooth both use the fixed weight 0.05: identical decisions
        a = experiments.run_job(dict(scenario="fast3", util=0.7, balancer="p12_b5", seed=1, n=20000))
        for name in ("pd_b5_s0", "pt_b5_s0"):
            b = experiments.run_job(dict(scenario="fast3", util=0.7, balancer=name, seed=1, n=20000))
            self.assertEqual((a["p99"], a["mean"]), (b["p99"], b["mean"]), msg=name)

    def test_idle_server_full_replacement(self):
        # a dispatch gap of at least `smooth` replaces the average, as in patch 3
        bal = LeastTimeDispatchGap(bucket=5, smooth=3000)
        self.assertEqual(bal.gap_weight(5000.0), 1.0)
        self.assertEqual(bal.gap_weight(0.0), 0.01)
        self.assertAlmostEqual(bal.gap_weight(300.0), 0.1)

    def test_typical_gap_weight_ignores_current_gap(self):
        bal = LeastTimeTypicalGap(bucket=5, smooth=3000)
        from lbsim import PeerState
        p = PeerState(0)
        p.svc = 300.0                                  # typical gap 300 ms -> weight 0.1
        self.assertAlmostEqual(bal.weight_for(p, 50.0), 0.1)
        self.assertAlmostEqual(bal.weight_for(p, 2000.0), 0.1)
        self.assertEqual(bal.weight_for(p, 3000.0), 1.0)  # idle for smooth: replace


if __name__ == "__main__":
    unittest.main()
