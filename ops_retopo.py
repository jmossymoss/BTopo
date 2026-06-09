import bmesh
import bpy
from bpy.types import Operator
from mathutils.kdtree import KDTree

from .feature_graph import build_feature_curves, resample_curve
from .ops_analyze import _bmesh_for_object, _finish_bmesh, detect_features


class BTOPO_OT_setup_retopo(Operator):
    """Start a retopo-over session for the active CAD mesh.

    Creates an empty `<name>_retopo` mesh, configures surface snapping, adds
    a Shrinkwrap modifier targeting the source, and sets up display so the
    new topology reads clearly over the reference. The source object stays
    visible but unselectable — it is the visual reference and, later, the
    bake high-poly.
    """

    bl_idname = "btopo.setup_retopo"
    bl_label = "Start Retopo Session"
    bl_description = (
        "Create a snapped, shrinkwrapped retopo object over the active mesh"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH'
                and obj.mode == 'OBJECT'
                and not obj.name.endswith("_retopo"))

    def execute(self, context):
        source = context.active_object
        settings = context.scene.btopo

        mesh = bpy.data.meshes.new(f"{source.name}_retopo")
        retopo = bpy.data.objects.new(f"{source.name}_retopo", mesh)
        for collection in source.users_collection:
            collection.objects.link(retopo)
        retopo.matrix_world = source.matrix_world.copy()

        if settings.use_mirror:
            mirror = retopo.modifiers.new("BTopo Mirror", 'MIRROR')
            mirror.use_clip = True

        shrinkwrap = retopo.modifiers.new("BTopo Shrinkwrap", 'SHRINKWRAP')
        shrinkwrap.target = source
        shrinkwrap.wrap_method = 'NEAREST_SURFACEPOINT'
        shrinkwrap.wrap_mode = 'ABOVE_SURFACE'
        shrinkwrap.offset = settings.shrinkwrap_offset

        retopo.show_in_front = True
        retopo.show_wire = True
        retopo.show_all_edges = True
        retopo.color = (0.2, 0.7, 1.0, 1.0)

        source.hide_select = True

        tool_settings = context.tool_settings
        tool_settings.use_snap = True
        if 'FACE_NEAREST' in tool_settings.bl_rna.properties['snap_elements'].enum_items:
            tool_settings.snap_elements = {'FACE_NEAREST'}
        else:
            tool_settings.snap_elements = {'FACE'}
        tool_settings.use_snap_self = False

        for obj in context.selected_objects:
            obj.select_set(False)
        retopo.select_set(True)
        context.view_layer.objects.active = retopo
        bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, f"Retopo session started: {retopo.name}")
        return {'FINISHED'}


class BTOPO_OT_trace_features(Operator):
    """Generate the retopo cage from the reference mesh's feature graph.

    Walks the reference mesh's feature edges, splits them into curves at
    corners and junctions, resamples each curve down to game resolution,
    and creates the matching vertices and edges in the retopo mesh. The
    result is the structural cage of the asset — the artist fills between
    the rails instead of placing every loop by hand.
    """

    bl_idname = "btopo.trace_features"
    bl_label = "Trace Feature Loops"
    bl_description = (
        "Generate resampled edge loops in the retopo mesh from the "
        "reference mesh's feature edges"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return cls._find_source(context.active_object) is not None

    @staticmethod
    def _find_source(obj):
        """The reference surface is the target of the session's shrinkwrap."""
        if obj is None or obj.type != 'MESH':
            return None
        for modifier in obj.modifiers:
            if (modifier.type == 'SHRINKWRAP' and modifier.target is not None
                    and modifier.target.type == 'MESH'):
                return modifier.target
        return None

    def execute(self, context):
        retopo = context.active_object
        source = self._find_source(retopo)
        settings = context.scene.btopo

        if not any(e.use_edge_sharp for e in source.data.edges):
            detect_features(source, settings.feature_angle,
                            mark_seams=settings.mark_seams)

        bm_source = bmesh.new()
        bm_source.from_mesh(source.data)
        curves = build_feature_curves(bm_source, settings.trace_corner_angle)

        to_local = retopo.matrix_world.inverted_safe() @ source.matrix_world
        resampled = []
        for verts, is_cycle in curves:
            coords = [v.co for v in verts]
            keep = resample_curve(coords, is_cycle,
                                  settings.trace_segment_angle,
                                  settings.trace_max_edge)
            resampled.append((
                [verts[i].index for i in keep],
                [to_local @ coords[i] for i in keep],
                is_cycle,
            ))
        bm_source.free()

        bm, is_edit = _bmesh_for_object(retopo)
        for elem in (*bm.verts, *bm.edges, *bm.faces):
            elem.select = False

        # Weld onto geometry the artist has already authored: new vertices
        # snap to existing ones within the merge distance.
        existing = list(bm.verts)
        kdtree = KDTree(len(existing))
        for i, vert in enumerate(existing):
            kdtree.insert(vert.co, i)
        kdtree.balance()

        vert_map = {}

        def vert_for(source_index, co):
            vert = vert_map.get(source_index)
            if vert is None:
                if existing:
                    _co, index, dist = kdtree.find(co)
                    if index is not None and dist <= settings.merge_distance:
                        vert = existing[index]
                if vert is None:
                    vert = bm.verts.new(co)
                vert_map[source_index] = vert
            return vert

        edge_count = 0
        for source_indices, coords, is_cycle in resampled:
            chain = [vert_for(si, co)
                     for si, co in zip(source_indices, coords)]
            pairs = list(zip(chain, chain[1:]))
            if is_cycle and len(chain) > 2:
                pairs.append((chain[-1], chain[0]))
            for a, b in pairs:
                if a is b:
                    continue
                edge = bm.edges.get((a, b)) or bm.edges.new((a, b))
                edge.smooth = False
                edge.select = True
                a.select = True
                b.select = True
                edge_count += 1

        bm.select_flush_mode()
        _finish_bmesh(retopo, bm, is_edit)

        self.report(
            {'INFO'},
            f"Traced {len(resampled)} feature curves "
            f"({len(vert_map)} verts, {edge_count} edges)",
        )
        return {'FINISHED'}


class BTOPO_OT_end_retopo(Operator):
    """Make the source selectable again after a retopo session."""

    bl_idname = "btopo.end_retopo"
    bl_label = "End Retopo Session"
    bl_description = "Restore selectability of source objects locked by Start Retopo Session"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        for obj in context.view_layer.objects:
            if obj.hide_select and f"{obj.name}_retopo" in bpy.data.objects:
                obj.hide_select = False
                count += 1
        self.report({'INFO'}, f"Unlocked {count} source object(s)")
        return {'FINISHED'}


_classes = (
    BTOPO_OT_setup_retopo,
    BTOPO_OT_trace_features,
    BTOPO_OT_end_retopo,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
