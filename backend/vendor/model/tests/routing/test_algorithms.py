import math
import unittest

from xaj_routing.algorithms import (
    linear_reservoir_step,
    muskingum_coefficients,
    runoff_mm_to_cms,
)


class AlgorithmTests(unittest.TestCase):
    def test_runoff_mm_to_cms(self):
        self.assertAlmostEqual(runoff_mm_to_cms(1.0, 1.0, 3600.0), 1000.0 / 3600.0)

    def test_linear_reservoir_exact_solution(self):
        outflow = linear_reservoir_step(
            inflow_cms=10.0,
            previous_outflow_cms=0.0,
            k_seconds=3600.0,
            time_step_seconds=3600.0,
        )
        self.assertAlmostEqual(outflow, 10.0 * (1.0 - math.exp(-1.0)))

    def test_muskingum_coefficients_sum_to_one(self):
        coeffs = muskingum_coefficients(
            k_seconds=3600.0,
            x=0.2,
            time_step_seconds=3600.0,
        )
        self.assertAlmostEqual(coeffs.c0 + coeffs.c1 + coeffs.c2, 1.0)


if __name__ == "__main__":
    unittest.main()
