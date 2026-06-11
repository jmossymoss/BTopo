"""Tests for relax.py, runnable without Blender: python3 tests/test_relax.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relax import relax_grid
from mocks import Vec


def test_relax_evens_out_bunched_interior():
    # 3x3 grid with the centre vertex bunched toward a corner
    grid = [[Vec(i, j) for j in range(3)] for i in range(3)]
    grid[1][1] = Vec(0.2, 0.2)
    out = relax_grid(grid, wrap=False, iterations=50)
    assert abs(out[1][1].x - 1.0) < 1e-3 and abs(out[1][1].y - 1.0) < 1e-3


def test_relax_keeps_boundary_fixed():
    grid = [[Vec(i, j) for j in range(3)] for i in range(4)]
    grid[1][1] = Vec(0.3, 0.4)
    out = relax_grid(grid, wrap=False, iterations=10)
    for i in (0, 3):
        for j in range(3):
            assert out[i][j] is grid[i][j]
    for i in range(4):
        for j in (0, 2):
            assert out[i][j] is grid[i][j]


def test_relax_projects_every_step():
    def to_unit_x(co):
        return Vec(1.0, co.y, co.z)

    grid = [[Vec(0, i, j) for j in range(3)] for i in range(3)]
    out = relax_grid(grid, wrap=False, iterations=3, project=to_unit_x)
    assert out[1][1].x == 1.0
    assert out[0][0].x == 0.0  # boundary untouched, never projected


def test_relax_wrap_moves_all_stations():
    grid = [[Vec(i, j) for j in range(3)] for i in range(4)]
    grid[0][1] = Vec(0.5, 0.2)
    out = relax_grid(grid, wrap=True, iterations=5)
    assert out[0][1] is not grid[0][1]
    for i in range(4):
        assert out[i][0] is grid[i][0] and out[i][2] is grid[i][2]


def test_zero_iterations_is_identity():
    grid = [[Vec(i, j) for j in range(2)] for i in range(2)]
    out = relax_grid(grid, wrap=False, iterations=0)
    assert all(out[i][j] is grid[i][j] for i in range(2) for j in range(2))


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
