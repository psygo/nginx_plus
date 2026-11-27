"""Tests for cost_routing.py.  python3 -m unittest discover -s sim/explore"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common                                          # noqa: E402
from cost_routing import SITA, LearnedCost, LeastWorkC, WORKLOADS, classes_workload  # noqa: E402
from cost_routing import split_shares, split_table  # noqa: E402
from common import PerWorker  # noqa: E402
from lbsim import PeerState, Request                   # noqa: E402
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


def done(bal, p, cls, rt, busy=0, now=0.0):
    """One completed request of route `cls` on peer p that found `busy` others in progress."""
    req = Request(0, now, cls, 1.0)
    req.dispatch = now
    p.conns += busy
    bal.on_dispatch(None, p, req, now)
    p.conns -= busy
    bal.on_done(None, p, req, now + rt, rt)


class TestLeastWorkC(unittest.TestCase):
    """LearnedCost as cmodule/ngx_http_upstream_least_work_module computes it."""

    def test_near_ties_round_robin(self):
        # equal servers whose estimates differ by less than 1 ms (whole-ms response
        # times make such differences noise): alternate, as least_conn would
        bal, peers = LeastWorkC(), [PeerState(0), PeerState(1)]
        for _ in range(50):
            done(bal, peers[0], 0, 10.0)
            done(bal, peers[1], 0, 10.4)
        picks = [bal.select(None, peers, Request(i, 0.0, 0, 1.0), 0.0).idx for i in range(10)]
        self.assertEqual(picks.count(0), 5)

    def test_a_real_difference_still_wins(self):
        bal, peers = LeastWorkC(), [PeerState(0), PeerState(1)]
        for _ in range(300):
            done(bal, peers[0], 0, 10.0)
            done(bal, peers[1], 0, 30.0)
        picks = [bal.select(None, peers, Request(i, 0.0, 0, 1.0), 0.0).idx for i in range(10)]
        self.assertEqual(picks, [0] * 10)

    def test_zero_ms_responses_teach(self):
        # nginx measures whole milliseconds: a sub-millisecond server reads 0 ms most of
        # the time. Its zeros are valid samples (the difference of two truncated clocks is
        # unbiased); skipping them left a fast server at the default speed.
        bal, peers = LeastWorkC(alpha=0.05), [PeerState(0), PeerState(1)]
        for i in range(400):
            done(bal, peers[0], 0, 200.0)
            done(bal, peers[1], 0, 0.0)                     # always under 1 ms
        self.assertGreater(bal.speed(peers[1]) / bal.speed(peers[0]), 20.0)
        picks = [bal.select(None, peers, Request(i, 0.0, 0, 1.0), 0.0).idx for i in range(10)]
        self.assertEqual(picks, [1] * 10)

    def test_speed_is_bounded_by_a_half_millisecond_floor(self):
        # all-zero samples must not make a speed (and the mean default) unbounded
        bal, peers = LeastWorkC(alpha=0.05), [PeerState(0), PeerState(1)]
        for _ in range(2000):
            done(bal, peers[0], 0, 10.0)
            done(bal, peers[1], 0, 0.0)
        self.assertLessEqual(bal.speed(peers[1]), bal.num[1] / 0.5 + 1e-9)

    def test_a_zero_cost_route_does_not_zero_a_speed(self):
        # a route that always reads 0 ms learns cost 0, which says nothing about speed
        bal, peers = LeastWorkC(alpha=0.05), [PeerState(0), PeerState(1)]
        for _ in range(50):
            done(bal, peers[0], 0, 0.0)
            done(bal, peers[1], 0, 0.0)
        for p in peers:
            self.assertGreater(bal.speed(p), 0.0)
        bal.select(None, peers, Request(0, 0.0, 0, 1.0), 0.0)

    def test_unlearned_server_gets_the_arithmetic_mean_speed(self):
        bal, peers = LeastWorkC(alpha=0.05), [PeerState(i) for i in range(3)]
        for _ in range(500):
            done(bal, peers[0], 0, 5.0)
            done(bal, peers[1], 0, 20.0)
        v0, v1 = bal.speed(peers[0]), bal.speed(peers[1])
        self.assertAlmostEqual(bal.speed(peers[2]), (v0 + v1) / 2, places=9)


class TestRobustScenarios(unittest.TestCase):
    def test_zipf50_workload(self):
        classes = WORKLOADS["zipf50"]
        self.assertEqual(len(classes), 50)
        self.assertAlmostEqual(sum(p for p, _ in classes), 1.0, places=9)
        self.assertTrue(all(1.0 <= m <= 200.0 for _, m in classes))
        self.assertGreater(classes[0][0], 10 * classes[-1][0])          # popular vs rare routes

    def test_backend_kinds_keep_the_group_capacity(self):
        for kind in ("", "_ps", "_c4", "_mix"):
            bks = experiments.scen("cr_api3_het4" + kind)["backends"]()
            self.assertAlmostEqual(sum(b.c * b.speed for b in bks), 4.5, msg=kind)
        kinds = [(b.c, b.discipline) for b in experiments.scen("cr_api3_het4_mix")["backends"]()]
        self.assertEqual(kinds, [(1, "fcfs"), (1, "ps"), (4, "fcfs"), (4, "ps")])


class TestLearnedCost(unittest.TestCase):
    """Least outstanding work with route costs and server speeds learned from response
    times alone: no cost hint from the application, no configured capacity."""

    def test_learns_route_costs_and_server_speeds(self):
        # speeds 2 and 1, routes of 2 ms and 80 ms (at speed 1): idle rt = cost / speed
        bal, peers = LearnedCost(alpha=0.05), [PeerState(0), PeerState(1)]
        rng = random.Random(3)
        for _ in range(4000):
            i, r = rng.randrange(2), rng.randrange(2)
            done(bal, peers[i], r, (2.0, 80.0)[r] / (2.0, 1.0)[i])
        for i, v in enumerate((2.0, 1.0)):
            for r, k in enumerate((2.0, 80.0)):
                self.assertAlmostEqual(bal.predict(peers[i], r), k / v, delta=0.02 * k / v)

    def test_only_idle_requests_teach(self):
        # a request that queued behind others measures their work too: no sample
        bal, p = LearnedCost(alpha=0.05), PeerState(0)
        done(bal, p, 0, 10.0)
        before = bal.predict(p, 0)
        done(bal, p, 0, 500.0, busy=3)
        self.assertEqual(bal.predict(p, 0), before)

    def test_picks_least_outstanding_work(self):
        bal, peers = LearnedCost(alpha=0.05), [PeerState(0), PeerState(1)]
        for p in peers:
            for _ in range(50):
                done(bal, p, 0, 2.0)
                done(bal, p, 1, 80.0)
        req = Request(1, 0.0, 1, 1.0)                   # an 80 ms request is in progress on 0
        req.dispatch = 0.0
        bal.on_dispatch(None, peers[0], req, 0.0)
        new = Request(2, 0.0, 0, 1.0)
        self.assertIs(bal.select(None, peers, new, 0.0), peers[1])
        for _ in range(30):                            # 30 cheap ones in progress on 1: 60 ms
            r = Request(3, 0.0, 0, 1.0)
            r.dispatch = 0.0
            bal.on_dispatch(None, peers[1], r, 0.0)
        self.assertIs(bal.select(None, peers, new, 0.0), peers[1])     # 60 + 2 < 80 + 2
        for _ in range(11):                            # 41 cheap ones: 82 ms
            r = Request(3, 0.0, 0, 1.0)
            r.dispatch = 0.0
            bal.on_dispatch(None, peers[1], r, 0.0)
        self.assertIs(bal.select(None, peers, new, 0.0), peers[0])     # 80 + 2 < 82 + 2

    def test_unknown_route_gets_the_average_cost(self):
        bal, p = LearnedCost(alpha=0.05), PeerState(0)
        for _ in range(200):
            done(bal, p, 0, 2.0)
            done(bal, p, 1, 20.0)
        self.assertAlmostEqual(bal.predict(p, 7), 11.0, delta=1.0)

    def test_unlearned_server_gets_the_average_learned_speed(self):
        # speeds are known up to a common scale; a server with no sample yet must be on
        # that scale, not at an arbitrary 1.0 (it looked 5-10x too fast, and was flooded)
        bal, peers = LearnedCost(alpha=0.05), [PeerState(i) for i in range(3)]
        for _ in range(500):
            done(bal, peers[0], 0, 5.0)
            done(bal, peers[1], 0, 20.0)
        v0, v1 = bal.speed(peers[0]), bal.speed(peers[1])
        self.assertAlmostEqual(v0 / v1, 4.0, delta=0.1)
        self.assertAlmostEqual(bal.speed(peers[2]), (v0 * v1) ** 0.5, places=9)

    def test_first_sample_moves_the_speed_gradually(self):
        # one sample used to set the speed outright: a single short response made a
        # server look 36x faster, and it was never idle again to correct it
        bal, peers = LearnedCost(alpha=0.05), [PeerState(0), PeerState(1)]
        for _ in range(500):
            done(bal, peers[0], 0, 10.0)
        before = bal.speed(peers[1])
        done(bal, peers[1], 0, 0.5)                    # 20x shorter than expected
        self.assertLess(bal.speed(peers[1]) / before, 1.0 + 20 * 0.05 + 1e-9)

    def test_calibration_corrects_a_server_slower_than_predicted(self):
        # a server that is never idle never teaches its speed; the calibration factor,
        # learned from every completion, scales its predictions to what it delivers
        bal, p = LearnedCost(alpha=0.05), PeerState(0)
        for _ in range(100):
            done(bal, p, 0, 10.0)                      # idle: 10 ms
        pred = bal.predict(p, 0)
        for _ in range(300):                           # busy: 10x the predicted delay
            req = Request(0, 0.0, 0, 1.0)
            req.dispatch = 0.0
            p.conns += 1
            bal.on_dispatch(None, p, req, 0.0)
            p.conns -= 1
            bal.on_done(None, p, req, 10 * pred, 10 * pred)
        self.assertAlmostEqual(bal.calibration(p), 10.0, delta=0.5)

    def test_calibration_speed_is_its_own_parameter(self):
        def factor_after(cal_alpha, n=20):
            bal, p = LearnedCost(alpha=0.05, cal_alpha=cal_alpha), PeerState(0)
            for _ in range(100):
                done(bal, p, 0, 10.0)
            pred = bal.predict(p, 0)
            for _ in range(n):
                req = Request(0, 0.0, 0, 1.0)
                req.dispatch = 0.0
                p.conns += 1
                bal.on_dispatch(None, p, req, 0.0)
                p.conns -= 1
                bal.on_done(None, p, req, 10 * pred, 10 * pred)
            return bal.calibration(p)
        for c in (0.05, 0.005):                         # measured 10x predicted, 20 times
            self.assertAlmostEqual(factor_after(c), 1 + 9 * (1 - (1 - c) ** 20), places=6)
        self.assertEqual(LearnedCost(alpha=0.03).cal_alpha, 0.03)       # default: alpha

    def test_no_runaway_on_the_failing_seeds(self):
        # 20,000-request versions of the runaways in results/cost_routing.md (robustness):
        # 35x, 9.5x and 2.8x the p99 of patched least_time before the fixes
        for sc, seed in (("cr_zipf50_het4_ps", 9), ("cr_zipf50_het8_c4", 6), ("cr_zipf50_het8_c4", 3)):
            job = dict(scenario=sc, util=0.7, seed=seed, n=20000)
            a = common.run_job_ext(dict(job, balancer="learned_cost"))["p99"]
            b = common.run_job_ext(dict(job, balancer="p123_b5_s3"))["p99"]
            self.assertLess(a / b, 1.1, msg=f"{sc} seed {seed}: {a / b:.2f}")

    def test_key_from_the_hint(self):
        # key="hint": the route a request looks like (est_cost), which can be wrong
        bal = LearnedCost(key="hint")
        req = Request(0, 0.0, 1, 1.0)
        req.est_cost = 2.0
        self.assertEqual(bal.key(req), 2.0)
        self.assertEqual(LearnedCost().key(req), 1)


class TestSplit(unittest.TestCase):
    def test_shares_group_servers_by_speed_and_sum_seeds(self):
        rows = [dict(scenario="cr_x_het4", util=0.7, balancer="b", speeds=[2, 1, 1, 0.5],
                     by_class_peer={"0": {"0": 1, "1": 2, "2": 3, "3": 4}, "1": {"0": 3}}),
                dict(scenario="cr_x_het4", util=0.7, balancer="b", speeds=[2, 1, 1, 0.5],
                     by_class_peer={"0": {"3": 10}, "1": {"0": 1}})]
        sh = split_shares(rows)[("cr_x_het4", 0.7, "b")]
        self.assertAlmostEqual(sh["0"][2], 1 / 20)
        self.assertAlmostEqual(sh["0"][1], 5 / 20)
        self.assertAlmostEqual(sh["0"][0.5], 14 / 20)
        self.assertEqual(sh["1"], {2: 1.0})
        # the slowest speed is found even though class 1 never reached it
        self.assertEqual(split_table(rows)[2], "| x_het4 | 0.7 | b | 5 % / 70 % | 100 % / 0 % |")


class TestPerWorker(unittest.TestCase):
    def sim(self, bal, workers, shared, n=20000):
        sc = experiments.scen("cr_api3_het4")
        bks, wl = sc["backends"](), sc["workload"]()
        return Sim(bks, bal, wl, experiments.rate_for(0.7, bks, wl), n, workers=workers,
                   shared=shared, seed=3)

    def test_one_worker_is_the_plain_balancer(self):
        a = self.sim(LeastWorkC(), 1, True)
        a.run()
        b = self.sim(PerWorker(LeastWorkC), 1, True)
        b.run()
        self.assertEqual(a.done, b.done)

    def test_each_worker_learns_alone(self):
        pw = PerWorker(LeastWorkC)
        s = self.sim(pw, 4, False)
        s.run()
        self.assertEqual(sorted(pw.inst), [0, 1, 2, 3])
        for b in pw.inst.values():
            self.assertTrue(b.kappa)                         # every worker learned routes
            for w in b.work.values():
                self.assertAlmostEqual(w, 0.0, places=6)     # its own work only, drained
        # private learning differs from shared learning
        k = [round(b.kappa[0], 6) for b in pw.inst.values()]
        self.assertGreater(len(set(k)), 1)

    def test_upstream_zone_shares_nginx_peer_state_only(self):
        pw = PerWorker(LeastWorkC)
        s = self.sim(pw, 4, True)
        s.run()
        self.assertEqual(len(s.states), 1)                   # conns and weights shared
        self.assertEqual(len(pw.inst), 4)                    # learned state per worker
        self.assertEqual(sum(p.conns for p in s.states[0]), 0)


if __name__ == "__main__":
    unittest.main()
