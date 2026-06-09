import math

import bpy
from bpy.props import BoolProperty, FloatProperty, PointerProperty
from bpy.types import PropertyGroup


class BTopoSettings(PropertyGroup):
    """Per-scene settings shared by the BTopo operators."""

    feature_angle: FloatProperty(
        name="Feature Angle",
        description=(
            "Edges whose faces meet at more than this angle are treated as "
            "feature edges and marked sharp"
        ),
        subtype='ANGLE',
        default=math.radians(30.0),
        min=0.0,
        max=math.pi,
    )

    mark_seams: BoolProperty(
        name="Also Mark Seams",
        description="Mark detected feature edges as UV seams as well as sharp",
        default=False,
    )

    merge_distance: FloatProperty(
        name="Merge Distance",
        description=(
            "Weld vertices closer than this distance (closes gaps along CAD "
            "patch seams)"
        ),
        subtype='DISTANCE',
        default=0.0001,
        min=0.0,
        precision=5,
    )

    dissolve_angle: FloatProperty(
        name="Dissolve Angle",
        description=(
            "Limited-dissolve angle for removing laddered tessellation on "
            "flat and gently curved regions. Detected feature edges are "
            "never dissolved"
        ),
        subtype='ANGLE',
        default=math.radians(1.0),
        min=0.0,
        max=math.radians(30.0),
    )

    quadify: BoolProperty(
        name="Tris to Quads",
        description="Join triangles into quads after dissolving, respecting feature edges",
        default=True,
    )

    shrinkwrap_offset: FloatProperty(
        name="Surface Offset",
        description="Distance the retopo mesh floats above the reference surface",
        subtype='DISTANCE',
        default=0.002,
        min=0.0,
        precision=4,
    )

    use_mirror: BoolProperty(
        name="Add Mirror Modifier",
        description="Add an X-axis mirror modifier to the new retopo object",
        default=False,
    )


def register():
    bpy.utils.register_class(BTopoSettings)
    bpy.types.Scene.btopo = PointerProperty(type=BTopoSettings)


def unregister():
    del bpy.types.Scene.btopo
    bpy.utils.unregister_class(BTopoSettings)
