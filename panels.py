import bpy
from bpy.types import Panel


class BTopoPanelMixin:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BTopo"


class VIEW3D_PT_btopo(BTopoPanelMixin, Panel):
    bl_label = "BTopo"

    def draw(self, context):
        pass


class VIEW3D_PT_btopo_analyze(BTopoPanelMixin, Panel):
    bl_label = "1. Analyze"
    bl_parent_id = "VIEW3D_PT_btopo"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.btopo

        col = layout.column(align=True)
        col.prop(settings, "feature_angle")
        col.prop(settings, "mark_seams")

        obj = context.active_object
        if obj is not None and obj.type == 'MESH' and "groups" in obj.data:
            box = layout.box()
            box.label(text="Plasticity bridge data found", icon='CHECKMARK')
            box.prop(settings, "use_plasticity")
            sub = box.row()
            sub.enabled = settings.use_plasticity
            sub.prop(settings, "plasticity_tangent")

        layout.operator("btopo.detect_features", icon='SHARPCURVE')

        layout.separator()
        layout.label(text="Select Issues (Edit Mode):")
        row = layout.row(align=True)
        row.operator("btopo.select_issues", text="Tris").issue_type = 'TRIS'
        row.operator("btopo.select_issues", text="N-gons").issue_type = 'NGONS'
        row = layout.row(align=True)
        row.operator("btopo.select_issues", text="Poles").issue_type = 'POLES'
        row.operator("btopo.select_issues",
                     text="Non-Manifold").issue_type = 'NON_MANIFOLD'


class VIEW3D_PT_btopo_cleanup(BTopoPanelMixin, Panel):
    bl_label = "2. Cleanup (In-Place)"
    bl_parent_id = "VIEW3D_PT_btopo"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.btopo

        col = layout.column(align=True)
        col.prop(settings, "merge_distance")
        col.prop(settings, "dissolve_angle")
        col.prop(settings, "quadify")
        layout.operator("btopo.cleanup_cad", icon='MOD_DECIM')

        layout.separator()
        layout.label(text="In-Place Rebuild (Edit Mode):")
        col = layout.column(align=True)
        col.operator("btopo.simplify_rails", icon='CURVE_DATA')
        col.operator("btopo.rebuild_patch", icon='MESH_GRID')
        layout.label(text="Regular strips (fast path):")
        col = layout.column(align=True)
        col.operator("btopo.simplify_strip", icon='MOD_DECIM')
        col.operator("btopo.set_strip_spans", icon='MOD_ARRAY')


class VIEW3D_PT_btopo_retopo(BTopoPanelMixin, Panel):
    bl_label = "3. Retopo (Author-Over)"
    bl_parent_id = "VIEW3D_PT_btopo"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.btopo

        col = layout.column(align=True)
        col.prop(settings, "use_mirror")
        col.prop(settings, "auto_trace")
        col.prop(settings, "lock_source")
        layout.operator("btopo.setup_retopo", icon='SNAP_FACE')

        layout.separator()
        col = layout.column(align=True)
        col.prop(settings, "trace_segment_angle")
        col.prop(settings, "trace_corner_angle")
        col.prop(settings, "trace_max_edge")
        layout.operator("btopo.trace_features", icon='CURVE_DATA')
        layout.operator("btopo.bridge_fill", icon='MOD_LATTICE')
        layout.operator("btopo.patch_fill", icon='MESH_GRID')

        layout.separator()
        layout.operator("btopo.end_retopo")


class VIEW3D_PT_btopo_finalize(BTopoPanelMixin, Panel):
    bl_label = "4. Finalize"
    bl_parent_id = "VIEW3D_PT_btopo"

    def draw(self, context):
        layout = self.layout
        layout.operator("btopo.finalize_shading", icon='SHADING_RENDERED')


_classes = (
    VIEW3D_PT_btopo,
    VIEW3D_PT_btopo_analyze,
    VIEW3D_PT_btopo_cleanup,
    VIEW3D_PT_btopo_retopo,
    VIEW3D_PT_btopo_finalize,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
