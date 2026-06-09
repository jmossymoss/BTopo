# BTopo: authoring tools for turning CAD tessellations into game-ready
# hard-surface meshes. See DESIGN.md for the full design.

bl_info = {
    "name": "BTopo",
    "author": "Jordan Moss",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > BTopo",
    "description": "CAD to game-ready hard-surface retopology tools",
    "category": "Mesh",
}

from . import (
    properties,
    ops_analyze,
    ops_cleanup,
    ops_retopo,
    ops_finalize,
    panels,
)

_modules = (
    properties,
    ops_analyze,
    ops_cleanup,
    ops_retopo,
    ops_finalize,
    panels,
)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
