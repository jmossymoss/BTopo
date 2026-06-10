"""Tests for patch_fill.py, runnable without Blender: python3 tests/test_patch_fill.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patch_fill import coons_interior, split_loop_sides
from mocks import Vec


def square_loop(per_side):
    """Closed loop around the unit square, `per_side` edges per side."""
    points = []
    corners = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for k in range(4):
        ax, ay = corners[k]
        bx, by = corners[(k + 1) % 4]
        for s in range(per_side):
            t = s / per_side
            points.append(Vec(ax + (bx - ax) * t, ay + (by - ay) * t))
    return points


def test_split_square_finds_the_corners():
    coords = square_loop(per_side=4)
    sides = split_loop_sides(coords)
    assert [side[0] for side in sides] == [0, 4, 8, 12], sides
    assert all(len(side) == 5 for side in sides), sides
    # consecutive sides share their corner; loop closes
    assert sides[0][-1] == sides[1][0]
    assert sides[3][-1] == sides[0][0]


def test_split_rejects_tiny_loops():
    try:
        split_loop_sides([Vec(0, 0), Vec(1, 0), Vec(0, 1)])
    except ValueError:
        return
    raise AssertionError("expected ValueError for a 3-vertex boundary")


def test_coons_flat_square_is_bilinear():
    line = lambda a, b, count: [a + (b - a) * (k / count) for k in range(count + 1)]
    bottom = line(Vec(0, 0), Vec(1, 0), 4)
    top = line(Vec(0, 1), Vec(1, 1), 4)
    left = line(Vec(0, 0), Vec(0, 1), 4)
    right = line(Vec(1, 0), Vec(1, 1), 4)
    rows = coons_interior(bottom, right, top, left)
    assert len(rows) == 3 and all(len(r) == 3 for r in rows)
    center = rows[1][1]
    assert abs(center.x - 0.5) < 1e-9 and abs(center.y - 0.5) < 1e-9
    assert abs(rows[0][2].x - 0.75) < 1e-9 and abs(rows[0][2].y - 0.25) < 1e-9


def test_coons_follows_curved_side():
    # Bottom bulges in z; interior near the bottom inherits most of the bulge.
    count = 4
    bottom = [Vec(i / count, 0, 1.0 - abs(i / count - 0.5) * 2) for i in range(count + 1)]
    top = [Vec(i / count, 1, 0) for i in range(count + 1)]
    left = [Vec(0, j / count, 0) for j in range(count + 1)]
    right = [Vec(1, j / count, 0) for j in range(count + 1)]
    rows = coons_interior(bottom, right, top, left)
    near_bottom_z = rows[0][1].z
    near_top_z = rows[2][1].z
    assert near_bottom_z > near_top_z > 0 - 1e-9, (near_bottom_z, near_top_z)
    # rows[0][1] is P(i=2, j=1): above the bulge peak (z=1) at v=1/4
    assert abs(near_bottom_z - 0.75) < 1e-9, near_bottom_z


def test_coons_rejects_mismatched_sides():
    line = lambda a, b, count: [a + (b - a) * (k / count) for k in range(count + 1)]
    try:
        coons_interior(
            line(Vec(0, 0), Vec(1, 0), 4),
            line(Vec(1, 0), Vec(1, 1), 3),
            line(Vec(0, 1), Vec(1, 1), 5),
            line(Vec(0, 0), Vec(0, 1), 3),
        )
    except ValueError:
        return
    raise AssertionError("expected ValueError for mismatched side counts")


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
