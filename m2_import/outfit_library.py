"""Outfit asset library: render every outfit in the current filter on the user's own model.

For each outfit matching the Type / Source / Expansion filters the atlas is built, laid over
the model's skin, rendered with the scene camera and pose, and stored as a Material asset
(preview = the render, outfit id in the ``m2_outfit_id`` custom property) in a .blend that
Blender's Asset Browser can index. Nothing but the outfit id is stored: the textures are
rebuilt from the user's own game files when an outfit is used.
"""

import os
import re
import uuid

import bpy
import numpy as np
from bpy.types import Operator, Panel

from . import outfit, outfit_index, outfit_ui, prefs

# Camera rig + lighting shipped with the add-on (tools/AssetLibrary-Setup.blend).
DEFAULT_RIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tools", "AssetLibrary-Setup.blend")
PREVIEW = 256       # asset thumbnail size


def _safe(text):
    return re.sub(r"[^\w\- ]+", "", text).strip()[:80] or "outfit"


def _shrink(arr, size):
    """(h, w, 4) float image -> at most ``size`` px high/wide by whole-factor box averaging."""
    h, w, _ = arr.shape
    f = max(1, min(w, h) // size)
    while f > 1 and (h % f or w % f):
        f -= 1
    if f > 1:
        arr = arr.reshape(h // f, f, w // f, f, 4).mean((1, 3))
    return np.ascontiguousarray(arr, dtype=np.float32)


CATS_FILE = "blender_assets.cats.txt"
_CATS_HEAD = """# This is an Asset Catalog Definition file for Blender.
#
# Empty lines and lines starting with `#` will be ignored.
# The first non-ignored line should be the version indicator.
# Other lines are of the format "UUID:catalog/path/for/assets:simple catalog name"

VERSION 1

"""


def _cat_part(text):
    return re.sub(r"[:/\\]+", " ", text).strip() or "Other"


def catalog_path(g):
    """Catalog of an outfit, mirroring the browser filters: Type / Source / Expansion.

    Outfits that have a texture in the mod folder get their own top-level ``Modded`` catalog
    with the same Type / Source / Expansion tree below it."""
    parts = (_cat_part(dict(outfit_index.TYPES).get(g["kind"], g["kind"])),
             _cat_part(outfit_index.SOURCE_LABEL[g["source"]].split(" (")[0]),
             _cat_part(g["tier"]))
    return "/".join((("Modded",) if g.get("modded") else ()) + parts)


def write_catalogs(folder, paths):
    """Make sure every catalog path (and its parents) is in the library's catalog file.

    Existing entries, including ones made by hand in Blender, are kept. Ids are derived from
    the path, so re-running never creates duplicates. Returns ``{path: uuid string}``.
    """
    file = os.path.join(folder, CATS_FILE)
    known, lines = {}, []
    try:
        with open(file, encoding="utf8") as f:
            for line in f:
                line = line.rstrip("\n")
                lines.append(line)
                parts = line.split(":", 2)
                if len(parts) == 3 and not line.startswith("#"):
                    known[parts[1]] = parts[0]
    except OSError:
        lines = _CATS_HEAD.split("\n")[:-1]
    ids, added = {}, []
    for path in sorted(paths):
        bits = path.split("/")
        for n in range(1, len(bits) + 1):
            sub = "/".join(bits[:n])
            if sub not in known:
                known[sub] = str(uuid.uuid5(uuid.NAMESPACE_URL, "elune-m2/catalog/" + sub))
                added.append("%s:%s:%s" % (known[sub], sub, sub.replace("/", "-")))
        ids[path] = known[path]
    if added:
        while lines and not lines[-1].strip():
            lines.pop()
        os.makedirs(folder, exist_ok=True)
        with open(file, "w", encoding="utf8", newline="\n") as f:
            f.write("\n".join(lines + added) + "\n")
    return ids


def _is_skin_mesh(ob):
    """An imported geoset drawn only with the client-composited character skin (type 1)."""
    if ob.type != "MESH" or "m2_skin_section_id" not in ob.keys() or not ob.material_slots:
        return False
    return all(s.material is not None
               and str(s.material.get("m2_texture_types", "")).split(",")[0] == "1"
               for s in ob.material_slots)


def _descendants(ob):
    for c in ob.children:
        yield c
        yield from _descendants(c)


class M2_OT_outfit_setup_scene(Operator):
    bl_idname = "m2.outfit_setup_scene"
    bl_label = "Set Up Library Scene"
    bl_description = ("From the selected imported character: join the skin geosets in its "
                      "'Core Model' collection (body, feet, hands, ...) into one 'Body' mesh with a single skin material, "
                      "use the Skin texture on it, keep the robe skirt (geoset 1302) for Robe outfits, "
                      "play the Stand animation, bring in the camera rig and lighting from the "
                      "library rig file and set the render to Eevee at the Render Size. The importer's hidden collections (variant geosets, helpers) "
                      "are deleted; the original core geosets stay, hidden")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.mode == "OBJECT"

    def execute(self, context):
        p = context.scene.m2_outfit
        root = context.active_object
        while root.parent is not None:
            root = root.parent
        # only the default pieces the importer put in "Core Model": never the variant geosets
        # (other gloves, boots, robes, ...) that sit in the Geoset_* collections
        def in_core(o):
            return any(c.name.split(".")[0] == "Core Model" for c in o.users_collection)
        parts = [o for o in _descendants(root) if _is_skin_mesh(o) and in_core(o)]
        if root.type == "MESH" and _is_skin_mesh(root) and in_core(root):
            parts.append(root)
        if not parts:
            self.report({"ERROR"}, "No skin geosets in a 'Core Model' collection under the "
                                   "selected character (import it with 'split geosets' on).")
            return {"CANCELLED"}
        # work on copies so the imported model stays as it was
        coll = bpy.data.collections.new("Outfit Library Scene")
        context.scene.collection.children.link(coll)
        copies = []
        for o in parts:
            c = o.copy()
            c.data = o.data.copy()
            coll.objects.link(c)
            copies.append(c)
            o.hide_set(True)
            o.hide_render = True
        for o in context.view_layer.objects:
            o.select_set(False)
        for c in copies:
            c.select_set(True)
        context.view_layer.objects.active = copies[0]
        with context.temp_override(active_object=copies[0], object=copies[0],
                                   selected_objects=copies, selected_editable_objects=copies):
            bpy.ops.object.join()
        body = context.view_layer.objects.active
        body.name = body.data.name = "Body"
        # one skin material for the whole joined mesh: the atlas goes on it
        mesh = body.data
        counts = {}
        for poly in mesh.polygons:
            counts[poly.material_index] = counts.get(poly.material_index, 0) + 1
        best = max(counts, key=counts.get) if counts else 0
        src = mesh.materials[best] if best < len(mesh.materials) else None
        skin = src.copy() if src is not None else bpy.data.materials.new("Outfit Skin")
        skin.name = "Outfit Skin"
        mesh.polygons.foreach_set("material_index", [0] * len(mesh.polygons))
        mesh.materials.clear()
        mesh.materials.append(skin)
        mesh.update()
        race, gender = outfit_ui.race_gender(root, prefs.path("game_path"))
        p.skin_key = "%s/%s" % (race, gender) if race else ""
        if gender:
            p.gender = "f" if gender == "Female" else "m"
        outfit_ui.ensure_atlas(int(p.atlas_size))
        outfit_ui.assign_atlas(skin, outfit_ui.skin_image(p))
        p.lib_body = body
        robe = self._keep_robe(context, root, coll, skin)
        p.lib_robe = robe
        arm = self._armature(root)
        removed = self._remove_hidden_collections(context, root, [o for o in (body, robe) if o is not None])
        stand = self._play_stand(context, root)
        rig = self._append_rig(context, arm)
        self._render_settings(context, p)
        if not rig:
            self._camera_and_light(context, body)
        msg = "Body ready (%d geosets joined%s)." % (len(parts), ", robe kept" if robe else "")
        msg += " Removed %d hidden collection(s) (%d objects)." % removed
        msg += " Stand: %s." % ("playing" if stand else "not found, pose unchanged")
        msg += " Camera rig and lights: %s." % (
            "loaded" if rig else "rig file not found, used a basic camera and light")
        if not outfit_ui.skin_file(p):
            msg += (" No separate skin found for %s: set the Game Export Folder (it needs "
                    "Character/<Race>/<Gender>)." % (p.skin_key or "this race"))
        else:
            msg += " Skin: %s." % os.path.basename(outfit_ui.skin_file(p))
        if not rig:
            self.report({"WARNING"}, "Camera rig NOT loaded: set 'Library Rig File' in the add-on "
                                     "preferences (Asset Paths) to the .blend with the 'Render' "
                                     "collection. A basic camera and one light were added instead. "
                                     + msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}

    @staticmethod
    def _keep_robe(context, root, coll, skin):
        """Move the robe skirt (geoset 1302) into the library scene collection, on the shared
        skin material, hidden until a Robe outfit is rendered. None if there is no such geoset."""
        src = next((o for o in _descendants(root)
                    if o.type == "MESH" and int(o.get("m2_skin_section_id", -1)) == 1302
                    and _is_skin_mesh(o)), None)
        if src is None:
            return None
        for c in list(src.users_collection):
            c.objects.unlink(src)
        coll.objects.link(src)
        src.name = "Robe"
        mesh = src.data
        mesh.polygons.foreach_set("material_index", [0] * len(mesh.polygons))
        mesh.materials.clear()
        mesh.materials.append(skin)
        mesh.update()
        src.hide_render = True
        src.hide_set(True)
        return src

    @staticmethod
    def _remove_hidden_collections(context, root, keep_objects):
        """Delete the collections the importer left hidden (variant geosets, helpers, ...) with
        everything in them. Returns (collections, objects) removed."""
        ours = {o for o in _descendants(root)}
        keep = set(keep_objects) | {root}

        def hidden(lc, out):
            for child in lc.children:
                if child.exclude or child.hide_viewport or child.collection.hide_viewport:
                    out.append(child.collection)
                else:
                    hidden(child, out)
            return out

        cols = []
        for c in hidden(context.view_layer.layer_collection, []):
            cols.append(c)
            cols.extend(c.children_recursive)
        # only collections that hold the character's own objects; never the library scene
        cols = [c for c in cols if any(o in ours for o in c.all_objects)]
        doomed = {o for c in cols for o in c.all_objects if o not in keep}
        n_obj = 0
        meshes = []
        for o in doomed:
            if o.data is not None and o.type == "MESH":
                meshes.append(o.data)
            bpy.data.objects.remove(o)
            n_obj += 1
        for c in cols:
            if c.name in bpy.data.collections and not c.all_objects:
                bpy.data.collections.remove(c)
        for m in meshes:
            if m.users == 0:
                bpy.data.meshes.remove(m)
        return len(cols), n_obj

    @staticmethod
    def _render_settings(context, p):
        """Thumbnail renders: Eevee, a small square image (the Render Size setting)."""
        r = context.scene.render
        try:
            r.engine = "BLENDER_EEVEE"
        except TypeError:       # older Blender spelled it BLENDER_EEVEE_NEXT
            r.engine = "BLENDER_EEVEE_NEXT"
        r.resolution_x = r.resolution_y = p.lib_size
        r.resolution_percentage = 100

    @staticmethod
    def _armature(root):
        return root if root.type == "ARMATURE" else next(
            (o for o in _descendants(root) if o.type == "ARMATURE"), None)

    @staticmethod
    def _play_stand(context, root):
        """Make the armature play its Stand clip (sequence 0, variation 0) from frame 0."""
        arm = M2_OT_outfit_setup_scene._armature(root)
        if arm is None:
            return False
        acts = [a for a in bpy.data.actions
                if a.get("m2_seq_id") == 0 and a.get("m2_seq_var", 0) == 0]
        if not acts:
            return False
        act = next((a for a in acts if a.name.startswith(arm.name) or a.name.startswith(root.name)),
                   acts[0])
        ad = arm.animation_data_create()
        ad.action = act
        try:
            if act.slots:
                ad.action_slot = act.slots[0]
        except Exception:  # noqa: BLE001 - slot API differs between versions
            pass
        sc = context.scene
        lo, hi = act.frame_range
        sc.frame_start, sc.frame_end = int(lo), max(int(hi), int(lo) + 1)
        sc.frame_set(int(lo))
        return True

    # rig bone -> (character bone to follow, axes followed)
    _FOLLOW = {"Aim": ("Spine_02", (True, True, True)), "Camera": ("Spine_03", (False, False, True))}

    @staticmethod
    def _retarget(rig, arm):
        """Attach the rig to ``arm``: the Aim bone follows Spine_02 (X, Y, Z) and the Camera
        bone follows Spine_03 (Z only). Existing Copy Location constraints are re-aimed (and
        any other constraint pointing at a stand-in armature); missing ones are added, ahead of
        Track To so the aim is computed from the final position."""
        n = 0
        for ob in rig.all_objects:
            cons = list(ob.constraints)
            if ob.pose is not None:
                for pb in ob.pose.bones:
                    cons += list(pb.constraints)
            for c in cons:
                t = getattr(c, "target", None)
                if t is not None and t.type == "ARMATURE" and t is not ob and t is not arm:
                    c.target = arm
                    n += 1
        drive = next((o for o in rig.all_objects if o.type == "ARMATURE"), None)
        if drive is None:
            return n
        for bone, (follow, axes) in M2_OT_outfit_setup_scene._FOLLOW.items():
            pb = drive.pose.bones.get(bone)
            if pb is None:
                continue
            con = next((c for c in pb.constraints if c.type == "COPY_LOCATION"
                        and c.target is not None and c.target.type == "ARMATURE"
                        and c.target is not drive), None)
            if con is None:
                con = pb.constraints.new("COPY_LOCATION")
                pb.constraints.move(len(pb.constraints) - 1, 0)
                n += 1
            con.name = "Follow %s" % follow
            con.target, con.subtarget = arm, follow
            con.use_x, con.use_y, con.use_z = axes
            con.target_space = con.owner_space = "WORLD"
        return n

    @staticmethod
    def _append_rig(context, arm=None):
        """Append the 'Render' collection (camera rig, lights) from the rig file, attach its
        constraints to ``arm`` and make its camera the scene camera."""
        sc = context.scene
        rig = next((c for c in bpy.data.collections if c.get("m2_library_rig")), None)
        if rig is not None:
            if arm is not None:
                M2_OT_outfit_setup_scene._retarget(rig, arm)
            return True
        path = prefs.path("rig_file") or DEFAULT_RIG
        if not os.path.isfile(path):
            return False
        before = (set(bpy.data.worlds), set(bpy.data.objects), set(bpy.data.armatures),
                  set(bpy.data.actions))
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            if "Render" not in src.collections:
                return False
            dst.collections = ["Render"]
            dst.worlds = list(src.worlds)
        rig = next((c for c in dst.collections if c is not None), None)
        if rig is None:
            return False
        rig["m2_library_rig"] = True
        sc.collection.children.link(rig)
        # Appending also pulled in whatever the constraints pointed at (a stand-in armature).
        # Re-aim them at the real character, then drop the stand-in and its leftovers.
        if arm is not None:
            M2_OT_outfit_setup_scene._retarget(rig, arm)
        keep = set(rig.all_objects)
        for o in [o for o in bpy.data.objects if o not in before[1] and o not in keep]:
            data = o.data
            bpy.data.objects.remove(o)
            if isinstance(data, bpy.types.Armature) and data.users == 0:
                bpy.data.armatures.remove(data)
        for act in [a for a in bpy.data.actions if a not in before[3]]:
            if act.users == 0 or (act.users == 1 and act.use_fake_user):
                bpy.data.actions.remove(act)
        for c in (rig, *rig.children_recursive):
            if c.name.split(".")[0].startswith("Widgets"):
                for o in c.objects:
                    o.hide_render = True
        cam = next((o for o in rig.all_objects if o.type == "CAMERA"), None)
        if cam is not None:
            sc.camera = cam
        new_worlds = [w for w in bpy.data.worlds if w not in before[0]]
        if new_worlds and sc.world is None:
            sc.world = new_worlds[0]
        return True

    @staticmethod
    def _camera_and_light(context, body):
        from mathutils import Vector
        import math
        sc = context.scene
        pts = [body.matrix_world @ Vector(c) for c in body.bound_box]
        lo = Vector((min(v.x for v in pts), min(v.y for v in pts), min(v.z for v in pts)))
        hi = Vector((max(v.x for v in pts), max(v.y for v in pts), max(v.z for v in pts)))
        mid, height = (lo + hi) / 2, max(hi.z - lo.z, 0.1)
        if sc.camera is None:
            cam = bpy.data.objects.new("Outfit Camera", bpy.data.cameras.new("Outfit Camera"))
            cam.data.lens, cam.data.sensor_width = 50, 36
            # imported models face -Y, so the front view looks along +Y
            dist = height * 0.6 / (18 / 50)
            cam.location = (mid.x, lo.y - dist, mid.z)
            cam.rotation_euler = (math.pi / 2, 0, 0)
            sc.collection.objects.link(cam)
            sc.camera = cam
        if not any(o.type == "LIGHT" for o in sc.objects):
            lamp = bpy.data.objects.new("Outfit Light", bpy.data.lights.new("Outfit Light", "SUN"))
            lamp.data.energy = 3
            lamp.rotation_euler = (math.radians(55), 0, math.radians(-25))
            lamp.location = (mid.x, lo.y - height, hi.z + height)
            sc.collection.objects.link(lamp)


class M2_OT_outfit_library(Operator):
    bl_idname = "m2.outfit_library"
    bl_label = "Render Outfit Library"
    bl_description = ("Render a thumbnail of every armor matching the Type / Source / Expansion filters "
                      "on the chosen model (its pose and the scene camera), and save them as a "
                      "Material asset library. Esc cancels. Replaces the current outfit atlas")

    _timer = None

    @classmethod
    def poll(cls, context):
        p = context.scene.m2_outfit
        return p.lib_body is not None and context.scene.camera is not None

    # --- setup ----------------------------------------------------------------
    def invoke(self, context, event):
        p = context.scene.m2_outfit
        idx = outfit_ui._index(p)
        root = outfit_ui._roots(p)
        out = prefs.path("lib_path")
        mat = p.lib_body.active_material
        img = outfit_ui._get_atlas()
        problem = (
            "Set the Game Export Folder (or Components Override) in the add-on preferences first." if idx is None else
            "Set the Outfit Library Folder in the add-on preferences first." if not out else
            "The model has no active material to lay the outfit on." if mat is None else
            "The outfit atlas has unsaved paint; save or discard it first."
            if outfit_ui.atlas_has_paint(img) else "")
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        self._gids = idx.variants(p.source, p.tier, p.armor_type)
        if not self._gids:
            self.report({"ERROR"}, "No outfits match the current filters.")
            return {"CANCELLED"}
        self._idx, self._root, self._mat = idx, root, mat
        self._robe = p.lib_robe
        self._gender, self._atlas = p.gender, int(p.atlas_size)
        tag = " - ".join(_safe(x) for x in (p.armor_type, p.source, p.tier))
        self._blend = os.path.join(out, "Outfits - %s.blend" % tag)
        self._prev = os.path.join(out, "previews", tag)
        os.makedirs(self._prev, exist_ok=True)
        r = context.scene.render
        self._saved = (r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath,
                       r.image_settings.file_format, r.image_settings.color_mode)
        r.resolution_x = r.resolution_y = p.lib_size
        r.resolution_percentage = 100
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGBA"
        self._i, self._done, self._skipped, self._names = 0, [], 0, set()
        wm = context.window_manager
        wm.progress_begin(0, len(self._gids))
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # --- one outfit per tick --------------------------------------------------
    def modal(self, context, event):
        if event.type == "ESC":
            return self._finish(context, cancelled=True)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if self._i >= len(self._gids):
            return self._finish(context)
        self._render_one(context, self._gids[self._i])
        self._i += 1
        context.window_manager.progress_update(self._i)
        context.workspace.status_text_set(
            "Rendering outfits %d / %d  (Esc to cancel)" % (self._i, len(self._gids)))
        return {"RUNNING_MODAL"}

    def _render_one(self, context, gid):
        g = self._idx.groups[gid]
        rgba, used = outfit.build_atlas(self._root, self._idx.regions(gid), self._atlas,
                                        self._gender, log=lambda m: None)
        if not used:                    # nothing of this armor exists for the chosen gender
            self._skipped += 1
            return
        outfit_ui._write_atlas(rgba)
        outfit_ui.assign_atlas(self._mat)
        if self._robe is not None:
            self._robe.hide_render = g["kind"] != "robe"
        name = "%s - %s" % (g["label"], dict(outfit_index.TYPES).get(g["kind"], g["kind"]))
        if name in self._names:
            name += " " + gid.split("/")[1]
        self._names.add(name)
        path = os.path.join(self._prev, "%04d %s.png" % (self._i, _safe(name)))
        context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        self._done.append((gid, name, path))

    # --- write the library ----------------------------------------------------
    def _finish(self, context, cancelled=False):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        wm.progress_end()
        context.workspace.status_text_set(None)
        r = context.scene.render
        (r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath,
         r.image_settings.file_format, r.image_settings.color_mode) = self._saved
        if self._robe is not None:
            self._robe.hide_render = True
        self._cats = write_catalogs(
            os.path.dirname(self._blend),
            {catalog_path(self._idx.groups[gid]) for gid, _, _ in self._done}) if self._done else {}
        mats = [self._make_asset(*job) for job in self._done]
        if mats:
            bpy.data.libraries.write(self._blend, set(mats), fake_user=True, compress=True)
            for m in mats:
                bpy.data.materials.remove(m)
        self.report({"WARNING" if cancelled else "INFO"},
                    "%s: %d outfit(s) in %s%s" % (
                        "Cancelled" if cancelled else "Done", len(mats), self._blend,
                        "; %d skipped (nothing for this gender)" % self._skipped
                        if self._skipped else ""))
        return {"CANCELLED" if cancelled else "FINISHED"}

    def _make_asset(self, gid, name, path):
        g = self._idx.groups[gid]
        m = bpy.data.materials.new(name)
        m["m2_outfit_id"] = gid
        m["m2_gender"] = self._gender
        m.asset_mark()
        ad = m.asset_data
        ad.description = g["tip"][:500]
        cat = self._cats.get(catalog_path(g))
        if cat:
            ad.catalog_id = cat
        for tag in (dict(outfit_index.TYPES).get(g["kind"], g["kind"]),
                    outfit_index.SOURCE_LABEL[g["source"]].split(" (")[0], g["tier"],
                    *(("Modded",) if g.get("modded") else ())):
            ad.tags.new(tag[:63], skip_if_exists=True)
        img = bpy.data.images.load(path)
        try:
            w, h = img.size
            buf = np.empty(w * h * 4, np.float32)
            img.pixels.foreach_get(buf)
            small = _shrink(buf.reshape(h, w, 4), PREVIEW)
        finally:
            bpy.data.images.remove(img)
        sh, sw, _ = small.shape
        pv = m.preview_ensure()
        for kind in ("image", "icon"):
            setattr(pv, kind + "_size", (sw, sh))
            getattr(pv, kind + "_pixels_float").foreach_set(small.ravel())
        return m


class VIEW3D_PT_m2_outfit_library(outfit_ui._OutfitPanel, Panel):
    bl_idname = "VIEW3D_PT_m2_outfit_library"
    bl_label = "Outfit Library"
    bl_parent_id = "VIEW3D_PT_m2_outfit"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = context.scene.m2_outfit
        col = self.layout.column(align=True)
        col.operator("m2.outfit_setup_scene", icon="MOD_ARMATURE")
        if p.skin_key:
            tones = outfit_ui.skin_tones(p)
            col.label(text="Race: %s" % p.skin_key.replace("/", " "), icon="USER")
            if tones:
                col.prop(p, "skin_tone")
            else:
                col.label(text="No skin found in the Game Export Folder", icon="ERROR")
        col.separator()
        col.prop(p, "lib_body")
        col.prop(p, "lib_size")
        idx = outfit_ui._index(p)
        n = len(idx.variants(p.source, p.tier, p.armor_type)) if idx else 0
        col = self.layout.column()
        col.label(text="%d outfits match the Type / Source / Expansion filters" % n, icon="INFO")
        col.label(text="Uses the scene camera and the model's current pose")
        row = col.row()
        row.scale_y = 1.4
        row.operator("m2.outfit_library", icon="RENDER_STILL")


_classes = (M2_OT_outfit_setup_scene, M2_OT_outfit_library, VIEW3D_PT_m2_outfit_library)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
