"""Add-on preferences: every asset path the add-on reads from or writes to, in one place.

Edit > Preferences > Add-ons > Elune M2.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import AddonPreferences

ADDON_ID = __package__.rpartition(".")[0]


class M2Preferences(AddonPreferences):
    bl_idname = ADDON_ID

    listfile: StringProperty(
        name="Listfile", subtype="FILE_PATH",
        description="Optional community listfile (id;path) used to resolve FileDataID "
                    "textures on retail models to paths")

    def draw(self, context):
        from . import listfile_update
        col = self.layout.column()
        box = col.box()
        box.label(text="Game data", icon="FILE_FOLDER")
        box.prop(self, "listfile")
        listfile_update.draw(box, self)


def get():
    """The add-on's preferences (a default-valued stand-in if it isn't registered yet)."""
    try:
        return bpy.context.preferences.addons[ADDON_ID].preferences
    except (KeyError, AttributeError):
        class _Blank:
            listfile = ""
        return _Blank()


def path(name):
    """A preference path as an absolute path ('' when unset)."""
    v = getattr(get(), name, "")
    return bpy.path.abspath(v) if v else ""


_classes = (M2Preferences,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
