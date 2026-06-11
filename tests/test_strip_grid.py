"""Tests for strip_grid.py, runnable without Blender: python3 tests/test_strip_grid.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strip_grid import recover_grid, region_boundary_cycles, resample_polyline_count
from mocks import Vec, Vert


def make_grid(along, across, closed=False):
    """Vert grid `along` x `across`; faces wrap in the along direction if closed."""
    verts = [[Vert(Vec(i, j), i * across + j) for j in range(across)]
             for i in range(along)]
    faces = []
    stations = along if closed else along - 1
    for i in range(stations):
        i2 = (i + 1) % along
        for j in range(across - 1):
            faces.append((verts[i][j], verts[i2][j],
                          verts[i2][j + 1], verts[i][j + 1]))
    return verts, faces


def test_open_strip_recovers_along_the_long_direction():
    verts, faces = make_grid(5, 3)
    rows, closed = recover_grid(faces)
    assert not closed
    assert len(rows) == 5 and all(len(c) == 3 for c in rows), rows
    stations = {tuple(v.index for v in c) for c in rows}
    expected = {tuple(verts[i][j].index for j in range(3)) for i in range(5)}
    flipped = {t[::-1] for t in expected}
    assert stations <= expected | flipped, stations


def test_flip_swaps_grid_direction():
    _, faces = make_grid(5, 3)
    rows, _ = recover_grid(faces, flip=True)
    assert len(rows) == 3 and all(len(c) == 5 for c in rows)


def test_closed_ring_recovers_with_wrap():
    _, faces = make_grid(6, 3, closed=True)
    rows, closed = recover_grid(faces)
    assert closed
    assert len(rows) == 6 and all(len(c) == 3 for c in rows)


def test_single_quad_is_a_one_by_one_grid():
    _, faces = make_grid(2, 2)
    rows, closed = recover_grid(faces)
    assert not closed
    assert len(rows) == 2 and all(len(c) == 2 for c in rows)


def test_rejects_triangles():
    a, b, c = (Vert(Vec(0, 0), 0), Vert(Vec(1, 0), 1), Vert(Vec(0, 1), 2))
    try:
        recover_grid([(a, b, c)])
    except ValueError as exc:
        assert "quad" in str(exc).lower()
        return
    raise AssertionError("expected ValueError for triangles")


def test_rejects_l_shaped_region():
    verts, faces = make_grid(3, 3)
    # remove one corner quad to make an L: 6 corners, not 4
    faces = [f for f in faces if verts[1][1] not in f or verts[2][2] not in f]
    try:
        recover_grid(faces)
    except ValueError:
        return
    raise AssertionError("expected ValueError for an L-shaped region")


def test_boundary_cycles_open_region():
    _, faces = make_grid(3, 3)
    cycles = region_boundary_cycles(faces)
    assert len(cycles) == 1 and len(cycles[0]) == 8, cycles


def test_boundary_cycles_ring_region():
    _, faces = make_grid(6, 2, closed=True)
    cycles = region_boundary_cycles(faces)
    assert len(cycles) == 2
    assert sorted(len(c) for c in cycles) == [6, 6]


def test_boundary_cycles_mixed_polygons():
    # an ngon next to a quad still yields one clean boundary loop
    a = [Vert(Vec(x, 0), x) for x in range(3)]
    b = [Vert(Vec(x, 1), 10 + x) for x in range(3)]
    faces = [(a[0], a[1], b[1], b[0]),
             (a[1], a[2], b[2], b[1], )]
    cycles = region_boundary_cycles(faces)
    assert len(cycles) == 1 and len(cycles[0]) == 6, cycles


def test_resample_polyline_by_arc_length():
    coords = [Vec(x, 0) for x in range(5)]
    out = resample_polyline_count(coords, 2)
    assert len(out) == 3
    assert abs(out[1].x - 2.0) < 1e-9 and out[0].x == 0.0 and out[2].x == 4.0


def test_resample_handles_uneven_input_spacing():
    coords = [Vec(0, 0), Vec(3, 0), Vec(4, 0)]
    out = resample_polyline_count(coords, 4)
    assert [round(p.x, 6) for p in out] == [0.0, 1.0, 2.0, 3.0, 4.0], out


def test_resample_endpoints_exact():
    coords = [Vec(0, 0, 1), Vec(1, 2, 3), Vec(4, 4, 4)]
    out = resample_polyline_count(coords, 3)
    assert out[0] is coords[0] and out[-1] is coords[-1]


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
