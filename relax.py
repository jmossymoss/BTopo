"""Projected grid relaxation: even quad distribution along curvature.

After a patch interior is synthesised (Coons + projection), spacing can
bunch where the surface curves strongly. Relaxing each interior vertex
toward its four-neighbour average and re-projecting onto the reference
surface every step converges to an even distribution that follows the
curvature — the role spline fitting plays in tools without a reference
surface. Boundary vertices never move.

Pure logic, deliberately free of bpy imports (see tests/test_relax.py).
"""


def relax_grid(grid, wrap, iterations, strength=0.5, project=None):
    """Relax the interior of a coordinate grid, boundary fixed.

    `grid` is rows[i][j]; j = 0 and j = last are always fixed (rails).
    With `wrap` the i direction is cyclic (ring patches) and every station
    relaxes; otherwise i = 0 and i = last are fixed too. `project` is an
    optional callback snapping a coordinate back onto the surface.
    Returns a new grid; the input is not modified.
    """
    rows = len(grid)
    cols = len(grid[0])
    current = [list(row) for row in grid]

    i_range = range(rows) if wrap else range(1, rows - 1)
    for _ in range(iterations):
        result = [list(row) for row in current]
        for i in i_range:
            i_prev = (i - 1) % rows
            i_next = (i + 1) % rows
            for j in range(1, cols - 1):
                average = (current[i_prev][j] + current[i_next][j]
                           + current[i][j - 1] + current[i][j + 1]) * 0.25
                point = current[i][j] + (average - current[i][j]) * strength
                if project is not None:
                    point = project(point)
                result[i][j] = point
        current = result
    return current
