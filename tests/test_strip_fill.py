"""Tests for strip_fill.py, runnable without Blender: python3 tests/test_strip_fill.py"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strip_fill import align_rail, auto_cuts, grid_rows, order_rails
from mocks import Vec, polyline


def circle(count, radius=1.0, z=0.0, phase=0.0):
    return [(radius * math.cos(2 * math.pi * i / count + phase),
             radius * math.sin(2 * math.pi * i / count + phase), z)
            for i in range(count)]


def test_two_parallel_lines_make_two_open_rails():
    _, edges_a = polyline([(i, 0) for i in range(5)])
    _, edges_b = polyline([(i, 1) for i in range(5)])
    rails = order_rails(edges_a + edges_b)
    assert len(rails) == 2
    assert all(not is_cycle and len(verts) == 5 for verts, is_cycle in rails)


def test_branching_selection_raises():
    verts, edges = polyline([(0, 0), (1, 0), (2, 0)])
    from mocks import Edge, Vert
    branch = Edge(verts[1], Vert(Vec(1, 1), 99))
    try:
        order_rails(edges + [branch])
    except ValueError:
        return
    raise AssertionError("expected ValueError for branching selection")


def test_two_circles_make_two_cycles():
    _, edges_a = polyline(circle(8, z=0.0), closed=True)
    _, edges_b = polyline(circle(8, z=1.0), closed=True)
    rails = order_rails(edges_a + edges_b)
    assert len(rails) == 2
    assert all(is_cycle and len(verts) == 8 for verts, is_cycle in rails)


def test_align_reverses_open_rail():
    a = [Vec(i, 0) for i in range(5)]
    b = [Vec(i, 1) for i in reversed(range(5))]
    order = align_rail(a, b, is_cycle=False)
    assert order == [4, 3, 2, 1, 0], order


def test_align_rotates_cycle():
    a = [Vec(*p) for p in circle(8)]
    b = [Vec(*p) for p in circle(8, z=1.0, phase=2 * math.pi * 3 / 8)]
    order = align_rail(a, b, is_cycle=True)
    for k in range(8):
        assert (b[order[k]] - a[k]).length < 1.01, (k, order)


def test_grid_rows_lerp():
    a = [Vec(0, 0), Vec(1, 0)]
    b = [Vec(0, 3), Vec(1, 3)]
    rows = grid_rows(a, b, cuts=2)
    assert len(rows) == 2 and all(len(r) == 2 for r in rows)
    assert abs(rows[0][0].y - 1.0) < 1e-9
    assert abs(rows[1][1].y - 2.0) < 1e-9
    assert abs(rows[1][1].x - 1.0) < 1e-9


def test_auto_cuts_makes_square_quads():
    a = [Vec(i * 0.25, 0) for i in range(5)]
    b = [Vec(i * 0.25, 1) for i in range(5)]
    assert auto_cuts(a, b, is_cycle=False) == 3


def test_auto_cuts_zero_for_close_rails():
    a = [Vec(i, 0) for i in range(5)]
    b = [Vec(i, 1) for i in range(5)]
    assert auto_cuts(a, b, is_cycle=False) == 0


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
