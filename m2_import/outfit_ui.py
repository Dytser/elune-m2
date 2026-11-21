"""Outfit Textures panel: pick armor (Type > Armor > Colour), merge it into one paintable atlas, split it back out."""

import os

import bpy
import numpy as np
from bpy.props import (BoolProperty, EnumProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

from . import outfit, outfit_index

ATLAS_NAME = "M2_Outfit_Atlas"
ATLAS_FILE = "outfit_atlas.png"


def _abs(path):
    return bpy.path.abspath(path) if path else ""


# ---------------------------------------------------------------------------
# Armor browser: Type -> Armor -> Colour
# ---------------------------------------------------------------------------
_items = {}     # enum item lists must stay referenced or Blender shows garbage
_busy = [False]


def _on_path(self, context):
    outfit.clear_cache()
    outfit_index.clear_cache()
    _items.clear()


def _index(p):
    root = _abs(p.components_path)
    return outfit_index.get_index(root) if os.path.isdir(root) else None


def _cached(key, build):
    if key not in _items:
        _items[key] = build()
    return _items[key]


def _type_items(self, context):
    root = _abs(self.components_path)

    def build():
        idx = _index(self)
        items = [(k, label, tip) for k, label, tip in idx.types()] if idx else []
        return items or [("NONE", "Set the Components folder", "")]
    return _cached(("type", root), build)


def _armor_items(self, context):
    root, kind = _abs(self.components_path), self.armor_type

    def build():
        idx = _index(self)
        out = []
        for key, label in (idx.armors(kind) if idx else []):
            n = len(idx.colors(kind, key))
            out.append((key, "%s  (%d colours)" % (label, n) if n > 1 else label, key))
        return out or [("NONE", "(none)", "")]
    return _cached(("armor", root, kind), build)


def _color_items(self, context):
    root, kind, key = _abs(self.components_path), self.armor_type, self.armor

    def build():
        idx = _index(self)
        out = [(c or outfit_index.DEFAULT_COLOR,
                c.replace("_", " ").title() if c else "Default", "")
               for c in (idx.colors(kind, key) if idx else [])]
        return out or [("NONE", "(none)", "")]
    return _cached(("color", root, kind, key), build)


def _first(items_fn, owner):
    return items_fn(owner, bpy.context)[0][0]


def _apply(p):
    """Fill the ticked regions from the chosen type / armor / colour."""
    idx = _index(p)
    if idx is None:
        return
    color = "" if p.color == outfit_index.DEFAULT_COLOR else p.color
    found = idx.regions(p.armor_type, p.armor, color)
    for key, *_ in outfit.REGIONS:
        if getattr(p, "use_" + key):
            setattr(p, key, found.get(key, ""))


def _on_type(self, context):
    if _busy[0]:
        return
    _busy[0] = True
    try:
        self.armor = _first(_armor_items, self)
        self.color = _first(_color_items, self)
    finally:
        _busy[0] = False
    _apply(self)


def _on_armor(self, context):
    if _busy[0]:
        return
    _busy[0] = True
    try:
        self.color = _first(_color_items, self)
    finally:
        _busy[0] = False
    _apply(self)


def _on_color(self, context):
    if not _busy[0]:
        _apply(self)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
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
        description="Armour texture for the %s region. Filled by the armor picker; "
                    "type here to override it by hand, or clear it to skip the region"
                    % label.lower(),
        search=_slot_search(key), search_options={"SORT"},
    )


class M2OutfitProps(PropertyGroup):
    components_path: StringProperty(
        name="Components", subtype="DIR_PATH", update=_on_path,
        description="Folder holding the extracted armour components "
                    "(the one containing ArmUpperTexture, TorsoUpperTexture, ...)")
    export_path: StringProperty(
        name="Export Path", subtype="DIR_PATH",
        description="Where the atlas and the re-split outfit textures are written")
    armor_type: EnumProperty(
        name="Type", items=_type_items, update=_on_type,
        description="Armor class: cloth, leather, mail, plate, ...")
    armor: EnumProperty(
        name="Armor", items=_armor_items, update=_on_armor,
        description="An armor set. Picking one fills the ticked regions with its pieces")
    color: EnumProperty(
        name="Colour", items=_color_items, update=_on_color,
        description="Colour variation of the chosen armor")
    gender: EnumProperty(
        name="Gender", default="m",
        items=[("m", "Male", "Prefer _m components, then unisex"),
               ("f", "Female", "Prefer _f components, then unisex")])
    atlas_size: EnumProperty(
        name="Atlas Size", default="1024",
        description="Skin texture size. The left half is the body (painted); "
                    "the right half is the face and is left empty",
        items=[("512", "512 x 256", "Low resolution, matches 128-wide components"),
               ("1024", "1024 x 512", "Standard skin size, matches 256-wide components"),
               ("2048", "2048 x 1024", "Extra detail for painting"),
               ("4096", "4096 x 2048", "Very large")])
    skin_path: StringProperty(
        name="Skin", subtype="FILE_PATH",
        description="Optional body skin (PNG/BLP) shown underneath the outfit on the model "
                    "as a painting reference. Never exported")
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
    __annotations__.update({
        "use_" + k: BoolProperty(
            name=label, default=True,
            description="Picking an armor fills the %s region" % label.lower())
        for k, label, *_ in outfit.REGIONS})


def _slots(p):
    return {k: getattr(p, k) for k, *_ in outfit.REGIONS}


# ---------------------------------------------------------------------------
# Atlas image
# ---------------------------------------------------------------------------
def _get_atlas():
    return bpy.data.images.get(ATLAS_NAME)


def _read_atlas(img):
    w, h = img.size
    buf = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(buf)
    arr = np.rint(np.clip(buf, 0, 1).reshape(h, w, 4) * 255).astype(np.uint8)
    return np.ascontiguousarray(arr[::-1])


def _write_atlas(rgba):
    h, size = rgba.shape[:2]
    img = _get_atlas()
    if img is not None and tuple(img.size) != (size, h):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(ATLAS_NAME, size, h, alpha=True)
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


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
class M2_OT_outfit_rescan(Operator):
    bl_idname = "m2.outfit_rescan"
    bl_label = "Rescan Components"
    bl_description = "Re-read the components folder (after adding or removing files)"

    def execute(self, context):
        _on_path(context.scene.m2_outfit, context)
        return {"FINISHED"}


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
            self.report({"ERROR"}, "Pick an armor (or at least one region) first.")
            return {"CANCELLED"}
        size = int(p.atlas_size)
        msgs = []
        rgba, used = outfit.build_atlas(root, slots, size, p.gender, log=msgs.append)
        img = _write_atlas(rgba)
        folder = _abs(p.export_path)
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

    @staticmethod
    def _skin_image(p):
        path = _abs(p.skin_path)
        if not os.path.isfile(path):
            return None
        try:
            if path.lower().endswith(".blp"):
                png = os.path.join(bpy.app.tempdir, os.path.basename(path) + ".png")
                outfit.textures.blp_to_png(path, png)
                path = png
            return bpy.data.images.load(path, check_existing=True)
        except (OSError, RuntimeError, outfit.textures.BLPError):
            return None

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
        color = node.outputs["Color"]
        skin = self._skin_image(context.scene.m2_outfit)
        for n in [n for n in nt.nodes if n.label in ("Outfit Skin", "Outfit Over Skin")]:
            nt.nodes.remove(n)
        if skin is not None:
            sn = nt.nodes.new("ShaderNodeTexImage")
            sn.label = "Outfit Skin"
            sn.image = skin
            sn.location = (-420, 300)
            mix = nt.nodes.new("ShaderNodeMix")
            mix.label = "Outfit Over Skin"
            mix.data_type = "RGBA"
            mix.location = (-160, 150)
            nt.links.new(node.outputs["Alpha"], mix.inputs[0])
            nt.links.new(sn.outputs["Color"], mix.inputs[6])
            nt.links.new(node.outputs["Color"], mix.inputs[7])
            color = mix.outputs[2]
        nt.links.new(color, bsdf.inputs["Base Color"])
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


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------
class _OutfitPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "M2"


class VIEW3D_PT_m2_outfit(_OutfitPanel, Panel):
    bl_idname = "VIEW3D_PT_m2_outfit"
    bl_label = "M2 Outfit Textures"

    def draw(self, context):
        layout = self.layout
        p = context.scene.m2_outfit
        col = layout.column(align=True)
        col.prop(p, "components_path")
        col.prop(p, "export_path")
        col.prop(p, "skin_path")
        row = layout.row(align=True)
        row.prop(p, "gender", expand=True)
        row.prop(p, "atlas_size", text="")

        box = layout.box()
        row = box.row()
        row.label(text="Armor", icon="MATCLOTH")
        row.operator("m2.outfit_rescan", text="", icon="FILE_REFRESH")
        box.prop(p, "armor_type")
        box.prop(p, "armor")
        box.prop(p, "color")
        box.label(text="Fills the regions ticked below", icon="INFO")


class VIEW3D_PT_m2_outfit_regions(_OutfitPanel, Panel):
    bl_idname = "VIEW3D_PT_m2_outfit_regions"
    bl_label = "Regions"
    bl_parent_id = "VIEW3D_PT_m2_outfit"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = context.scene.m2_outfit
        col = self.layout.column(align=True)
        for key, label, *_ in outfit.REGIONS:
            row = col.row(align=True)
            row.prop(p, "use_" + key, text="")
            row.prop(p, key, text=label)


class VIEW3D_PT_m2_outfit_atlas(_OutfitPanel, Panel):
    bl_idname = "VIEW3D_PT_m2_outfit_atlas"
    bl_label = "Atlas"
    bl_parent_id = "VIEW3D_PT_m2_outfit"

    def draw(self, context):
        layout = self.layout
        layout.operator("m2.outfit_build", icon="TEXTURE")
        layout.operator("m2.outfit_assign", icon="MATERIAL")


class VIEW3D_PT_m2_outfit_export(_OutfitPanel, Panel):
    bl_idname = "VIEW3D_PT_m2_outfit_export"
    bl_label = "Export"
    bl_parent_id = "VIEW3D_PT_m2_outfit"

    def draw(self, context):
        p = context.scene.m2_outfit
        box = self.layout
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
    M2_OT_outfit_rescan,
    M2_OT_outfit_build,
    M2_OT_outfit_assign,
    M2_OT_outfit_export,
    VIEW3D_PT_m2_outfit,
    VIEW3D_PT_m2_outfit_regions,
    VIEW3D_PT_m2_outfit_atlas,
    VIEW3D_PT_m2_outfit_export,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.m2_outfit = PointerProperty(type=M2OutfitProps)


def unregister():
    del bpy.types.Scene.m2_outfit
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
