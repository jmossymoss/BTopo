"""Minimal stand-ins for mathutils Vectors and bmesh elements.

Just enough surface for the pure-logic modules (feature_graph, strip_fill)
to run outside Blender.
"""

import math


class Vec:
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __sub__(self, other):
        return Vec(self.x - other.x, self.y - other.y, self.z - other.z)

    def __add__(self, other):
        return Vec(self.x + other.x, self.y + other.y, self.z + other.z)

    def __mul__(self, scalar):
        return Vec(self.x * scalar, self.y * scalar, self.z * scalar)

    @property
    def length(self):
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)

    def angle(self, other):
        denom = self.length * other.length
        if denom == 0.0:
            raise ValueError("zero-length vector")
        dot = self.x * other.x + self.y * other.y + self.z * other.z
        return math.acos(max(-1.0, min(1.0, dot / denom)))


class Vert:
    def __init__(self, co, index):
        self.co = co
        self.index = index


class Edge:
    def __init__(self, v1, v2, smooth=False, faces=2, boundary=False):
        self.verts = (v1, v2)
        self.smooth = smooth
        self.link_faces = [object()] * faces
        self.is_boundary = boundary

    def other_vert(self, vert):
        return self.verts[1] if vert is self.verts[0] else self.verts[0]


class BM:
    def __init__(self, edges):
        self.edges = edges


def polyline(points, closed=False):
    verts = [Vert(Vec(*p), i) for i, p in enumerate(points)]
    edges = [Edge(verts[i], verts[i + 1]) for i in range(len(verts) - 1)]
    if closed:
        edges.append(Edge(verts[-1], verts[0]))
    return verts, edges
