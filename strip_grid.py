"""Quad-strip grid recovery and on-surface resampling for in-place editing.

The in-place philosophy: vertices of a CAD tessellation lie exactly on the
true surface, so tools keep or remove existing vertices wherever possible;
when a new vertex is unavoidable it is placed by arc length on an existing
polyline, which is on-surface by construction. Nothing ever slides.

Pure mesh-topology logic, deliberately free of bpy imports so it can be
unit-tested outside Blender (see tests/test_strip_grid.py).
"""

from collections import defaultdict


def region_boundary_cycles(faces_verts):
    """Ordered boundary loop(s) of a face region, any polygon sizes.

    Returns a list of vertex lists, one per boundary loop, each in walk
    order with implied closure. Raises ValueError if the region has no
    boundary (it covers a closed surface) or the boundary is irregular.
    """
    edge_faces = defaultdict(list)
    for face in faces_verts:
        n = len(face)
        for k in range(n):
            edge_faces[frozenset((face[k], face[(k + 1) % n]))].append(face)
    boundary = [e for e, fs in edge_faces.items() if len(fs) == 1]
    if not boundary:
        raise ValueError("region has no boundary — it covers a closed surface")
    return _boundary_cycles(boundary)


def recover_grid(faces_verts, flip=False):
    """Recover the vert grid of a regular quad strip region.

    `faces_verts` is an iterable of ordered vertex tuples (one per quad).
    Returns (rows, closed_along): rows[i][j] is the vertex at station i
    along the strip, position j across it; `closed_along` is True for
    strips that wrap (a fillet ring), in which case the stations cycle.

    For open strips the longer boundary pair is taken as the rails
    (stations run along them); `flip` chooses the other pair instead.
    Raises ValueError with an actionable message for non-quad or irregular
    regions.
    """
    faces = [tuple(face) for face in faces_verts]
    if not faces:
        raise ValueError("no faces in the strip region")
    for face in faces:
        if len(face) != 4:
            raise ValueError(
                "strip region must be all quads — run CAD Cleanup first")

    edge_faces = defaultdict(list)
    vert_edges = defaultdict(set)
    vert_face_count = defaultdict(int)
    for face in faces:
        for k in range(4):
            edge = frozenset((face[k], face[(k + 1) % 4]))
            edge_faces[edge].append(face)
            for vert in edge:
                vert_edges[vert].add(edge)
        for vert in face:
            vert_face_count[vert] += 1

    boundary_edges = [e for e, fs in edge_faces.items() if len(fs) == 1]
    cycles = _boundary_cycles(boundary_edges)

    if len(cycles) == 1:
        rail_a, rail_b = _open_rails(cycles[0], vert_face_count, flip)
        closed_along = False
    elif len(cycles) == 2:
        if any(vert_face_count[v] == 1 for v in vert_face_count):
            raise ValueError("strip region is not a regular quad grid")
        rail_a, rail_b = cycles
        closed_along = True
    else:
        raise ValueError(
            "strip region must have one boundary loop (open strip) or two "
            "(closed ring)")

    rail_edge_set = set()
    for rail in (rail_a, rail_b):
        last = len(rail) if closed_along else len(rail) - 1
        for k in range(last):
            rail_edge_set.add(frozenset((rail[k], rail[(k + 1) % len(rail)])))

    rail_b_set = set(rail_b)
    rows = []
    for vert in rail_a:
        starts = [e for e in vert_edges[vert] if e not in rail_edge_set]
        if len(starts) != 1:
            raise ValueError("strip region is not a regular quad grid")
        column = [vert]
        edge, current = starts[0], vert
        for _ in range(len(vert_edges)):
            (current,) = set(edge) - {current}
            column.append(current)
            if current in rail_b_set:
                break
            edge = _continuation(current, edge, vert_edges, edge_faces)
        else:
            raise ValueError("strip region is not a regular quad grid")
        rows.append(column)

    if len({len(column) for column in rows}) != 1:
        raise ValueError(
            "strip columns have uneven vertex counts — the region is not a "
            "regular grid")
    return rows, closed_along


def _continuation(vert, edge_in, vert_edges, edge_faces):
    """The edge continuing straight through `vert`: shares no face with
    the incoming edge. Unique at every interior vertex of a quad grid."""
    faces_in = set(map(id, edge_faces[edge_in]))
    candidates = [
        e for e in vert_edges[vert]
        if e != edge_in and not faces_in & set(map(id, edge_faces[e]))
    ]
    if len(candidates) != 1:
        raise ValueError("strip region is not a regular quad grid")
    return candidates[0]


def _boundary_cycles(boundary_edges):
    link = defaultdict(list)
    for edge in boundary_edges:
        for vert in edge:
            link[vert].append(edge)
    for vert, edges in link.items():
        if len(edges) != 2:
            raise ValueError("strip region boundary is irregular")

    cycles = []
    visited = set()
    for start_edge in boundary_edges:
        if start_edge in visited:
            continue
        vert = next(iter(start_edge))
        cycle = [vert]
        edge = start_edge
        while True:
            visited.add(edge)
            (vert,) = set(edge) - {vert}
            if vert is cycle[0]:
                break
            cycle.append(vert)
            (edge,) = (e for e in link[vert] if e is not edge)
        cycles.append(cycle)
    return cycles


def _open_rails(cycle, vert_face_count, flip):
    """Split an open strip's boundary cycle into rails at its 4 corners."""
    corner_positions = [k for k, v in enumerate(cycle)
                        if vert_face_count[v] == 1]
    if len(corner_positions) != 4:
        raise ValueError(
            "strip region must be four-cornered (found "
            f"{len(corner_positions)} corners) — select a single rectangular "
            "strip")

    n = len(cycle)
    sides = []
    for k in range(4):
        start = corner_positions[k]
        end = corner_positions[(k + 1) % 4]
        side = [cycle[start]]
        i = start
        while i != end:
            i = (i + 1) % n
            side.append(cycle[i])
        sides.append(side)

    long_pair = (len(sides[0]) >= len(sides[1]))
    pick_first = long_pair != flip  # flip swaps which pair acts as rails
    return (sides[0], sides[2]) if pick_first else (sides[1], sides[3])


def resample_polyline_count(coords, count):
    """Resample a polyline to `count` segments by arc length.

    Endpoints are preserved exactly; intermediate points lie on the
    original polyline (on-surface up to the source tessellation, which is
    the ground truth). Returns count + 1 coordinates.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    segments = [(coords[k + 1] - coords[k]).length
                for k in range(len(coords) - 1)]
    total = sum(segments)
    if total <= 0.0:
        return [coords[0]] * count + [coords[-1]]

    result = [coords[0]]
    k = 0
    walked = 0.0
    for s in range(1, count):
        target = total * s / count
        while k < len(segments) and walked + segments[k] < target:
            walked += segments[k]
            k += 1
        if k >= len(segments):
            result.append(coords[-1])
            continue
        t = (target - walked) / segments[k] if segments[k] > 0.0 else 0.0
        result.append(coords[k] + (coords[k + 1] - coords[k]) * t)
    result.append(coords[-1])
    return result
