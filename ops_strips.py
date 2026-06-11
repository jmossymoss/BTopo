"""In-place editing: rail resampling, patch rebuilding, strip tools.

These operate directly on the CAD tessellation, under the invariant that
no vertex ever slides: tools keep or remove original (exactly-on-surface)
vertices; unavoidable new vertices are placed on existing on-surface
polylines or projected onto the original surface snapshot.

The general path for real CAD tessellations (ladders, T-junctions,
triangles, ngons) is Simplify Rails → Rebuild Patch: rails are resampled
by dissolving, then patch interiors are discarded wholesale and
resynthesised between the rails with transfinite (Coons) interpolation
projected back onto the original surface. The quad-grid strip tools
remain as fast paths for already-regular strips.
"""

import math

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator
from mathutils.bvhtree import BVHTree

from .feature_graph import build_feature_curves, resample_curve
from .patch_fill import _turn, coons_interior, split_loop_sides
from .relax import relax_grid
from .splines import catmull_rom_even
from .strip_fill import align_rail, auto_cuts, grid_rows
from .strip_grid import (recover_grid, region_boundary_cycles,
                         resample_polyline_count)

PATCH_LAYER = "btopo_patch"


def _expand_to_patches(bm, faces):
    """Grow a face selection to whole CAD patches via the baked group layer."""
    layer = bm.faces.layers.int.get(PATCH_LAYER)
    if layer is None:
        return faces
    wanted = {f[layer] for f in faces} - {-1}
    if not wanted:
        return faces
    return [f for f in bm.faces if f[layer] in wanted]


def _strip_region(bm, use_patch):
    region = [f for f in bm.faces if f.select]
    if region and use_patch:
        region = _expand_to_patches(bm, region)
    return region


def _edge_between(a, b):
    return next((e for e in a.link_edges if e.other_vert(a) is b), None)


def _loop_turns(loop):
    """Turn angle at each vertex of a closed vert loop, keyed by vertex."""
    n = len(loop)
    turns = {}
    for i, vert in enumerate(loop):
        prev_dir = vert.co - loop[i - 1].co
        next_dir = loop[(i + 1) % n].co - vert.co
        turns[vert] = _turn(prev_dir, next_dir)
    return turns


class BTOPO_OT_simplify_rails(Operator):
    """Resample the feature rails in place by dissolving vertices.

    Walks the mesh's feature curves (sharp edges) and keeps a
    curvature-driven subset of their vertices — straight rails collapse to
    single edges, curved rails keep one vertex per segment angle, corners
    and junctions always survive. Removal is by dissolve, so any topology
    hanging off a removed vertex (ladder rungs, fans, T-junctions) merges
    into the adjacent faces and the mesh stays watertight. Kept vertices
    are untouched originals.
    """

    bl_idname = "btopo.simplify_rails"
    bl_label = "Simplify Rails"
    bl_description = (
        "Dissolve feature-rail vertices down to a curvature-driven subset; "
        "rails define the spans every other tool builds between"
    )
    bl_options = {'REGISTER', 'UNDO'}

    segment_angle: FloatProperty(
        name="Segment Angle",
        description="Curvature per kept vertex along a rail",
        subtype='ANGLE',
        default=math.radians(30.0),
        min=math.radians(1.0),
        max=math.radians(120.0),
    )

    corner_angle: FloatProperty(
        name="Corner Angle",
        description="Turns sharper than this always keep their vertex",
        subtype='ANGLE',
        default=math.radians(45.0),
        min=math.radians(5.0),
        max=math.pi,
    )

    max_edge: FloatProperty(
        name="Max Edge Length",
        description="Keep vertices so no rail edge exceeds this (0 = unlimited)",
        subtype='DISTANCE',
        default=0.0,
        min=0.0,
    )

    only_selected: BoolProperty(
        name="Only Selected",
        description="Limit to rails touching the current selection",
        default=True,
    )

    spacing: EnumProperty(
        name="Spacing",
        description="Where the surviving rail vertices end up",
        items=(
            ('KEEP', "Keep Originals",
             "Only remove vertices; survivors stay exactly where the CAD "
             "tessellation put them"),
            ('EVEN', "Even (Spline)",
             "Redistribute survivors at even arc length along a spline "
             "fitted through the original rail, re-projected onto the "
             "surface — vertices slide along the surface, never off it"),
        ),
        default='KEEP',
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'

    def invoke(self, context, event):
        settings = context.scene.btopo
        self.segment_angle = settings.trace_segment_angle
        self.corner_angle = settings.trace_corner_angle
        self.max_edge = settings.trace_max_edge
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        curves = build_feature_curves(bm, self.corner_angle)
        if self.only_selected:
            if not any(v.select for v in bm.verts):
                self.report({'ERROR'},
                            "Select part of the rails to simplify, or "
                            "disable Only Selected")
                return {'CANCELLED'}
            curves = [c for c in curves if any(v.select for v in c[0])]
        if not curves:
            self.report({'ERROR'},
                        "No feature rails found — run Detect Features first")
            return {'CANCELLED'}

        victims = {}
        curve_data = []
        for verts, is_cycle in curves:
            coords = [v.co.copy() for v in verts]
            keep = resample_curve(coords, is_cycle,
                                  self.segment_angle, self.max_edge)
            curve_data.append((verts, coords, keep, is_cycle))
            keep_set = set(keep)
            for i, vert in enumerate(verts):
                if i not in keep_set:
                    victims[vert] = None
        victims = list(victims)
        if not victims and self.spacing == 'KEEP':
            self.report({'INFO'}, "Nothing to simplify at this density")
            return {'FINISHED'}

        # Snapshot the surface before any edits so redistributed vertices
        # can be glued back onto the exact original geometry.
        bvh = BVHTree.FromBMesh(bm) if self.spacing == 'EVEN' else None

        # First merge away whatever hangs off the dropped vertices (rungs,
        # fans): smooth interior edges only, so rails are never crossed.
        hanging = {e for v in victims for e in v.link_edges
                   if e.smooth and not e.is_boundary}
        if hanging:
            bmesh.ops.dissolve_edges(bm, edges=list(hanging), use_verts=False)

        # Dropped vertices are now plain 2-valence points on their rail and
        # dissolve into the rail edge. Anything still higher-valence is
        # structural (non-manifold junctions) and is left alone.
        removable = [v for v in victims
                     if v.is_valid and len(v.link_edges) == 2]
        if removable:
            bmesh.ops.dissolve_verts(bm, verts=removable)

        if bvh is not None:
            self._redistribute(curve_data, bvh)

        bmesh.update_edit_mesh(obj.data)
        skipped = len(victims) - len(removable)
        message = f"Removed {len(removable)} rail vertices"
        if skipped:
            message += f" ({skipped} structural vertices kept)"
        if bvh is not None:
            message += ", even spacing"
        self.report({'INFO'}, message)
        return {'FINISHED'}

    def _redistribute(self, curve_data, bvh):
        """Slide surviving rail vertices to even arc length, on-surface.

        Targets come from a centripetal Catmull-Rom through the *original*
        rail (so curvature is honoured, not chord-cut) and are re-projected
        onto the surface snapshot. Corners, junctions and cycle anchors
        never move.
        """
        for verts, coords, keep, is_cycle in curve_data:
            kept = [verts[i] for i in keep]
            if is_cycle:
                if len(kept) < 3:
                    continue
                anchor = keep[0]
                rotated = [coords[(anchor + k) % len(coords)]
                           for k in range(len(coords))]
                rotated.append(coords[anchor])
                targets = catmull_rom_even(rotated, len(kept))
                movable = range(1, len(kept))
            else:
                if len(kept) < 3:
                    continue
                targets = catmull_rom_even(coords, len(kept) - 1)
                movable = range(1, len(kept) - 1)
            for k in movable:
                vert = kept[k]
                if not vert.is_valid:
                    continue
                location, _normal, _index, _dist = bvh.find_nearest(targets[k])
                vert.co = location if location is not None else targets[k]


class BTOPO_OT_rebuild_patch(Operator):
    """Resynthesise a CAD patch interior as an even quad grid.

    Works on any interior topology — ladders, T-junctions, triangles,
    ngons: the interior is discarded wholesale and rebuilt between the
    patch's rails with transfinite (Coons) interpolation, every new vertex
    projected onto a snapshot of the original surface. Four-sided patches
    become m×n grids; two-loop patches (bevel rings, cylinder walls)
    become bridged rings. Run Simplify Rails first to set the boundary
    density — the rails are the spans.

    Where opposite rails disagree, Match Sides resolves the mismatch by
    dissolving the flattest excess vertices on the denser rail or
    conformally subdividing the sparser one.
    """

    bl_idname = "btopo.rebuild_patch"
    bl_label = "Rebuild Patch"
    bl_description = (
        "Replace the selected region's interior with an even quad grid "
        "spanned between its boundary rails and projected onto the "
        "original surface"
    )
    bl_options = {'REGISTER', 'UNDO'}

    rotate: IntProperty(
        name="Rotate Grid",
        description=(
            "Rotate which boundary sides pair as opposites when the grid "
            "runs the wrong way"
        ),
        default=0,
        min=0,
        max=3,
    )

    match_mode: EnumProperty(
        name="Match Sides",
        description="How to resolve mismatched vertex counts on opposite rails",
        items=(
            ('DISSOLVE', "Dissolve Denser",
             "Remove the flattest excess vertices from the denser rail"),
            ('SUBDIVIDE', "Subdivide Sparser",
             "Insert vertices into the sparser rail's longest edges "
             "(conforming — neighbouring faces gain the vertex too)"),
            ('OFF', "Off", "Fail with the counts instead of changing rails"),
        ),
        default='DISSOLVE',
    )

    use_auto_cuts: BoolProperty(
        name="Auto Cuts",
        description=(
            "For two-loop ring patches, choose the span count for roughly "
            "square quads"
        ),
        default=True,
    )

    cuts: IntProperty(
        name="Cuts",
        description="Interior loops across a two-loop ring patch",
        default=2,
        min=0,
        max=200,
    )

    use_patch: BoolProperty(
        name="Whole CAD Patch",
        description=(
            "Grow the selection to entire Plasticity CAD faces (uses the "
            "patch ids baked by Detect Features / CAD Cleanup)"
        ),
        default=True,
    )

    smooth_iterations: IntProperty(
        name="Smooth Iterations",
        description=(
            "Projected relaxation passes on the new interior: evens the "
            "quad distribution along curvature while staying glued to the "
            "original surface (rails never move)"
        ),
        default=5,
        min=0,
        max=100,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        region = _strip_region(bm, self.use_patch)
        if not region:
            self.report({'ERROR'}, "Select the faces of a patch to rebuild")
            return {'CANCELLED'}
        layer = bm.faces.layers.int.get(PATCH_LAYER)
        patch_id = region[0][layer] if layer is not None else None

        try:
            cycles = region_boundary_cycles([tuple(f.verts) for f in region])
        except ValueError as exc:
            self.report({'ERROR'}, str(exc).capitalize())
            return {'CANCELLED'}
        if len(cycles) > 2:
            self.report({'ERROR'},
                        f"Region has {len(cycles)} boundary loops — rebuild "
                        "patches with one loop (grid) or two (ring)")
            return {'CANCELLED'}

        # Snapshot the original surface before any destruction; rebuilt
        # vertices are projected onto it.
        bvh = BVHTree.FromBMesh(bm)

        boundary_verts = {v for cycle in cycles for v in cycle}
        interior = list({v for f in region for v in f.verts
                         if v not in boundary_verts})
        if interior:
            bmesh.ops.delete(bm, geom=interior, context='VERTS')
        leftovers = [f for f in region if f.is_valid]
        if leftovers:
            bmesh.ops.delete(bm, geom=leftovers, context='FACES')

        for elem in (*bm.verts, *bm.edges, *bm.faces):
            elem.select = False

        try:
            if len(cycles) == 1:
                new_faces = self._rebuild_grid(bm, bvh, cycles[0])
            else:
                new_faces = self._rebuild_ring(bm, bvh, cycles)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc).capitalize())
            return {'CANCELLED'}

        if layer is not None and patch_id is not None:
            layer = bm.faces.layers.int.get(PATCH_LAYER)
            for face in new_faces:
                face[layer] = patch_id
        for face in new_faces:
            face.smooth = True
            face.select = True
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Rebuilt patch with {len(new_faces)} quads")
        return {'FINISHED'}

    def _match_counts(self, bm, side_x, side_y, turns, closed=False):
        """Equalize two rails' vertex counts per match_mode (in place)."""
        while len(side_x) != len(side_y):
            longer, shorter = ((side_x, side_y)
                               if len(side_x) > len(side_y)
                               else (side_y, side_x))
            if self.match_mode == 'OFF':
                raise ValueError(
                    f"opposite rails have {len(side_x)} and {len(side_y)} "
                    "verts — enable Match Sides or fix with Simplify Rails")
            if self.match_mode == 'DISSOLVE':
                pool = longer if closed else longer[1:-1]
                if not pool:
                    raise ValueError(
                        "cannot dissolve rails down to matching counts — "
                        "try Subdivide Sparser")
                victim = min(pool, key=lambda v: turns.get(v, 0.0))
                bmesh.ops.dissolve_verts(bm, verts=[victim])
                longer.remove(victim)
            else:  # SUBDIVIDE
                count = len(shorter) if closed else len(shorter) - 1
                gaps = [(shorter[k], shorter[(k + 1) % len(shorter)])
                        for k in range(count)]
                a, b = max(gaps, key=lambda ab: (ab[1].co - ab[0].co).length)
                edge = _edge_between(a, b)
                if edge is None:
                    raise ValueError("rail edge missing — rebuild aborted")
                result = bmesh.ops.subdivide_edges(bm, edges=[edge], cuts=1)
                new_vert = next(e for e in result['geom_inner']
                                if isinstance(e, bmesh.types.BMVert))
                new_vert.select = False
                turns[new_vert] = 0.0
                shorter.insert(shorter.index(a) + 1, new_vert)

    def _rebuild_grid(self, bm, bvh, loop):
        sides_idx = split_loop_sides([v.co for v in loop],
                                     rotate=self.rotate)
        sides = [[loop[i] for i in side] for side in sides_idx]
        turns = _loop_turns(loop)
        self._match_counts(bm, sides[0], sides[2], turns)
        self._match_counts(bm, sides[1], sides[3], turns)

        bottom = sides[0]
        right = sides[1]
        top = list(reversed(sides[2]))
        left = list(reversed(sides[3]))
        m = len(bottom) - 1
        n = len(left) - 1

        interior = coons_interior(
            [v.co.copy() for v in bottom], [v.co.copy() for v in right],
            [v.co.copy() for v in top], [v.co.copy() for v in left])

        grid = [[None] * (n + 1) for _ in range(m + 1)]
        for i in range(m + 1):
            grid[i][0] = bottom[i]
            grid[i][n] = top[i]
        for j in range(n + 1):
            grid[0][j] = left[j]
            grid[m][j] = right[j]
        for j in range(1, n):
            for i in range(1, m):
                grid[i][j] = bm.verts.new(_project(bvh, interior[j - 1][i - 1]))

        self._relax_interior(grid, wrap=False, bvh=bvh)
        return _grid_faces(bm, grid, wrap=False)

    def _rebuild_ring(self, bm, bvh, cycles):
        rail_a, rail_b = cycles
        turns = {**_loop_turns(rail_a), **_loop_turns(rail_b)}
        self._match_counts(bm, rail_a, rail_b, turns, closed=True)

        coords_a = [v.co.copy() for v in rail_a]
        coords_b = [v.co.copy() for v in rail_b]
        order = align_rail(coords_a, coords_b, True)
        rail_b = [rail_b[i] for i in order]
        coords_b = [coords_b[i] for i in order]

        cuts = (auto_cuts(coords_a, coords_b, True)
                if self.use_auto_cuts else self.cuts)
        vert_rows = [rail_a]
        for row in grid_rows(coords_a, coords_b, cuts):
            vert_rows.append([bm.verts.new(_project(bvh, co)) for co in row])
        vert_rows.append(rail_b)

        # Transpose into the station-major grid the face builder expects.
        grid = [[row[i] for row in vert_rows] for i in range(len(rail_a))]
        self._relax_interior(grid, wrap=True, bvh=bvh)
        return _grid_faces(bm, grid, wrap=True)

    def _relax_interior(self, grid, wrap, bvh):
        if self.smooth_iterations <= 0 or len(grid[0]) < 3:
            return
        coords = [[v.co.copy() for v in row] for row in grid]
        relaxed = relax_grid(coords, wrap, self.smooth_iterations,
                             project=lambda co: _project(bvh, co))
        i_range = range(len(grid)) if wrap else range(1, len(grid) - 1)
        for i in i_range:
            for j in range(1, len(grid[0]) - 1):
                grid[i][j].co = relaxed[i][j]


def _project(bvh, co):
    location, _normal, _index, _dist = bvh.find_nearest(co)
    return location if location is not None else co


def _grid_faces(bm, grid, wrap):
    new_faces = []
    stations = len(grid) if wrap else len(grid) - 1
    spans = len(grid[0]) - 1
    for i in range(stations):
        i2 = (i + 1) % len(grid)
        for j in range(spans):
            corners = (grid[i][j], grid[i2][j],
                       grid[i2][j + 1], grid[i][j + 1])
            if len(set(corners)) < 4 or bm.faces.get(corners):
                continue
            new_faces.append(bm.faces.new(corners))
    return new_faces


class BTOPO_OT_set_strip_spans(Operator):
    """Rebuild a regular quad strip with a chosen number of spans.

    Fast path for strips that are already clean grids; for irregular
    interiors use Rebuild Patch. Rails stay untouched and new interior
    vertices are placed by arc length along the existing cross-sections.
    """

    bl_idname = "btopo.set_strip_spans"
    bl_label = "Set Strip Spans"
    bl_description = (
        "Rebuild the selected regular quad strip with the given number of "
        "spans across it, keeping its rails fixed"
    )
    bl_options = {'REGISTER', 'UNDO'}

    spans: IntProperty(
        name="Spans",
        description="Number of quad spans across the strip",
        default=2,
        min=1,
        max=64,
    )

    flip: BoolProperty(
        name="Flip Direction",
        description="Re-span along the strip instead of across it",
        default=False,
    )

    use_patch: BoolProperty(
        name="Whole CAD Patch",
        description=(
            "Grow the selection to entire Plasticity CAD faces (uses the "
            "patch ids baked by Detect Features / CAD Cleanup)"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        region = _strip_region(bm, self.use_patch)
        if not region:
            self.report({'ERROR'}, "Select the faces of a quad strip")
            return {'CANCELLED'}
        try:
            rows, closed = recover_grid(
                [tuple(f.verts) for f in region], flip=self.flip)
        except ValueError as exc:
            self.report({'ERROR'},
                        f"{str(exc).capitalize()} — for irregular regions "
                        "use Rebuild Patch")
            return {'CANCELLED'}

        layer = bm.faces.layers.int.get(PATCH_LAYER)
        patch_id = region[0][layer] if layer is not None else None
        spans_before = len(rows[0]) - 1
        new_cross = [
            resample_polyline_count([v.co.copy() for v in column], self.spans)
            for column in rows
        ]

        interior = [v for column in rows for v in column[1:-1]]
        if interior:
            bmesh.ops.delete(bm, geom=interior, context='VERTS')
        leftovers = [f for f in region if f.is_valid]
        if leftovers:
            bmesh.ops.delete(bm, geom=leftovers, context='FACES')

        grid = []
        for i, column in enumerate(rows):
            new_column = [column[0]]
            for j in range(1, self.spans):
                new_column.append(bm.verts.new(new_cross[i][j]))
            new_column.append(column[-1])
            grid.append(new_column)

        new_faces = _grid_faces(bm, grid, wrap=closed)
        for face in new_faces:
            face.smooth = True
            face.select = True
            if layer is not None and patch_id is not None:
                face[layer] = patch_id

        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        self.report(
            {'INFO'},
            f"Rebuilt strip: {spans_before} → {self.spans} spans "
            f"({len(new_faces)} quads)",
        )
        return {'FINISHED'}


class BTOPO_OT_simplify_strip(Operator):
    """Collapse ladder rungs along a regular quad strip by curvature.

    Fast path for strips that are already clean grids; for irregular
    interiors use Simplify Rails + Rebuild Patch. Dissolves rung edges
    (never vertices across rails, so feature boundaries are preserved),
    keeping a curvature-driven subset of cross-sections.
    """

    bl_idname = "btopo.simplify_strip"
    bl_label = "Simplify Strip"
    bl_description = (
        "Collapse ladder rungs along the selected regular quad strip, "
        "keeping a curvature-driven subset"
    )
    bl_options = {'REGISTER', 'UNDO'}

    segment_angle: FloatProperty(
        name="Segment Angle",
        description="Curvature per kept rung along the strip",
        subtype='ANGLE',
        default=math.radians(30.0),
        min=math.radians(1.0),
        max=math.radians(120.0),
    )

    max_edge: FloatProperty(
        name="Max Edge Length",
        description="Keep rungs so no edge exceeds this length (0 = unlimited)",
        subtype='DISTANCE',
        default=0.0,
        min=0.0,
    )

    flip: BoolProperty(
        name="Flip Direction",
        description="Simplify across the strip instead of along it",
        default=False,
    )

    use_patch: BoolProperty(
        name="Whole CAD Patch",
        description=(
            "Grow the selection to entire Plasticity CAD faces (uses the "
            "patch ids baked by Detect Features / CAD Cleanup)"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'

    def invoke(self, context, event):
        settings = context.scene.btopo
        self.segment_angle = settings.trace_segment_angle
        self.max_edge = settings.trace_max_edge
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        region = _strip_region(bm, self.use_patch)
        if not region:
            self.report({'ERROR'}, "Select the faces of a quad strip")
            return {'CANCELLED'}
        try:
            rows, closed = recover_grid(
                [tuple(f.verts) for f in region], flip=self.flip)
        except ValueError as exc:
            self.report({'ERROR'},
                        f"{str(exc).capitalize()} — for irregular regions "
                        "use Simplify Rails + Rebuild Patch")
            return {'CANCELLED'}

        rail_coords = [column[0].co.copy() for column in rows]
        keep = set(resample_curve(rail_coords, closed,
                                  self.segment_angle, self.max_edge))
        dropped = [i for i in range(len(rows)) if i not in keep]
        if not dropped:
            self.report({'INFO'}, "Nothing to simplify at this density")
            return {'FINISHED'}

        # Dissolve the dropped cross-sections' rung edges. use_verts then
        # removes the stranded 2-valence vertices along rails and interior
        # lines. Dissolving edges (not vertices) is what keeps the rails
        # intact: faces only ever merge within the strip or within the
        # neighbouring patch, never across a feature boundary.
        rung_edges = set()
        for i in dropped:
            column = rows[i]
            for a, b in zip(column, column[1:]):
                edge = _edge_between(a, b)
                if edge is not None:
                    rung_edges.add(edge)
        bmesh.ops.dissolve_edges(bm, edges=list(rung_edges), use_verts=True)

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Collapsed {len(dropped)} of {len(rows)} rungs")
        return {'FINISHED'}


_classes = (
    BTOPO_OT_simplify_rails,
    BTOPO_OT_rebuild_patch,
    BTOPO_OT_set_strip_spans,
    BTOPO_OT_simplify_strip,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
