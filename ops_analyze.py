import bisect

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


def plasticity_face_groups(mesh):
    """Map each polygon to its Plasticity CAD face, or None if no bridge data.

    The Plasticity Blender bridge stores one (loop_start, loop_count) pair
    per CAD BREP face in mesh["groups"]; a polygon belongs to the group
    whose loop range contains its loop_start. Returns a list with one group
    ordinal per polygon (-1 where unresolvable).
    """
    groups = mesh.get("groups")
    if groups is None:
        return None
    groups = list(groups)
    if len(groups) < 2 or len(groups) % 2:
        return None
    starts = groups[0::2]
    counts = groups[1::2]
    order = sorted(range(len(starts)), key=starts.__getitem__)
    sorted_starts = [starts[i] for i in order]

    face_groups = []
    for polygon in mesh.polygons:
        k = bisect.bisect_right(sorted_starts, polygon.loop_start) - 1
        group = order[k] if k >= 0 else -1
        if group >= 0 and polygon.loop_start >= starts[group] + counts[group]:
            group = -1
        face_groups.append(group)
    return face_groups


def bake_patch_attribute(mesh, face_groups):
    """Persist CAD face ids as a face attribute that survives topology edits.

    mesh["groups"] maps loop ranges of the original tessellation and goes
    stale the moment topology changes; the baked attribute rides along on
    whatever faces remain. Returns True if baked.
    """
    if face_groups is None or len(face_groups) != len(mesh.polygons):
        return False
    attribute = mesh.attributes.get("btopo_patch")
    if attribute is None:
        attribute = mesh.attributes.new("btopo_patch", 'INT', 'FACE')
    elif attribute.domain != 'FACE' or attribute.data_type != 'INT':
        return False
    attribute.data.foreach_set("value", face_groups)
    return True


def detect_features(obj, angle, mark_seams=False, face_groups=None,
                    tangent_boundaries=False):
    """Mark feature edges sharp (and optionally as seams).

    Without `face_groups`, an edge is a feature if its faces meet at more
    than `angle`, or it is a boundary or non-manifold edge.

    With `face_groups` (CAD face per polygon, e.g. from the Plasticity
    bridge) detection becomes exact: edges interior to one CAD face are
    never features regardless of tessellation noise, and CAD face
    boundaries are features when they exceed `angle` — or unconditionally
    if `tangent_boundaries` is set, which also captures the smooth fillet
    transitions that an angle test can never see.

    Returns the number of feature edges.
    """
    bm, is_edit = _bmesh_for_object(obj)
    use_groups = (face_groups is not None
                  and len(face_groups) == len(bm.faces))
    count = 0
    for edge in bm.edges:
        face_angle = edge.calc_face_angle(None)
        if face_angle is None:
            is_feature = True  # boundary or non-manifold
        elif use_groups:
            group_a, group_b = (face_groups[f.index] for f in edge.link_faces)
            if group_a == group_b and group_a != -1:
                is_feature = False
            else:
                is_feature = tangent_boundaries or face_angle > angle
        else:
            is_feature = face_angle > angle
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
        obj = context.active_object
        face_groups = None
        if settings.use_plasticity:
            face_groups = plasticity_face_groups(obj.data)
            if obj.mode == 'OBJECT':
                bake_patch_attribute(obj.data, face_groups)
        count = detect_features(
            obj,
            settings.feature_angle,
            mark_seams=settings.mark_seams,
            face_groups=face_groups,
            tangent_boundaries=settings.plasticity_tangent,
        )
        source = ("Plasticity CAD face groups" if face_groups
                  else "face angle")
        self.report({'INFO'}, f"Marked {count} feature edges ({source})")
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
