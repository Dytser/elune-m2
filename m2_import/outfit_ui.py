"""Outfit Textures panel: merge armour components into one paintable atlas and split it back out."""

import os

import bpy
import numpy as np
from bpy.props import (BoolProperty, EnumProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

from . import outfit

ATLAS_NAME = "M2_Outfit_Atlas"
ATLAS_FILE = "outfit_atlas.png"


def _abs(path):
    return bpy.path.abspath(path) if path else ""


def _slot_search(key):
    def search(self, context, edit_text):
        root = _abs(self.components_path)
        names = outfit.component_names(root, key) if root else []
        q = edit_text.lower()
        return [n for n in names if q in n.lower()][:500]
    return search


def _slot_prop(key, label):
    return StringProperty(
        name=label,
        description="Armour texture for the %s region. Type to search the "
                    "components folder; leave empty to skip this region" % label.lower(),
        search=_slot_search(key), search_options={"SORT"},
    )


def _on_path(self, context):
    outfit.clear_cache()


class M2OutfitProps(PropertyGroup):
    components_path: StringProperty(
        name="Components", subtype="DIR_PATH", update=_on_path,
        description="Folder holding the extracted armour components "
                    "(the one containing ArmUpperTexture, TorsoUpperTexture, ...)")
    export_path: StringProperty(
        name="Export Path", subtype="DIR_PATH",
        description="Where the atlas and the re-split outfit textures are written")
    gender: EnumProperty(
        name="Gender", default="m",
        items=[("m", "Male", "Prefer _m components, then unisex"),
               ("f", "Female", "Prefer _f components, then unisex")])
    atlas_size: EnumProperty(
        name="Atlas Size", default="512",
        items=[("256", "256", "Low resolution, matches 128-wide components"),
               ("512", "512", "Standard, matches 256-wide components"),
               ("1024", "1024", "Extra detail for painting"),
               ("2048", "2048", "Very large")])
    name_suffix: StringProperty(
        name="Name Suffix", default="",
        description="Appended to each exported file name (before _m/_f/_u), "
                    "e.g. _custom. Empty keeps the original names")
    native_size: BoolProperty(
        name="Original Component Size", default=False,
        description="Scale each region back down to the size of the component it "
                    "came from instead of keeping the atlas resolution")
    write_blp: BoolProperty(name="BLP", default=True)
    write_png: BoolProperty(name="PNG", default=True)

    __annotations__.update({k: _slot_prop(k, label) for k, label, *_ in outfit.REGIONS})


def _slots(p):
    return {k: getattr(p, k) for k, *_ in outfit.REGIONS}


def _atlas_dir(p):
    return _abs(p.export_path)


def _get_atlas():
    return bpy.data.images.get(ATLAS_NAME)


def _read_atlas(img):
    w, h = img.size
    buf = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(buf)
    arr = np.rint(np.clip(buf, 0, 1).reshape(h, w, 4) * 255).astype(np.uint8)
    return np.ascontiguousarray(arr[::-1])


def _write_atlas(rgba, size):
    img = _get_atlas()
    if img is not None and tuple(img.size) != (size, size):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(ATLAS_NAME, size, size, alpha=True)
        img.alpha_mode = "STRAIGHT"
    flat = (rgba[::-1].astype(np.float32) / 255.0).reshape(-1)
    img.pixels.foreach_set(flat)
    img.update()
    return img


def _save_atlas(img, folder):
    """Write the atlas PNG into ``folder`` and make the image file-backed."""
    os.makedirs(folder, exist_ok=True)
    img.filepath_raw = os.path.join(folder, ATLAS_FILE)
    img.file_format = "PNG"
    img.save()


class M2_OT_outfit_build(Operator):
    bl_idname = "m2.outfit_build"
    bl_label = "Build Outfit Atlas"
    bl_description = ("Merge the selected armour components onto one body-texture atlas "
                      "you can paint on. Replaces the current atlas image")

    def invoke(self, context, event):
        img = _get_atlas()
        if img is not None and img.is_dirty:
            return context.window_manager.invoke_confirm(
                self, event, message="The atlas has unsaved paint. Rebuild and discard it?")
        return self.execute(context)

    def execute(self, context):
        p = context.scene.m2_outfit
        root = _abs(p.components_path)
        if not os.path.isdir(root):
            self.report({"ERROR"}, "Set the Components folder first.")
            return {"CANCELLED"}
        slots = {k: v for k, v in _slots(p).items() if v}
        if not slots:
            self.report({"ERROR"}, "Pick at least one armour component.")
            return {"CANCELLED"}
        size = int(p.atlas_size)
        msgs = []
        rgba, used = outfit.build_atlas(root, slots, size, p.gender, log=msgs.append)
        img = _write_atlas(rgba, size)
        folder = _atlas_dir(p)
        if folder:
            try:
                _save_atlas(img, folder)
            except (OSError, RuntimeError) as exc:
                msgs.append("could not save atlas: %s" % exc)
        else:
            img.pack()
        for m in msgs:
            print("[Elune M2] outfit:", m)
        self.report({"WARNING" if msgs else "INFO"},
                    "Atlas built from %d region(s)%s" % (
                        len(used), "; %d problem(s), see console" % len(msgs) if msgs else "."))
        return {"FINISHED"}


class M2_OT_outfit_assign(Operator):
    bl_idname = "m2.outfit_assign"
    bl_label = "Use Atlas on Active Material"
    bl_description = "Wire the outfit atlas into the active material's Base Color so it shows (and paints) on the model"

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return _get_atlas() is not None and ob is not None and ob.active_material is not None

    def execute(self, context):
        mat = context.active_object.active_material
        mat.use_nodes = True
        nt = mat.node_tree
        node = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.label == "Outfit Atlas"), None)
        if node is None:
            node = nt.nodes.new("ShaderNodeTexImage")
            node.label = "Outfit Atlas"
            node.location = (-420, 0)
        node.image = _get_atlas()
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
            out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) \
                or nt.nodes.new("ShaderNodeOutputMaterial")
            nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
        nt.nodes.active = node
        self.report({"INFO"}, "Atlas assigned to '%s'." % mat.name)
        return {"FINISHED"}


class M2_OT_outfit_export(Operator):
    bl_idname = "m2.outfit_export"
    bl_label = "Export Outfit Textures"
    bl_description = "Split the edited atlas back into its regions and save them as PNG/BLP"

    @classmethod
    def poll(cls, context):
        return _get_atlas() is not None

    def execute(self, context):
        p = context.scene.m2_outfit
        root = _abs(p.components_path)
        out_dir = _abs(p.export_path)
        if not out_dir:
            self.report({"ERROR"}, "Set the Export Path first.")
            return {"CANCELLED"}
        if not (p.write_blp or p.write_png):
            self.report({"ERROR"}, "Enable BLP and/or PNG output.")
            return {"CANCELLED"}
        slots = {k: v for k, v in _slots(p).items() if v}
        if not slots:
            self.report({"ERROR"}, "No armour components selected.")
            return {"CANCELLED"}
        img = _get_atlas()
        rgba = _read_atlas(img)
        msgs = []
        try:
            written = outfit.split_atlas(
                rgba, root, slots, out_dir, p.gender, suffix=p.name_suffix,
                native_size=p.native_size, write_png=p.write_png,
                write_blp=p.write_blp, log=msgs.append)
            _save_atlas(img, out_dir)
        except OSError as exc:
            self.report({"ERROR"}, "Export failed: %s" % exc)
            return {"CANCELLED"}
        for m in msgs:
            print("[Elune M2] outfit:", m)
        self.report({"INFO"}, "Wrote %d file(s) to %s" % (len(written), out_dir))
        return {"FINISHED"}


class VIEW3D_PT_m2_outfit(Panel):
    bl_idname = "VIEW3D_PT_m2_outfit"
    bl_label = "M2 Outfit Textures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "M2"

    def draw(self, context):
        layout = self.layout
        p = context.scene.m2_outfit
        col = layout.column(align=True)
        col.prop(p, "components_path")
        col.prop(p, "export_path")
        row = layout.row(align=True)
        row.prop(p, "gender", expand=True)
        row.prop(p, "atlas_size", text="")

        box = layout.box()
        box.label(text="Armour components", icon="MATCLOTH")
        col = box.column(align=True)
        for key, label, *_ in outfit.REGIONS:
            col.prop(p, key, text=label)

        layout.operator("m2.outfit_build", icon="TEXTURE")
        layout.operator("m2.outfit_assign", icon="MATERIAL")

        box = layout.box()
        box.label(text="Export", icon="EXPORT")
        row = box.row(align=True)
        row.prop(p, "write_blp", toggle=True)
        row.prop(p, "write_png", toggle=True)
        box.prop(p, "name_suffix")
        box.prop(p, "native_size")
        row = box.row()
        row.scale_y = 1.4
        row.operator("m2.outfit_export", icon="EXPORT")


_classes = (
    M2OutfitProps,
    M2_OT_outfit_build,
    M2_OT_outfit_assign,
    M2_OT_outfit_export,
    VIEW3D_PT_m2_outfit,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.m2_outfit = PointerProperty(type=M2OutfitProps)


def unregister():
    del bpy.types.Scene.m2_outfit
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
