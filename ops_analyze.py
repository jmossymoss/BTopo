import bmesh
import bpy
from bpy.props import EnumProperty
from bpy.types import Operator


def _bmesh_for_object(obj):
    """Return (bm, is_edit_mode) for the object's mesh."""
    if obj.mode == 'EDIT':
        return bmesh.from_edit_mesh(obj.data), True
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    return bm, False


def _finish_bmesh(obj, bm, is_edit_mode):
    if is_edit_mode:
        bmesh.update_edit_mesh(obj.data)
    else:
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()


def detect_features(obj, angle, mark_seams=False):
    """Mark feature edges sharp (and optionally as seams).

    An edge is a feature if its faces meet at more than `angle`, or it is a
    boundary or non-manifold edge. Returns the number of feature edges.
    """
    bm, is_edit = _bmesh_for_object(obj)
    count = 0
    for edge in bm.edges:
        face_angle = edge.calc_face_angle(None)
        is_feature = face_angle is None or face_angle > angle
        edge.smooth = not is_feature
        if mark_seams:
            edge.seam = is_feature
        if is_feature:
            count += 1
    _finish_bmesh(obj, bm, is_edit)
    return count


class BTOPO_OT_detect_features(Operator):
    """Detect feature edges on the CAD mesh and mark them sharp.

    Feature edges are the foundation for the cleanup and retopo tools: they
    delimit dissolves, drive shading splits, and trace the design intent.
    """

    bl_idname = "btopo.detect_features"
    bl_label = "Detect Features"
    bl_description = (
        "Mark edges sharp where the face angle exceeds the feature angle, "
        "plus boundary and non-manifold edges"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        settings = context.scene.btopo
        count = detect_features(
            context.active_object,
            settings.feature_angle,
            mark_seams=settings.mark_seams,
        )
        self.report({'INFO'}, f"Marked {count} feature edges")
        return {'FINISHED'}


class BTOPO_OT_select_issues(Operator):
    """Select topology problems for QA passes."""

    bl_idname = "btopo.select_issues"
    bl_label = "Select Issues"
    bl_description = "Select problematic topology on the active mesh"
    bl_options = {'REGISTER', 'UNDO'}

    issue_type: EnumProperty(
        name="Issue",
        items=(
            ('TRIS', "Triangles", "Faces with 3 sides"),
            ('NGONS', "N-gons", "Faces with more than 4 sides"),
            ('POLES', "Poles", "Interior vertices whose edge count is not 4"),
            ('NON_MANIFOLD', "Non-Manifold", "Non-manifold edges and vertices"),
        ),
        default='TRIS',
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        for elem in (*bm.verts, *bm.edges, *bm.faces):
            elem.select = False

        count = 0
        if self.issue_type in {'TRIS', 'NGONS'}:
            context.tool_settings.mesh_select_mode = (False, False, True)
            for face in bm.faces:
                hit = (len(face.verts) == 3 if self.issue_type == 'TRIS'
                       else len(face.verts) > 4)
                if hit:
                    face.select = True
                    count += 1
        elif self.issue_type == 'POLES':
            context.tool_settings.mesh_select_mode = (True, False, False)
            for vert in bm.verts:
                if vert.is_boundary or not vert.link_edges:
                    continue
                if len(vert.link_edges) != 4:
                    vert.select = True
                    count += 1
        else:  # NON_MANIFOLD
            context.tool_settings.mesh_select_mode = (False, True, False)
            for edge in bm.edges:
                if not edge.is_manifold:
                    edge.select = True
                    count += 1

        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Selected {count} {self.issue_type.lower().replace('_', '-')}")
        return {'FINISHED'}


_classes = (
    BTOPO_OT_detect_features,
    BTOPO_OT_select_issues,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
