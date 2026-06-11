"""Tests for splines.py, runnable without Blender: python3 tests/test_splines.py"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from splines import catmull_rom_even
from mocks import Vec


def quarter_circle(count):
    return [Vec(math.cos(math.pi / 2 * i / count),
                math.sin(math.pi / 2 * i / count)) for i in range(count + 1)]


def test_endpoints_exact():
    points = quarter_circle(8)
    out = catmull_rom_even(points, 4)
    assert out[0] is points[0] or (out[0] - points[0]).length < 1e-12
    assert (out[-1] - points[-1]).length < 1e-12


def test_spline_hugs_curvature_better_than_chords():
    # Resample a coarse quarter circle down to 3 segments: spline points
    # should sit close to the unit circle, unlike chord midpoints.
    points = quarter_circle(8)
    out = catmull_rom_even(points, 3)
    for p in out:
        radius = math.sqrt(p.x ** 2 + p.y ** 2)
        assert abs(radius - 1.0) < 0.01, radius


def test_spacing_is_even():
    points = quarter_circle(16)
    out = catmull_rom_even(points, 4)
    gaps = [(out[k + 1] - out[k]).length for k in range(4)]
    assert max(gaps) - min(gaps) < 0.02 * max(gaps), gaps


def test_straight_line_stays_straight():
    points = [Vec(x, 0) for x in (0, 1, 3, 4, 7)]
    out = catmull_rom_even(points, 4)
    for p in out:
        assert abs(p.y) < 1e-9
    xs = [p.x for p in out]
    assert xs == sorted(xs) and abs(xs[2] - 3.5) < 0.05, xs


def test_short_input_falls_back_to_chords():
    points = [Vec(0, 0), Vec(1, 0)]
    out = catmull_rom_even(points, 2)
    assert len(out) == 3 and abs(out[1].x - 0.5) < 1e-9


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
