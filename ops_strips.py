"""In-place strip editing: ladder collapse and bevel re-spanning.

These operate directly on the CAD tessellation. Kept vertices are original,
exactly-on-surface vertices; new vertices (re-spanning only) are placed by
arc length on existing cross-section polylines. No vertex ever slides.
"""

import math

import bmesh
import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator

from .feature_graph import resample_curve
from .strip_grid import recover_grid, resample_polyline_count

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


class BTOPO_OT_set_strip_spans(Operator):
    """Rebuild a bevel/fillet strip with a chosen number of spans.

    The strip's rails stay untouched (neighbouring patches are unaffected)
    and new interior vertices are placed by arc length along the existing
    cross-section polylines, so they lie on the source surface by
    construction. Matching span counts across adjacent bevels is what makes
    loops continuous around the part.
    """

    bl_idname = "btopo.set_strip_spans"
    bl_label = "Set Strip Spans"
    bl_description = (
        "Rebuild the selected quad strip with the given number of spans "
        "across it, keeping its rails fixed"
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
            "patch ids baked by CAD Cleanup)"
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
            self.report({'ERROR'}, str(exc).capitalize())
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

        new_faces = []
        stations = len(grid) if closed else len(grid) - 1
        for i in range(stations):
            i2 = (i + 1) % len(grid)
            for j in range(self.spans):
                corners = (grid[i][j], grid[i2][j],
                           grid[i2][j + 1], grid[i][j + 1])
                if len(set(corners)) < 4 or bm.faces.get(corners):
                    continue
                face = bm.faces.new(corners)
                face.smooth = True
                face.select = True
                if layer is not None and patch_id is not None:
                    face[layer] = patch_id
                new_faces.append(face)

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
    """Collapse ladder rungs along a strip by curvature.

    Dissolves whole cross-sections, keeping a curvature-driven subset of
    the originals (same density rules as Trace Feature Loops): straight
    runs collapse to a single segment, curved runs keep one rung per
    segment angle. Kept vertices are untouched originals — nothing slides.
    Rail vertices of dropped rungs dissolve out of the neighbouring faces
    too, so the ladder stops propagating into adjacent patches.
    """

    bl_idname = "btopo.simplify_strip"
    bl_label = "Simplify Strip"
    bl_description = (
        "Collapse ladder rungs along the selected quad strip, keeping a "
        "curvature-driven subset"
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
            "patch ids baked by CAD Cleanup)"
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
            self.report({'ERROR'}, str(exc).capitalize())
            return {'CANCELLED'}

        rail_coords = [column[0].co.copy() for column in rows]
        keep = set(resample_curve(rail_coords, closed,
                                  self.segment_angle, self.max_edge))
        dropped = [i for i in range(len(rows)) if i not in keep]
        if not dropped:
            self.report({'INFO'}, "Nothing to simplify at this density")
            return {'FINISHED'}

        victims = [v for i in dropped for v in rows[i]]
        bmesh.ops.dissolve_verts(bm, verts=victims)
        bmesh.update_edit_mesh(obj.data)
        self.report(
            {'INFO'},
            f"Collapsed {len(dropped)} of {len(rows)} rungs",
        )
        return {'FINISHED'}


_classes = (
    BTOPO_OT_set_strip_spans,
    BTOPO_OT_simplify_strip,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
