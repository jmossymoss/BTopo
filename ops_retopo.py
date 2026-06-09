import bpy
from bpy.types import Operator


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
    BTOPO_OT_end_retopo,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
