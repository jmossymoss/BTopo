"""Tests for feature_graph.py, runnable without Blender: python3 tests/test_feature_graph.py

Uses minimal mocks duck-typed like bmesh elements and mathutils Vectors.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_graph import build_feature_curves, resample_curve
from mocks import BM, Edge, Vec, Vert, polyline

CORNER = math.radians(45)


def test_square_splits_into_four_chains():
    _, edges = polyline([(0, 0), (1, 0), (1, 1), (0, 1)], closed=True)
    curves = build_feature_curves(BM(edges), CORNER)
    assert len(curves) == 4, curves
    assert all(not is_cycle and len(verts) == 2 for verts, is_cycle in curves)


def test_circle_is_one_cycle_resampled_to_twelve():
    points = [(math.cos(2 * math.pi * i / 24), math.sin(2 * math.pi * i / 24))
              for i in range(24)]
    _, edges = polyline(points, closed=True)
    curves = build_feature_curves(BM(edges), CORNER)
    assert len(curves) == 1
    verts, is_cycle = curves[0]
    assert is_cycle and len(verts) == 24
    keep = resample_curve([v.co for v in verts], True, math.radians(30))
    assert len(keep) == 12, keep


def test_straight_run_collapses_to_endpoints():
    points = [(i, 0) for i in range(11)]
    _, edges = polyline(points)
    curves = build_feature_curves(BM(edges), CORNER)
    assert len(curves) == 1
    verts, is_cycle = curves[0]
    assert not is_cycle and len(verts) == 11
    keep = resample_curve([v.co for v in verts], False, math.radians(30))
    assert keep == [0, 10], keep


def test_max_edge_length_subdivides_straight_run():
    points = [(i, 0) for i in range(11)]
    _, edges = polyline(points)
    (verts, _), = build_feature_curves(BM(edges), CORNER)
    keep = resample_curve([v.co for v in verts], False, math.radians(30),
                          max_edge_length=3.0)
    assert keep == [0, 3, 6, 9, 10], keep


def test_hard_kink_splits_chain():
    points = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2)]
    _, edges = polyline(points)
    curves = build_feature_curves(BM(edges), CORNER)
    assert len(curves) == 2, curves
    assert sorted(len(verts) for verts, _ in curves) == [3, 3]


def test_t_junction_yields_three_chains():
    center = Vert(Vec(0, 0), 0)
    arms = []
    index = 1
    for direction in ((1, 0), (-1, 0), (0, 1)):
        mid = Vert(Vec(direction[0], direction[1]), index)
        tip = Vert(Vec(direction[0] * 2, direction[1] * 2), index + 1)
        arms += [Edge(center, mid), Edge(mid, tip)]
        index += 2
    curves = build_feature_curves(BM(arms), CORNER)
    assert len(curves) == 3, curves
    assert all(not is_cycle and len(verts) == 3 for verts, is_cycle in curves)


def test_cycle_keeps_minimum_four_vertices():
    points = [(math.cos(2 * math.pi * i / 6), math.sin(2 * math.pi * i / 6))
              for i in range(6)]
    _, edges = polyline(points, closed=True)
    (verts, is_cycle), = build_feature_curves(BM(edges), math.radians(80))
    assert is_cycle
    keep = resample_curve([v.co for v in verts], True, math.radians(170))
    assert len(keep) == 4, keep


def test_smooth_interior_edges_are_not_features():
    verts, edges = polyline([(0, 0), (1, 0), (2, 0)])
    for edge in edges:
        edge.smooth = True
    assert build_feature_curves(BM(edges), CORNER) == []


def test_closed_curve_through_single_corner_repeats_it():
    # Teardrop: a loop whose only corner is the sharp point at the origin.
    points = [(0, 0)] + [(1 + math.cos(a), math.sin(a))
                         for a in (2 * math.pi * i / 12 for i in range(5, 20))]
    _, edges = polyline(points, closed=True)
    curves = build_feature_curves(BM(edges), CORNER)
    chains = [c for c in curves if not c[1]]
    assert chains, curves
    for verts, _ in chains:
        assert verts[0] is verts[-1] or len(verts) >= 2


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
