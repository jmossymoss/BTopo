import bpy
from bpy.types import Operator

from .ops_analyze import detect_features


class BTOPO_OT_finalize_shading(Operator):
    """Apply the standard game hard-surface shading recipe.

    Smooth shading everywhere, sharp edges at features, and a keep-sharp
    Weighted Normal modifier so large faces dominate the vertex normals.
    """

    bl_idname = "btopo.finalize_shading"
    bl_label = "Finalize Shading"
    bl_description = (
        "Shade smooth, mark feature edges sharp, and add a Weighted Normal "
        "modifier"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'OBJECT'

    def execute(self, context):
        obj = context.active_object
        settings = context.scene.btopo

        detect_features(obj, settings.feature_angle,
                        mark_seams=settings.mark_seams)

        for polygon in obj.data.polygons:
            polygon.use_smooth = True
        obj.data.update()

        modifier = next(
            (m for m in obj.modifiers if m.type == 'WEIGHTED_NORMAL'), None)
        if modifier is None:
            modifier = obj.modifiers.new("BTopo Weighted Normals",
                                         'WEIGHTED_NORMAL')
        modifier.mode = 'FACE_AREA'
        modifier.keep_sharp = True
        modifier.weight = 50

        self.report({'INFO'}, "Shading finalized (smooth + sharps + weighted normals)")
        return {'FINISHED'}


_classes = (BTOPO_OT_finalize_shading,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
