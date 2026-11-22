"""Outfit Textures panel: pick armor (Type > Source > Expansion > Armor > Colour, or search), merge it into one paintable atlas, split it back out."""

import os

import bpy
import numpy as np
from bpy.props import (BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

from . import outfit, outfit_index, prefs

ATLAS_NAME = "M2_Outfit_Atlas"
ATLAS_FILE = "outfit_atlas.png"


def _abs(path):
    return bpy.path.abspath(path) if path else ""


def _find_dir(root, *parts):
    """``root``/part/part... matched case-insensitively, or '' if any step is missing."""
    cur = root
    for part in parts:
        if not (cur and os.path.isdir(cur)):
            return ""
        hit = outfit._listing(cur).get(part.lower())
        cur = os.path.join(cur, hit) if hit else ""
    return cur if cur and os.path.isdir(cur) else ""


def _components(p):
    """The armour components folder: the explicit override, else Item/TextureComponents of the
    game export folder."""
    explicit = prefs.path("components_path")
    if explicit:
        return explicit
    return _find_dir(prefs.path("game_path"), "Item", "TextureComponents")


def skin_tones(p):
    """{tone number: file} of the loaded race's body skins in the game export folder."""
    key = p.skin_key
    game = prefs.path("game_path")
    if not (key and game):
        return {}
    race, gender = key.split("/")
    folder = _find_dir(game, "Character", race, gender)
    if not folder:
        return {}
    import re
    fam = (race + gender).lower()
    pat = re.compile(r"skin00_(\d+)(?:_[a-z0-9]+)*$")      # Skin00_08, Skin00_08_hd
    out = {}
    for low, name in outfit._listing(folder).items():
        base, ext = os.path.splitext(low)
        m = pat.search(base)
        if (not m or ext not in (".png", ".blp") or "naked" in base or "extra" in base
                or not base.replace("_", "").startswith(fam)):
            continue
        n = int(m.group(1))
        if n not in out or ext == ".png":
            out[n] = os.path.join(folder, name)
    return out


def skin_file(p):
    """The skin texture to lay the outfit over: the chosen skin tone of the loaded race."""
    tones = skin_tones(p)
    if not tones:
        return ""
    return tones.get(p.skin_tone) or tones[min(tones)]


def race_gender(root, game):
    """('nightelf', 'female') as the game folders spell them, for an imported character."""
    import re
    objs = [root, *_all_children(root)]
    for o in objs:
        for slot in getattr(o, "material_slots", ()):
            mat = slot.material
            fn = str(mat.get("m2_texture_filename", "")) if mat else ""
            m = re.search(r"character[\\/]+([^\\/]+)[\\/]+(male|female)[\\/]", fn, re.I)
            if m:
                return m.group(1), m.group(2).capitalize()
    # fall back on the object name ("nightelffemale") against the Character folder's races
    chars = _find_dir(game, "Character")
    name = root.name.lower().replace("_", "")
    gender = "Female" if "female" in name else "Male" if "male" in name else ""
    races = [d for d in (os.listdir(chars) if chars else []) if d.lower() in name]
    if gender and races:
        return max(races, key=len), gender
    return "", ""


def _all_children(ob):
    for c in ob.children:
        yield c
        yield from _all_children(c)


def _on_skin(self, context):
    """A new skin tone: swap the image under the outfit on the body material."""
    body = self.lib_body
    mat = body.active_material if body else None
    path = skin_file(self)
    if mat is None or not mat.node_tree or not path:
        return
    img = skin_image(self)
    node = next((n for n in mat.node_tree.nodes if n.label == "Outfit Skin"), None)
    if node is not None and img is not None:
        node.image = img


# ---------------------------------------------------------------------------
# Armor browser: Type -> Source -> Expansion -> Armor -> Colour (plus a search box)
# ---------------------------------------------------------------------------
_items = {}     # enum item lists must stay referenced or Blender shows garbage
_busy = [False]
_NONE = [("NONE", "(none)", "")]


def _on_path(self, context):
    outfit.clear_cache()
    outfit_index.clear_cache()
    _items.clear()


def _roots(p):
    """Texture folders in priority order: the mod (if set) over the base components."""
    base, mod = _components(p), _abs(p.mod_path)
    mod = mod if mod and os.path.isdir(mod) else ""
    return tuple(r for r in (mod, base) if r and os.path.isdir(r))


def _index(p):
    base, mod = _components(p), _abs(p.mod_path)
    base = base if os.path.isdir(base) else ""
    mod = mod if os.path.isdir(mod) else ""
    if not (base or mod):
        return None
    idx = outfit_index.get_index(base, mod)
    idx.only_mod = bool(p.mod_only and mod)
    return idx


def _cached(key, build):
    if key not in _items:
        _items[key] = build() or _NONE
    return _items[key]


def _kind_items(self, context):
    root = _components(self)
    return _cached(("kind", root), lambda: (
        _index(self).kinds() if _index(self) else [("NONE", "Set the Components folder", "")]))


def _source_items(self, context):
    root, kind = _components(self), self.armor_type
    return _cached(("source", root, kind), lambda: _index(self).sources(kind) if _index(self) else [])


def _tier_items(self, context):
    root, kind, src = _components(self), self.armor_type, self.source
    return _cached(("tier", root, kind, src), lambda: _index(self).tiers(src, kind) if _index(self) else [])


def _armor_items(self, context):
    root, kind, src, tier = _components(self), self.armor_type, self.source, self.tier
    return _cached(("armor", root, kind, src, tier),
                   lambda: _index(self).armors(src, tier, kind) if _index(self) else [])


def _color_items(self, context):
    root, src, tier, armor = _components(self), self.source, self.tier, self.armor
    return _cached(("color", root, src, tier, armor),
                   lambda: _index(self).colors(src, tier, armor) if _index(self) else [])


def _first(items_fn, owner):
    return items_fn(owner, bpy.context)[0][0]


def _apply(p, force=False):
    """Fill the ticked regions from the chosen armor."""
    idx = _index(p)
    if idx is None:
        return
    found = idx.regions(p.color)
    was, _busy[0] = _busy[0], True        # one rebuild at the end, not one per region field
    try:
        for key, *_ in outfit.REGIONS:
            if getattr(p, "use_" + key):
                setattr(p, key, found.get(key, ""))
    finally:
        _busy[0] = was
    _autobuild(p, force)


def select_outfit(p, gid, gender=None):
    """Point the browser at outfit ``gid`` and build its atlas, locked or not. False if unknown."""
    idx = _index(p)
    g = idx.groups.get(gid) if idx else None
    if g is None:
        return False
    was, _busy[0] = _busy[0], True
    try:
        if gender in ("m", "f"):
            p.gender = gender
        p.armor_type, p.source, p.tier = g["kind"], g["source"], g["tier"]
        p.armor, p.color = g["gkey"], gid
    except TypeError:       # not listed under the current filters (e.g. Mod Outfits Only)
        return False
    finally:
        _busy[0] = was
    _apply(p, force=True)
    return True


def _autobuild(p, force=False):
    """Rebuild the atlas whenever the armor / regions / gender / size change. Unsaved paint on
    the atlas is never overwritten unless ``force`` (the Discard Paint & Rebuild button)."""
    if _busy[0] or (atlas_has_paint() and not force):
        return
    root = _roots(p)
    slots = {k: v for k, v in _slots(p).items() if v}
    if not (root and slots):
        return
    msgs = []
    rgba, used = outfit.build_atlas(root, slots, int(p.atlas_size), p.gender, log=msgs.append)
    img = _write_atlas(rgba)
    folder = prefs.path("export_path")
    if folder:
        try:
            _save_atlas(img, folder)
        except (OSError, RuntimeError) as exc:
            msgs.append("could not save atlas: %s" % exc)
    else:
        img.pack()
    for m in msgs:
        print("[Elune M2] outfit:", m)


def _on_rebuild(self, context):
    _autobuild(self)


def _cascade(p, *levels):
    """Reset each lower level to its first entry, then refill the regions."""
    if _busy[0]:
        return
    _busy[0] = True
    try:
        fns = {"source": _source_items, "tier": _tier_items, "armor": _armor_items,
               "color": _color_items}
        for name in levels:
            setattr(p, name, _first(fns[name], p))
    finally:
        _busy[0] = False
    _apply(p)


def _on_kind(self, context):
    _cascade(self, "source", "tier", "armor", "color")


def _on_source(self, context):
    _cascade(self, "tier", "armor", "color")


def _on_tier(self, context):
    _cascade(self, "armor", "color")


def _on_armor(self, context):
    _cascade(self, "color")


def _on_color(self, context):
    if not _busy[0]:
        _apply(self)


_found = {}     # search result text -> armor id


def _find_search(self, context, edit_text):
    idx = _index(self)
    hits = idx.search(edit_text, self.armor_type, self.source, self.tier) if idx and edit_text.strip() else []
    _found.clear()
    _found.update({text: gid for gid, text in hits})
    return [text for _, text in hits]


def _on_find(self, context):
    gid = _found.get(self.find)
    if _busy[0] or not gid:
        return
    idx = _index(self)
    if idx is None or gid not in idx.groups:
        return
    _busy[0] = True
    try:
        self.armor = idx.groups[gid]["gkey"]       # results already respect the filters above
        self.color = gid
        self.find = ""
    finally:
        _busy[0] = False
    _apply(self)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _slot_search(key):
    def search(self, context, edit_text):
        root = _roots(self)
        names = outfit.component_names(root, key) if root else []
        idx = _index(self)
        return (idx.match_bases(names, edit_text) if idx else
                [n for n in names if edit_text.lower() in n.lower()])[:500]
    return search


def _slot_prop(key, label):
    return StringProperty(
        name=label,
        description="Armour texture for the %s region. Filled by the armor picker; "
                    "type here to override it by hand, or clear it to skip the region"
                    % label.lower(),
        search=_slot_search(key), search_options={"SORT"}, update=_on_rebuild,
    )


class M2OutfitProps(PropertyGroup):
    skin_key: StringProperty(
        name="Race", default="",
        description="Race/gender folder of the character the library was set up for")
    skin_tone: IntProperty(
        name="Skin Tone", default=0, min=0, max=99, update=_on_skin,
        description="Which of the race's body skins (Skin00_NN) to lay the outfit over")
    mod_path: StringProperty(
        name="Mod Folder", subtype="DIR_PATH", update=_on_path,
        description="Optional folder of edited textures (same layout as the Components folder). "
                    "Its files replace the Components ones of the same name; everything it "
                    "lacks is taken from the Components folder")
    mod_only: BoolProperty(
        name="Mod Outfits Only", default=False, update=_on_path,
        description="Only list outfits that have at least one texture in the mod folder")
    armor_type: EnumProperty(
        name="Type", items=_kind_items, update=_on_kind,
        description="Filter by armor class: cloth, leather, mail, plate, ...")
    color: EnumProperty(
        name="Colour", items=_color_items, update=_on_color,
        description="Colour variant of the chosen armor (each is named after its own item)")
    source: EnumProperty(
        name="Source", items=_source_items, update=_on_source,
        description="Where the armor comes from: raid, dungeon, other sets, ...")
    tier: EnumProperty(
        name="Expansion", items=_tier_items, update=_on_tier,
        description="Expansion the armor belongs to")
    find: StringProperty(
        name="Search", update=_on_find, search=_find_search, search_options={"SORT"},
        description="Search every armor by set, item, instance, expansion or type "
                    "(e.g. 'plate sunwell'). Picking a result selects it below")
    armor: EnumProperty(
        name="Armor", items=_armor_items, update=_on_armor,
        description="An armor set. Picking one fills the ticked regions with its pieces")
    gender: EnumProperty(
        name="Gender", default="f", update=_on_rebuild,
        items=[("m", "Male", "Prefer _m components, then unisex"),
               ("f", "Female", "Prefer _f components, then unisex")])
    atlas_size: EnumProperty(
        name="Atlas Size", default="1024", update=_on_rebuild,
        description="Skin texture size. The left half is the body (painted); "
                    "the right half is the face and is left empty",
        items=[("512", "512 x 256", "Low resolution, matches 128-wide components"),
               ("1024", "1024 x 512", "Standard skin size, matches 256-wide components"),
               ("2048", "2048 x 1024", "Extra detail for painting"),
               ("4096", "4096 x 2048", "Very large")])
    lib_body: PointerProperty(
        name="Model", type=bpy.types.Object, poll=lambda self, ob: ob.type == "MESH",
        description="The body mesh the outfits are shown on. Its active material (the skin) gets the atlas")
    lib_robe: PointerProperty(
        name="Robe", type=bpy.types.Object, poll=lambda self, ob: ob.type == "MESH",
        description="The robe skirt mesh (geoset 1302), shown in renders of Robe outfits only")
    lib_size: IntProperty(
        name="Render Size", default=256, min=64, max=2048,
        description="Square size in pixels of each rendered thumbnail")
    name_suffix: StringProperty(
        name="Name Suffix", default="",
        description="Appended to each exported file name (before _m/_f/_u), "
                    "e.g. _custom. Empty keeps the original names")
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


def ensure_atlas(size=1024):
    """The atlas image, created fully transparent (size x size/2) if none exists yet, so an
    atlas node never lacks an image: a missing image draws opaque pink over the skin."""
    img = _get_atlas()
    if img is None:
        img = _write_atlas(np.zeros((size // 2, size, 4), np.uint8))
    return img


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
    _last_built[:] = [rgba.copy()]
    return img


_last_built = []     # the atlas as the tool last wrote it, to tell paint from our own rebuilds


def atlas_has_paint(img=None):
    """True if the atlas differs from what the tool built, i.e. the user has painted on it.

    Blender marks the image dirty for any pixel write, including our own rebuilds, so
    ``is_dirty`` alone can't be used.
    """
    img = img or _get_atlas()
    if img is None or not img.is_dirty:
        return False
    if not _last_built:
        return True
    cur = _read_atlas(img)
    return cur.shape != _last_built[0].shape or not np.array_equal(cur, _last_built[0])


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


def skin_image(p):
    """The skin of the loaded race as a Blender image (BLP converted to PNG), or None."""
    path = skin_file(p)
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


class M2_OT_outfit_rebuild(Operator):
    bl_idname = "m2.outfit_rebuild"
    bl_label = "Discard Paint & Rebuild"
    bl_description = "Throw away the paint on the atlas and build the chosen armor again"

    def execute(self, context):
        _autobuild(context.scene.m2_outfit, force=True)
        return {"FINISHED"}


def assign_atlas(mat, skin=None):
    """Lay the atlas image over ``mat``'s existing base colour (``skin`` is used if it has none)."""
    mat.use_nodes = True
    nt = mat.node_tree
    node = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.label == "Outfit Atlas"), None)
    if node is None:
        node = nt.nodes.new("ShaderNodeTexImage")
        node.label = "Outfit Atlas"
        node.location = (-420, 0)
    node.image = _get_atlas() or ensure_atlas()
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) \
            or nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mix = next((n for n in nt.nodes if n.label == "Outfit Over Skin"), None)
    if mix is None:
        # Lay the outfit over whatever the material already shows as its
        # colour (the main skin), leaving that setup untouched underneath.
        under = bsdf.inputs["Base Color"].links
        under = under[0].from_socket if under else None
        if under is None and skin is not None:
            sn = nt.nodes.new("ShaderNodeTexImage")
            sn.label = "Outfit Skin"
            sn.image = skin
            sn.location = (-420, 300)
            under = sn.outputs["Color"]
        mix = nt.nodes.new("ShaderNodeMix")
        mix.label = "Outfit Over Skin"
        mix.data_type = "RGBA"
        mix.location = (-160, 150)
        nt.links.new(node.outputs["Alpha"], mix.inputs[0])
        if under is not None:
            nt.links.new(under, mix.inputs[6])
        nt.links.new(node.outputs["Color"], mix.inputs[7])
        nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    nt.nodes.active = node


class M2_OT_outfit_export(Operator):
    bl_idname = "m2.outfit_export"
    bl_label = "Export Outfit Textures"
    bl_description = "Split the edited atlas back into its regions and save them as PNG/BLP"

    @classmethod
    def poll(cls, context):
        return _get_atlas() is not None

    def execute(self, context):
        p = context.scene.m2_outfit
        root = _roots(p)
        out_dir = prefs.path("export_path")
        if not out_dir:
            self.report({"ERROR"}, "Set the Outfit Export Folder in the add-on preferences first.")
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
                native_size=True, write_png=p.write_png,
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
        col.operator("m2.open_preferences", icon="PREFERENCES")
        if not prefs.path("game_path") and not prefs.path("components_path"):
            col.label(text="Set the Game Export Folder in the add-on preferences", icon="ERROR")
        col.prop(p, "mod_path")
        if p.mod_path:
            col.prop(p, "mod_only")
        row = layout.row(align=True)
        row.prop(p, "gender", expand=True)
        row.prop(p, "atlas_size", text="")

        box = layout.box()
        row = box.row()
        row.label(text="Armor", icon="MATCLOTH")
        row.operator("m2.outfit_rescan", text="", icon="FILE_REFRESH")
        box.prop(p, "find", icon="VIEWZOOM")
        box.prop(p, "armor_type")
        box.prop(p, "source")
        box.prop(p, "tier")
        box.prop(p, "armor", text="")
        box.prop(p, "color", text="")
        box.label(text="Fills the regions ticked below", icon="INFO")
        if atlas_has_paint():
            warn = layout.box()
            warn.label(text="The atlas has unsaved paint. Picking other armor", icon="BRUSH_DATA")
            warn.label(text="won't overwrite it; export it or discard it.")
            warn.operator("m2.outfit_rebuild", icon="TRASH")


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
        row = box.row()
        row.scale_y = 1.4
        row.operator("m2.outfit_export", icon="EXPORT")


_classes = (
    M2OutfitProps,
    M2_OT_outfit_rescan,
    M2_OT_outfit_rebuild,
    M2_OT_outfit_export,
    VIEW3D_PT_m2_outfit,
    VIEW3D_PT_m2_outfit_regions,
    VIEW3D_PT_m2_outfit_export,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.m2_outfit = PointerProperty(type=M2OutfitProps)
    from . import outfit_library, outfit_drop
    outfit_library.register()
    outfit_drop.register()


def unregister():
    from . import outfit_library, outfit_drop
    outfit_drop.unregister()
    outfit_library.unregister()
    del bpy.types.Scene.m2_outfit
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
