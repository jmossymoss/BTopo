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


class VIEW3D_PT_btopo_retopo(BTopoPanelMixin, Panel):
    bl_label = "3. Retopo (Author-Over)"
    bl_parent_id = "VIEW3D_PT_btopo"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.btopo

        col = layout.column(align=True)
        col.prop(settings, "shrinkwrap_offset")
        col.prop(settings, "use_mirror")
        layout.operator("btopo.setup_retopo", icon='MOD_SHRINKWRAP')
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
