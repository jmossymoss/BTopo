"""Four-sided patch filling: boundary splitting and Coons interpolation.

Pure mesh-topology logic, deliberately free of bpy imports so it can be
unit-tested outside Blender (see tests/test_patch_fill.py).
"""


def _turn(prev_dir, next_dir):
    try:
        return prev_dir.angle(next_dir)
    except ValueError:  # zero-length direction at a degenerate vertex
        return 0.0


def split_loop_sides(coords):
    """Split a closed boundary loop into four sides at its sharpest corners.

    `coords` is the loop in walk order (closure implied). Returns four lists
    of indices into `coords`, in walk order; consecutive sides share their
    corner index and the last side closes back to the first corner.
    """
    n = len(coords)
    if n < 4:
        raise ValueError("patch boundary needs at least 4 vertices")

    turns = []
    for i in range(n):
        prev_dir = coords[i] - coords[i - 1]
        next_dir = coords[(i + 1) % n] - coords[i]
        turns.append(_turn(prev_dir, next_dir))

    corners = sorted(sorted(range(n), key=turns.__getitem__, reverse=True)[:4])

    sides = []
    for k in range(4):
        start, end = corners[k], corners[(k + 1) % 4]
        side = [start]
        i = start
        while i != end:
            i = (i + 1) % n
            side.append(i)
        sides.append(side)
    return sides


def coons_interior(bottom, right, top, left):
    """Interior points of a Coons patch bounded by four coordinate rows.

    Boundary convention (u along bottom/top, v along left/right):
    bottom[i] = P(i, 0), top[i] = P(i, n), left[j] = P(0, j),
    right[j] = P(m, j) — i.e. bottom and top run in the same direction, as
    do left and right, and the four rows share their corner coordinates.

    Uses the bilinearly blended Coons formula with uniform index
    parameterization (fine for rails resampled to roughly even spacing).
    Returns rows[j-1][i-1] = P(i, j) for the interior 0 < i < m, 0 < j < n.
    """
    m = len(bottom) - 1
    n = len(left) - 1
    if len(top) != m + 1 or len(right) != n + 1:
        raise ValueError("opposite sides must have matching vertex counts")

    p00, pm0 = bottom[0], bottom[m]
    p0n, pmn = top[0], top[m]

    rows = []
    for j in range(1, n):
        v = j / n
        row = []
        for i in range(1, m):
            u = i / m
            point = (bottom[i] * (1 - v) + top[i] * v
                     + left[j] * (1 - u) + right[j] * u
                     - (p00 * ((1 - u) * (1 - v)) + pm0 * (u * (1 - v))
                        + p0n * ((1 - u) * v) + pmn * (u * v)))
            row.append(point)
        rows.append(row)
    return rows
