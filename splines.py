"""Centripetal Catmull-Rom evaluation for curvature-true rail resampling.

When rail density changes, placing the surviving vertices on chords of the
original polyline slightly flattens curvature. Fitting a spline through the
original vertices first and sampling it at even arc length preserves the
curve's shape; BTopo then projects the result onto the exact source surface,
which spline-only tools cannot do.

Approach informed by the "AV: ResampleMesh" add-on (GPL-3.0-or-later,
copyright 2024 Anton Vashkevich), reimplemented dependency-free.

Pure logic, deliberately free of bpy imports (see tests/test_splines.py).
"""

try:
    from .strip_grid import resample_polyline_count
except ImportError:  # direct import by the test runner outside Blender
    from strip_grid import resample_polyline_count


def catmull_rom_even(points, count, alpha=0.5, oversample=16):
    """`count` + 1 points evenly spaced by arc length along a centripetal
    Catmull-Rom spline through `points`. Endpoints are exact.

    Falls back to chord-based resampling when the input is too short to
    define a spline.
    """
    n = len(points)
    if count < 1:
        raise ValueError("count must be at least 1")
    if n < 3 or count < 2:
        return resample_polyline_count(points, count)

    # Reflect the endpoints so the spline has tangent support there.
    extended = ([points[0] * 2.0 - points[1]]
                + list(points)
                + [points[-1] * 2.0 - points[-2]])
    knots = [0.0]
    for a, b in zip(extended, extended[1:]):
        step = (b - a).length ** alpha
        knots.append(knots[-1] + max(step, 1e-9))

    samples = max(count * oversample, n * 4)
    t_start, t_end = knots[1], knots[-2]
    dense = []
    for k in range(samples + 1):
        t = t_start + (t_end - t_start) * k / samples
        dense.append(_evaluate(extended, knots, t))
    dense[0] = points[0]
    dense[-1] = points[-1]
    return resample_polyline_count(dense, count)


def _evaluate(points, knots, t):
    """Barry-Goldman pyramid for one parameter value."""
    last = len(points) - 3
    i = 1
    while i < last and knots[i + 1] < t:
        i += 1

    p0, p1, p2, p3 = points[i - 1:i + 3]
    t0, t1, t2, t3 = knots[i - 1:i + 3]

    def lerp(a, b, ta, tb):
        if tb == ta:
            return a
        w = (t - ta) / (tb - ta)
        return a * (1.0 - w) + b * w

    a1 = lerp(p0, p1, t0, t1)
    a2 = lerp(p1, p2, t1, t2)
    a3 = lerp(p2, p3, t2, t3)
    b1 = lerp(a1, a2, t0, t2)
    b2 = lerp(a2, a3, t1, t3)
    return lerp(b1, b2, t1, t2)
