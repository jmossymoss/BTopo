"""Rail pairing and quad-strip interpolation for bridge fill.

Pure mesh-topology logic, deliberately free of bpy imports so it can be
unit-tested outside Blender (see tests/test_strip_fill.py). Operates on
bmesh elements, or anything duck-typed like them.
"""

from collections import defaultdict


def order_rails(edges):
    """Group an edge selection into ordered rails.

    Returns a list of (verts, is_cycle) tuples, one per connected run of
    edges, with verts in walk order. Raises ValueError if the selection
    branches (a vertex with more than two selected edges), since a rail
    must be an unambiguous path.
    """
    link = defaultdict(list)
    for edge in edges:
        for vert in edge.verts:
            link[vert].append(edge)
    for vert, vert_edges in link.items():
        if len(vert_edges) > 2:
            raise ValueError(
                "selection branches at a vertex — select two clean edge runs")

    visited = set()
    rails = []

    endpoints = [v for v, vert_edges in link.items() if len(vert_edges) == 1]
    for start in endpoints:
        edge = link[start][0]
        if edge in visited:
            continue
        chain = [start]
        vert = start
        while True:
            visited.add(edge)
            vert = edge.other_vert(vert)
            chain.append(vert)
            next_edges = [e for e in link[vert] if e not in visited]
            if not next_edges:
                break
            edge = next_edges[0]
        rails.append((chain, False))

    # Remaining edges have valence 2 everywhere: closed loops.
    for edge in edges:
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
        rails.append((chain, True))

    return rails


def align_rail(a_coords, b_coords, is_cycle):
    """Index order for rail B that best corresponds vertex-wise to rail A.

    Open rails are tried forward and reversed; cycles are tried at every
    rotation in both directions. Cheapest total pair distance wins.
    """
    n = len(b_coords)
    if is_cycle:
        candidates = []
        for start in range(n):
            candidates.append([(start + k) % n for k in range(n)])
            candidates.append([(start - k) % n for k in range(n)])
    else:
        forward = list(range(n))
        candidates = [forward, forward[::-1]]

    pairs = min(n, len(a_coords))

    def cost(order):
        return sum((b_coords[order[k]] - a_coords[k]).length
                   for k in range(pairs))

    return min(candidates, key=cost)


def adjust_alignment(order, twist=0, flip=False, is_cycle=False):
    """Manual correction on top of align_rail's automatic pairing.

    `flip` reverses rail B's direction (keeping the start vertex on closed
    loops, so it purely mirrors the correspondence); `twist` rotates the
    correspondence around a closed loop by that many vertices.
    """
    if flip:
        if is_cycle:
            order = [order[0]] + order[1:][::-1]
        else:
            order = order[::-1]
    if is_cycle and twist:
        t = twist % len(order)
        order = order[t:] + order[:t]
    return order


def grid_rows(a_coords, b_coords, cuts):
    """Interior rows of coordinates lerped between two equal-length rails.

    Returns `cuts` rows; row r sits at parameter (r+1)/(cuts+1) from rail A
    towards rail B. The caller is expected to re-project the results onto
    the reference surface.
    """
    rows = []
    for r in range(1, cuts + 1):
        t = r / (cuts + 1)
        rows.append([a + (b - a) * t for a, b in zip(a_coords, b_coords)])
    return rows


def auto_cuts(a_coords, b_coords, is_cycle):
    """Cut count that makes the bridged quads roughly square."""
    pairs = min(len(a_coords), len(b_coords))
    if pairs == 0:
        return 0
    span = sum((b_coords[k] - a_coords[k]).length
               for k in range(pairs)) / pairs

    edge_lengths = []
    for coords in (a_coords, b_coords):
        n = len(coords)
        last = n if is_cycle else n - 1
        edge_lengths += [(coords[(i + 1) % n] - coords[i]).length
                         for i in range(last)]
    if not edge_lengths:
        return 0
    average_edge = sum(edge_lengths) / len(edge_lengths)
    if average_edge <= 0.0:
        return 0
    return max(0, round(span / average_edge) - 1)
