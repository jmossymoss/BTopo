import bmesh
import bpy
from bpy.types import Operator

from .ops_analyze import detect_features


class BTOPO_OT_cleanup_cad(Operator):
    """One-click repair-in-place pass for CAD tessellations.

    Welds patch-seam doubles, dissolves laddered triangulation on flat and
    gently curved regions (never crossing feature edges), then joins the
    remaining triangles into quads.
    """

    bl_idname = "btopo.cleanup_cad"
    bl_label = "CAD Cleanup"
    bl_description = (
        "Weld doubles, dissolve ladders away from feature edges, and join "
        "triangles into quads"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'OBJECT'

    def execute(self, context):
        obj = context.active_object
        settings = context.scene.btopo

        # The dissolve is delimited by sharp edges, so features must be
        # marked first or the pass would eat them.
        if all(e.use_edge_sharp is False for e in obj.data.edges):
            detect_features(obj, settings.feature_angle,
                            mark_seams=settings.mark_seams)

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        faces_before = len(bm.faces)
        verts_before = len(bm.verts)

        if settings.merge_distance > 0.0:
            bmesh.ops.remove_doubles(
                bm, verts=bm.verts, dist=settings.merge_distance)

        bmesh.ops.dissolve_limit(
            bm,
            angle_limit=settings.dissolve_angle,
            use_dissolve_boundaries=False,
            verts=bm.verts,
            edges=bm.edges,
            delimit={'SHARP', 'SEAM'},
        )

        if settings.quadify:
            tris = [f for f in bm.faces if len(f.verts) == 3]
            bmesh.ops.join_triangles(
                bm,
                faces=tris,
                cmp_sharp=True,
                cmp_seam=True,
                angle_face_threshold=settings.feature_angle,
                angle_shape_threshold=settings.feature_angle,
            )

        faces_after = len(bm.faces)
        verts_after = len(bm.verts)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()

        self.report(
            {'INFO'},
            f"Faces {faces_before} → {faces_after}, "
            f"verts {verts_before} → {verts_after}",
        )
        return {'FINISHED'}


_classes = (BTOPO_OT_cleanup_cad,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
