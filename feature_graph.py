"""Feature-edge graph extraction and resampling.

Pure mesh-topology logic, deliberately free of bpy imports so it can be
unit-tested outside Blender (see tests/test_feature_graph.py). Operates on
bmesh elements, or anything duck-typed like them.
"""

import math
from collections import defaultdict

# Tolerance for threshold comparisons: accumulated turns routinely land
# exactly on the threshold (uniform CAD tessellation), and float rounding
# must not flip those placements.
_EPS = 1e-6


def is_feature_edge(edge):
    """Sharp-marked, boundary, and non-manifold edges are features.

    Wire edges (no faces) are ignored — CAD imports often carry stray
    construction wires that would pollute the graph.
    """
    if not edge.link_faces:
        return False
    return not edge.smooth or edge.is_boundary or len(edge.link_faces) > 2


def _turn_angle(prev_dir, next_dir):
    try:
        return prev_dir.angle(next_dir)
    except ValueError:  # zero-length direction at a degenerate vertex
        return 0.0


def build_feature_curves(bm, corner_angle):
    """Chain the feature edges of `bm` into polylines split at corners.

    A corner is a vertex where the feature graph has valence other than 2
    (curve endpoints and junctions), or a valence-2 vertex where the curve
    turns more sharply than `corner_angle` — a hard corner of the design
    that must survive resampling as a vertex.

    Returns a list of (verts, is_cycle) tuples. `verts` is an ordered list
    of BMVerts. Chains run corner-to-corner (a closed curve through a
    single corner repeats that corner at both ends); cycles are closed
    loops containing no corner at all, with verts[-1] connecting back to
    verts[0].
    """
    feature_edges = [e for e in bm.edges if is_feature_edge(e)]
    link = defaultdict(list)
    for edge in feature_edges:
        for vert in edge.verts:
            link[vert].append(edge)

    def is_corner(vert):
        edges = link[vert]
        if len(edges) != 2:
            return True
        a, b = (e.other_vert(vert) for e in edges)
        # The two outgoing directions are pi apart when the curve runs
        # straight through; the deficit from pi is how hard it turns.
        turn = math.pi - _turn_angle(a.co - vert.co, b.co - vert.co)
        return turn > corner_angle

    corners = {v for v in link if is_corner(v)}
    visited = set()
    curves = []

    for corner in corners:
        for start_edge in link[corner]:
            if start_edge in visited:
                continue
            chain = [corner]
            edge, vert = start_edge, corner
            while True:
                visited.add(edge)
                vert = edge.other_vert(vert)
                chain.append(vert)
                if vert in corners:
                    break
                next_edges = [e for e in link[vert] if e not in visited]
                if not next_edges:
                    break
                edge = next_edges[0]
            curves.append((chain, False))

    # Whatever remains has no corner anywhere: pure closed loops
    # (circles and the like).
    for edge in feature_edges:
        if edge in visited:
            continue
        start = edge.verts[0]
        chain = [start]
        current, vert = edge, start
        while True:
            visited.add(current)
            vert = current.other_vert(vert)
            if vert is start:
                break
            chain.append(vert)
            next_edges = [e for e in link[vert] if e is not current]
            if not next_edges:
                break
            current = next_edges[0]
        curves.append((chain, True))

    return curves


def resample_curve(coords, is_cycle, segment_angle, max_edge_length=0.0):
    """Pick the subset of a polyline's vertices to keep at game resolution.

    Walks the curve accumulating turning angle and arc length, placing a
    vertex whenever the accumulated turn reaches `segment_angle` (so a full
    circle gets 2*pi / segment_angle segments and straight runs collapse to
    a single edge) or, if `max_edge_length` > 0, whenever the next step
    would exceed it. Endpoints are always kept. Returns indices into
    `coords`, in walk order.
    """
    n = len(coords)
    if n <= 2:
        return list(range(n))
    if is_cycle:
        return _resample_cycle(coords, segment_angle, max_edge_length)

    keep = [0]
    acc_angle = 0.0
    acc_length = 0.0
    for i in range(1, n - 1):
        prev_dir = coords[i] - coords[i - 1]
        next_dir = coords[i + 1] - coords[i]
        acc_angle += _turn_angle(prev_dir, next_dir)
        acc_length += prev_dir.length
        too_long = (max_edge_length > 0.0
                    and acc_length + next_dir.length > max_edge_length + _EPS)
        if acc_angle >= segment_angle - _EPS or too_long:
            keep.append(i)
            acc_angle = 0.0
            acc_length = 0.0
    keep.append(n - 1)
    return keep


def _resample_cycle(coords, segment_angle, max_edge_length):
    n = len(coords)
    seg_lengths = [(coords[(i + 1) % n] - coords[i]).length for i in range(n)]
    turns = []
    for i in range(n):
        prev_dir = coords[i] - coords[i - 1]
        next_dir = coords[(i + 1) % n] - coords[i]
        turns.append(_turn_angle(prev_dir, next_dir))

    # Anchor the walk at the sharpest turn so resampling starts from the
    # loop's most feature-like point rather than an arbitrary vertex.
    start = max(range(n), key=turns.__getitem__)
    order = [(start + k) % n for k in range(n)]

    keep = [start]
    acc_angle = 0.0
    acc_length = 0.0
    for k in range(1, n):
        i = order[k]
        acc_angle += turns[i]
        acc_length += seg_lengths[(i - 1) % n]
        too_long = (max_edge_length > 0.0
                    and acc_length + seg_lengths[i] > max_edge_length + _EPS)
        if acc_angle >= segment_angle - _EPS or too_long:
            keep.append(i)
            acc_angle = 0.0
            acc_length = 0.0

    # A closed loop degenerates below 4 vertices; fall back to a roughly
    # uniform arc-length distribution.
    min_verts = min(4, n)
    if len(keep) < min_verts:
        total = sum(seg_lengths)
        step = total / min_verts
        keep = []
        walked = 0.0
        target = 0.0
        for k in range(n):
            i = order[k]
            if walked >= target - 1e-9:
                keep.append(i)
                target += step
            walked += seg_lengths[i]
    return keep
