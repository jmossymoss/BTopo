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

    use_plasticity: BoolProperty(
        name="Use Plasticity Face Groups",
        description=(
            "When the mesh came through the Plasticity Blender bridge, use "
            "its CAD face groups for exact feature detection instead of "
            "relying on the face angle alone"
        ),
        default=True,
    )

    plasticity_tangent: BoolProperty(
        name="Tangent Boundaries",
        description=(
            "Also treat smooth CAD face boundaries (fillet transitions) as "
            "feature edges — invisible to an angle test, but exactly the "
            "rails the in-place strip tools and tracing work along"
        ),
        default=True,
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

    use_mirror: BoolProperty(
        name="Add Mirror Modifier",
        description="Add an X-axis mirror modifier to the new retopo object",
        default=False,
    )

    trace_segment_angle: FloatProperty(
        name="Segment Angle",
        description=(
            "Curvature per segment when resampling traced feature curves; "
            "lower values keep more segments on curved features "
            "(30° turns a circle into a 12-gon)"
        ),
        subtype='ANGLE',
        default=math.radians(30.0),
        min=math.radians(1.0),
        max=math.radians(120.0),
    )

    trace_corner_angle: FloatProperty(
        name="Corner Angle",
        description=(
            "Feature curves turning more sharply than this are split at a "
            "hard corner that always keeps a vertex"
        ),
        subtype='ANGLE',
        default=math.radians(45.0),
        min=math.radians(5.0),
        max=math.pi,
    )

    trace_max_edge: FloatProperty(
        name="Max Edge Length",
        description=(
            "Subdivide traced edges longer than this so long straight rails "
            "keep enough vertices to fill between (0 = unlimited)"
        ),
        subtype='DISTANCE',
        default=0.0,
        min=0.0,
    )


def register():
    bpy.utils.register_class(BTopoSettings)
    bpy.types.Scene.btopo = PointerProperty(type=BTopoSettings)


def unregister():
    del bpy.types.Scene.btopo
    bpy.utils.unregister_class(BTopoSettings)
