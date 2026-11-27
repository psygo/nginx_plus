"""Tests for report_zone.py.  python3 -m unittest discover -s bench/linux -p 'test_*.py'"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report_zone                                     # noqa: E402


class TestReportZone(unittest.TestCase):
    def test_config_labels(self):
        self.assertEqual(report_zone.cfg(dict(workers=1)), "1 worker")
        self.assertEqual(report_zone.cfg(dict(workers=4, zone=True)), "4 workers + zone")
        self.assertEqual(report_zone.cfg(dict(workers=4, zone=False, reuseport=True)),
                         "4 workers, no zone")
        self.assertEqual(report_zone.cfg(dict(workers=4, zone=False, reuseport=False)),
                         "4 workers, no zone, no reuseport")


if __name__ == "__main__":
    unittest.main()
