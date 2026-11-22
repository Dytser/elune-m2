"""Add-on preferences: every asset path the add-on reads from or writes to, in one place.

Edit > Preferences > Add-ons > Elune M2. The import dialog, the Outfit Textures panel and the
outfit asset library all read these; none of them keeps its own copy.
"""

import os

import bpy
from bpy.props import StringProperty
from bpy.types import AddonPreferences, Operator

ADDON_ID = __package__.rpartition(".")[0]


def _changed(self, context):
    """A path changed: forget the outfit lists built from the old folders."""
    try:
        from . import outfit_ui
        outfit_ui._on_path(self, context)
    except Exception:  # noqa: BLE001 - never break the preferences window
        pass


class M2Preferences(AddonPreferences):
    bl_idname = ADDON_ID

    game_path: StringProperty(
        name="Game Export Folder", subtype="DIR_PATH", update=_changed,
        description="The extracted game data (the folder that holds Character and Item). Model "
                    "textures not found next to the .m2 or above it are taken from here, as "
                    "are armour components (Item/TextureComponents) and race skins")
    listfile: StringProperty(
        name="Listfile", subtype="FILE_PATH",
        description="Optional community listfile (id;path) used to resolve FileDataID "
                    "textures on retail models to paths")
    components_path: StringProperty(
        name="Components Override", subtype="DIR_PATH", update=_changed,
        description="Optional: use this armour components folder instead of the one inside "
                    "the Game Export Folder (the one containing ArmUpperTexture, ...)")
    export_path: StringProperty(
        name="Outfit Export Folder", subtype="DIR_PATH",
        description="Where the outfit atlas and the re-split outfit textures are written")
    lib_path: StringProperty(
        name="Outfit Library Folder", subtype="DIR_PATH",
        description="Folder for the outfit asset library. Add it under Preferences > "
                    "File Paths > Asset Libraries")

    rig_file: StringProperty(
        name="Library Rig File", subtype="FILE_PATH",
        description="A .blend holding the camera rig and lighting for the outfit library scene "
                    "(a collection named 'Render'). Empty uses the one shipped with the "
                    "add-on (tools/AssetLibrary-Setup.blend)")

    def draw(self, context):
        col = self.layout.column()
        box = col.box()
        box.label(text="Game data", icon="FILE_FOLDER")
        box.prop(self, "game_path")
        box.prop(self, "listfile")
        box.label(text="Model import: texture next to the .m2 (and in folders above it) "
                       "first, then the Game Export Folder.", icon="INFO")
        box = col.box()
        box.label(text="Outfit textures", icon="MATERIAL")
        box.prop(self, "components_path")
        box.prop(self, "export_path")
        box.prop(self, "lib_path")
        box.prop(self, "rig_file")


def get():
    """The add-on's preferences (a default-valued stand-in if it isn't registered yet)."""
    try:
        return bpy.context.preferences.addons[ADDON_ID].preferences
    except (KeyError, AttributeError):
        class _Blank:
            game_path = listfile = components_path = export_path = lib_path = rig_file = ""
        return _Blank()


def path(name):
    """A preference path as an absolute path ('' when unset)."""
    v = getattr(get(), name, "")
    return bpy.path.abspath(v) if v else ""


class M2_OT_open_preferences(Operator):
    bl_idname = "m2.open_preferences"
    bl_label = "Asset Paths (Add-on Preferences)"
    bl_description = "Open the Elune M2 add-on preferences, where the asset folders are set"

    def execute(self, context):
        bpy.ops.preferences.addon_show(module=ADDON_ID)
        return {"FINISHED"}


_classes = (M2Preferences, M2_OT_open_preferences)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
