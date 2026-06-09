import bmesh
import bpy
from bpy.props import BoolProperty, IntProperty
from bpy.types import Operator
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from .feature_graph import build_feature_curves, resample_curve
from .ops_analyze import (_bmesh_for_object, _finish_bmesh, detect_features,
                          plasticity_face_groups)
from .strip_fill import align_rail, auto_cuts, grid_rows, order_rails


def session_source(obj):
    """The session's reference surface: the object's shrinkwrap target."""
    if obj is None or obj.type != 'MESH':
        return None
    for modifier in obj.modifiers:
        if (modifier.type == 'SHRINKWRAP' and modifier.target is not None
                and modifier.target.type == 'MESH'):
            return modifier.target
    return None


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
        return session_source(context.active_object) is not None

    def execute(self, context):
        retopo = context.active_object
        source = session_source(retopo)
        settings = context.scene.btopo

        if not any(e.use_edge_sharp for e in source.data.edges):
            face_groups = (plasticity_face_groups(source.data)
                           if settings.use_plasticity else None)
            detect_features(source, settings.feature_angle,
                            mark_seams=settings.mark_seams,
                            face_groups=face_groups,
                            tangent_boundaries=settings.plasticity_tangent)

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


class BTOPO_OT_bridge_fill(Operator):
    """Fill between two selected rails with a surface-projected quad strip.

    Select two edge runs in the retopo mesh (typically traced feature
    loops). Equal vertex counts produce a pure quad grid: interior rows
    are interpolated between the rails and projected onto the reference
    surface, with cut count chosen automatically for roughly square quads.
    Mismatched counts fall back to Blender's bridge, which needs triangles.
    """

    bl_idname = "btopo.bridge_fill"
    bl_label = "Bridge Fill"
    bl_description = (
        "Fill between two selected edge runs with quads projected onto the "
        "reference surface"
    )
    bl_options = {'REGISTER', 'UNDO'}

    use_auto_cuts: BoolProperty(
        name="Auto Cuts",
        description="Choose the cut count so the quads come out roughly square",
        default=True,
    )

    cuts: IntProperty(
        name="Cuts",
        description="Number of interior edge loops in the bridge",
        default=2,
        min=0,
        max=200,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.mode == 'EDIT'
                and session_source(obj) is not None)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "use_auto_cuts")
        row = layout.row()
        row.enabled = not self.use_auto_cuts
        row.prop(self, "cuts")

    def execute(self, context):
        retopo = context.active_object
        source = session_source(retopo)
        bm = bmesh.from_edit_mesh(retopo.data)

        selected = [e for e in bm.edges if e.select]
        if not selected:
            self.report({'ERROR'}, "Select the two rails to bridge")
            return {'CANCELLED'}
        try:
            rails = order_rails(selected)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc).capitalize())
            return {'CANCELLED'}
        if len(rails) != 2:
            self.report({'ERROR'},
                        f"Select exactly two edge runs (found {len(rails)})")
            return {'CANCELLED'}
        (verts_a, cycle_a), (verts_b, cycle_b) = rails
        if cycle_a != cycle_b:
            self.report({'ERROR'}, "Cannot bridge an open run to a closed loop")
            return {'CANCELLED'}

        if len(verts_a) != len(verts_b):
            result = bmesh.ops.bridge_loops(bm, edges=selected)
            for face in result['faces']:
                face.smooth = True
                face.select = True
            bm.select_flush_mode()
            bmesh.update_edit_mesh(retopo.data)
            self.report(
                {'WARNING'},
                f"Rails have {len(verts_a)} and {len(verts_b)} verts — used "
                "Blender bridge (triangles); equalize counts for pure quads",
            )
            return {'FINISHED'}

        coords_a = [v.co.copy() for v in verts_a]
        coords_b = [v.co.copy() for v in verts_b]
        order = align_rail(coords_a, coords_b, cycle_a)
        verts_b = [verts_b[i] for i in order]
        coords_b = [coords_b[i] for i in order]

        cuts = (auto_cuts(coords_a, coords_b, cycle_a)
                if self.use_auto_cuts else self.cuts)

        depsgraph = context.evaluated_depsgraph_get()
        bvh = BVHTree.FromObject(source, depsgraph)
        to_source = source.matrix_world.inverted_safe() @ retopo.matrix_world
        from_source = to_source.inverted_safe()
        normal_to_retopo = from_source.to_3x3()

        def project(co):
            location, normal, _index, _dist = bvh.find_nearest(to_source @ co)
            if location is None:
                return co, None
            return from_source @ location, normal

        vert_rows = [verts_a]
        for row in grid_rows(coords_a, coords_b, cuts):
            vert_rows.append([bm.verts.new(project(co)[0]) for co in row])
        vert_rows.append(verts_b)

        for elem in (*bm.verts, *bm.edges, *bm.faces):
            elem.select = False

        count = len(coords_a)
        spans = count if cycle_a else count - 1
        new_faces = []
        for r in range(len(vert_rows) - 1):
            row_a, row_b = vert_rows[r], vert_rows[r + 1]
            for i in range(spans):
                j = (i + 1) % count
                corners = (row_a[i], row_a[j], row_b[j], row_b[i])
                if len(set(corners)) < 4 or bm.faces.get(corners):
                    continue
                face = bm.faces.new(corners)
                face.smooth = True
                new_faces.append(face)

        # Orient the new faces with the reference surface so the strip
        # doesn't come out inside-out.
        for face in new_faces:
            face.normal_update()
            _location, normal = project(face.calc_center_median())
            if normal is not None and face.normal.dot(normal_to_retopo @ normal) < 0:
                face.normal_flip()
            face.select = True

        bm.select_flush_mode()
        bmesh.update_edit_mesh(retopo.data)
        self.report({'INFO'},
                    f"Bridged with {len(new_faces)} quads ({cuts} cuts)")
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
    BTOPO_OT_bridge_fill,
    BTOPO_OT_end_retopo,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
