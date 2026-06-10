import math

import bmesh
import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from .feature_graph import build_feature_curves, resample_curve
from .ops_analyze import (_bmesh_for_object, _finish_bmesh, detect_features,
                          plasticity_face_groups)
from .patch_fill import coons_interior, split_loop_sides
from .strip_fill import (adjust_alignment, align_rail, auto_cuts, grid_rows,
                         order_rails)


def session_source(obj):
    """The session's reference surface for a retopo object."""
    if obj is None or obj.type != 'MESH':
        return None
    name = obj.get("btopo_source")
    if name:
        source = bpy.data.objects.get(name)
        if source is not None and source.type == 'MESH':
            return source
    # Legacy sessions linked source via a shrinkwrap modifier.
    for modifier in obj.modifiers:
        if (modifier.type == 'SHRINKWRAP' and modifier.target is not None
                and modifier.target.type == 'MESH'):
            return modifier.target
    return None


def _surface_projection(context, retopo, source):
    """Projection onto the reference surface, in retopo local space.

    Returns (project, normal_to_retopo): `project(co)` maps a retopo-local
    coordinate to (surface point, surface normal in source space) — or
    (co, None) if the BVH lookup fails — and `normal_to_retopo` brings
    those normals into retopo space for comparisons.
    """
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

    return project, normal_to_retopo


def _orient_and_select(faces, project, normal_to_retopo):
    """Flip new faces that disagree with the reference surface, select them."""
    for face in faces:
        face.normal_update()
        _location, normal = project(face.calc_center_median())
        if normal is not None and face.normal.dot(normal_to_retopo @ normal) < 0:
            face.normal_flip()
        face.select = True
        for vert in face.verts:
            vert.select = True
        for edge in face.edges:
            edge.select = True


class BTOPO_OT_setup_retopo(Operator):
    """Start a retopo-over session for the active CAD mesh.

    Creates an empty `<name>_retopo` mesh linked to its reference surface,
    configures face-nearest snapping, and sets up display so the new
    topology reads clearly over the reference. The source stays visible
    but unselectable — it is the visual reference and, later, the bake
    high-poly.

    Deliberately no shrinkwrap modifier: continuous conformance slides
    vertices and rounds hard corners. Generators project explicitly (one
    shot, at creation time); freehand edits rely on snapping, which only
    affects vertices being moved.
    """

    bl_idname = "btopo.setup_retopo"
    bl_label = "Start Retopo Session"
    bl_description = (
        "Create a surface-snapped retopo object over the active mesh"
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

        retopo["btopo_source"] = source.name

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

    # Operator copies of the scene trace settings (seeded in invoke), so
    # the whole trace stays adjustable in the redo panel after running.
    segment_angle: FloatProperty(
        name="Segment Angle",
        description="Curvature per segment when resampling feature curves",
        subtype='ANGLE',
        default=math.radians(30.0),
        min=math.radians(1.0),
        max=math.radians(120.0),
    )

    corner_angle: FloatProperty(
        name="Corner Angle",
        description="Turns sharper than this always keep a vertex",
        subtype='ANGLE',
        default=math.radians(45.0),
        min=math.radians(5.0),
        max=math.pi,
    )

    max_edge: FloatProperty(
        name="Max Edge Length",
        description="Subdivide traced edges longer than this (0 = unlimited)",
        subtype='DISTANCE',
        default=0.0,
        min=0.0,
    )

    @classmethod
    def poll(cls, context):
        return session_source(context.active_object) is not None

    def invoke(self, context, event):
        settings = context.scene.btopo
        self.segment_angle = settings.trace_segment_angle
        self.corner_angle = settings.trace_corner_angle
        self.max_edge = settings.trace_max_edge
        return self.execute(context)

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
        curves = build_feature_curves(bm_source, self.corner_angle)

        to_local = retopo.matrix_world.inverted_safe() @ source.matrix_world
        resampled = []
        for verts, is_cycle in curves:
            coords = [v.co for v in verts]
            keep = resample_curve(coords, is_cycle,
                                  self.segment_angle,
                                  self.max_edge)
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

    flip: BoolProperty(
        name="Flip",
        description=(
            "Reverse the second rail's direction when the automatic pairing "
            "crosses the strip over"
        ),
        default=False,
    )

    twist: IntProperty(
        name="Twist",
        description=(
            "Rotate the vertex correspondence around closed loops when the "
            "automatic pairing starts at the wrong vertex"
        ),
        default=0,
        min=-100,
        max=100,
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
        layout.prop(self, "flip")
        layout.prop(self, "twist")

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
        order = adjust_alignment(order, twist=self.twist, flip=self.flip,
                                 is_cycle=cycle_a)
        verts_b = [verts_b[i] for i in order]
        coords_b = [coords_b[i] for i in order]

        cuts = (auto_cuts(coords_a, coords_b, cycle_a)
                if self.use_auto_cuts else self.cuts)

        project, normal_to_retopo = _surface_projection(context, retopo, source)

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

        _orient_and_select(new_faces, project, normal_to_retopo)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(retopo.data)
        self.report({'INFO'},
                    f"Bridged with {len(new_faces)} quads ({cuts} cuts)")
        return {'FINISHED'}


class BTOPO_OT_patch_fill(Operator):
    """Fill a four-sided region with a surface-projected quad grid.

    Select the closed boundary loop of the patch (typically traced feature
    edges). The loop is split into four sides at its sharpest corners,
    the interior is interpolated as a Coons patch, and every new vertex is
    projected onto the reference surface. Opposite sides must have matching
    vertex counts for a pure quad grid.
    """

    bl_idname = "btopo.patch_fill"
    bl_label = "Patch Fill"
    bl_description = (
        "Fill the selected four-sided boundary loop with quads projected "
        "onto the reference surface"
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

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.mode == 'EDIT'
                and session_source(obj) is not None)

    def execute(self, context):
        retopo = context.active_object
        source = session_source(retopo)
        bm = bmesh.from_edit_mesh(retopo.data)

        selected = [e for e in bm.edges if e.select]
        if not selected:
            self.report({'ERROR'}, "Select the closed boundary of a patch")
            return {'CANCELLED'}
        try:
            rails = order_rails(selected)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc).capitalize())
            return {'CANCELLED'}
        if len(rails) != 1 or not rails[0][1]:
            self.report({'ERROR'},
                        "Select exactly one closed boundary loop "
                        f"(found {len(rails)} run(s))")
            return {'CANCELLED'}
        loop_verts = rails[0][0]

        try:
            sides = split_loop_sides([v.co for v in loop_verts],
                                     rotate=self.rotate)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc).capitalize())
            return {'CANCELLED'}
        side_verts = [[loop_verts[i] for i in side] for side in sides]
        side_a, side_b, side_c, side_d = side_verts
        if (len(side_a) != len(side_c) or len(side_b) != len(side_d)):
            self.report(
                {'ERROR'},
                "Opposite sides need matching vertex counts (got "
                f"{len(side_a) - 1}/{len(side_c) - 1} and "
                f"{len(side_b) - 1}/{len(side_d) - 1} edges) — try Rotate "
                "Grid, or adjust the cage with subdivide/dissolve",
            )
            return {'CANCELLED'}

        # Boundary rows in Coons orientation: bottom/top in the same
        # direction, left/right in the same direction (see coons_interior).
        bottom = side_a
        right = side_b
        top = list(reversed(side_c))
        left = list(reversed(side_d))
        m = len(bottom) - 1
        n = len(left) - 1

        project, normal_to_retopo = _surface_projection(context, retopo, source)
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
                grid[i][j] = bm.verts.new(project(interior[j - 1][i - 1])[0])

        for elem in (*bm.verts, *bm.edges, *bm.faces):
            elem.select = False

        new_faces = []
        for i in range(m):
            for j in range(n):
                corners = (grid[i][j], grid[i + 1][j],
                           grid[i + 1][j + 1], grid[i][j + 1])
                if len(set(corners)) < 4 or bm.faces.get(corners):
                    continue
                face = bm.faces.new(corners)
                face.smooth = True
                new_faces.append(face)

        _orient_and_select(new_faces, project, normal_to_retopo)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(retopo.data)
        self.report({'INFO'},
                    f"Filled {m}×{n} patch with {len(new_faces)} quads")
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
    BTOPO_OT_patch_fill,
    BTOPO_OT_end_retopo,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
