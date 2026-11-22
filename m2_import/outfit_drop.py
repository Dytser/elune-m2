"""Drag-and-drop for the outfit asset library.

The library's Material assets are placeholders that only carry an outfit id. When one is dropped
on a model, Blender puts it in the object's material slot. This handler notices that, puts the
slot's previous material back, and builds the outfit's textures from the user's own game files
onto the shared outfit atlas, laid over that material.
"""

import bpy
from bpy.app.handlers import persistent

_prev = {}          # (object name, slot index) -> last real (non-outfit) material
_pending = set()    # object names with a drop waiting for the timer
_busy = [False]


def _is_outfit(mat):
    return mat is not None and "m2_outfit_id" in mat.keys()


def _seed(ob):
    for i, slot in enumerate(ob.material_slots):
        if slot.material is not None and not _is_outfit(slot.material):
            _prev[(ob.name, i)] = slot.material


def _seed_all():
    for ob in bpy.data.objects:
        if ob.type == "MESH":
            _seed(ob)


def _message(text, icon="INFO"):
    def draw(self, context):
        self.layout.label(text=text)
    bpy.context.window_manager.popup_menu(draw, title="Outfit", icon=icon)
    print("[Elune M2] outfit drop:", text)


def _handle(ob_name):
    """Undo the material swap of one object and apply the dropped outfit."""
    from . import outfit_ui
    ob = bpy.data.objects.get(ob_name)
    if ob is None:
        return
    p = bpy.context.scene.m2_outfit
    dropped = None
    for i, slot in enumerate(ob.material_slots):
        mat = slot.material
        if not _is_outfit(mat):
            continue
        dropped = mat
        keep = _prev.get((ob.name, i))
        if keep is None and p.lib_body is not None:
            keep = p.lib_body.active_material      # model material chosen in the library panel
        slot.material = keep
        target = keep
        break
    if dropped is None:
        return
    gid, gender = dropped["m2_outfit_id"], dropped.get("m2_gender")
    name = dropped.name
    try:
        if dropped.users == 0 and dropped.library is None:
            bpy.data.materials.remove(dropped)
    except (ReferenceError, RuntimeError):
        pass
    if target is None:
        _message("'%s' has no material to put the outfit on." % ob.name, "ERROR")
        return
    if outfit_ui.atlas_has_paint():
        _message("The outfit atlas has unsaved paint; export or discard it first.", "ERROR")
        return
    if not outfit_ui.select_outfit(p, gid, gender):
        _message("Outfit not found in the current Components / Mod folders: %s" % name, "ERROR")
        return
    if outfit_ui._get_atlas() is None:
        _message("Could not build the outfit atlas for '%s'." % name, "ERROR")
        return
    outfit_ui.assign_atlas(target, outfit_ui.skin_image(p))
    _seed(ob)


def _run():
    _busy[0] = True
    try:
        for name in list(_pending):
            _pending.discard(name)
            try:
                _handle(name)
            except Exception as exc:         # never let a timer error spam the console forever
                print("[Elune M2] outfit drop failed:", exc)
    finally:
        _busy[0] = False
    return None


@persistent
def _on_depsgraph(scene, depsgraph):
    if _busy[0]:
        return
    for u in depsgraph.updates:
        ob = u.id
        if not isinstance(ob, bpy.types.Object) or ob.type != "MESH":
            continue
        if any(_is_outfit(s.material) for s in ob.material_slots):
            if not _pending:
                bpy.app.timers.register(_run, first_interval=0.0)
            _pending.add(ob.name)
        else:
            _seed(ob)


@persistent
def _on_load(*_):
    _prev.clear()
    _pending.clear()
    _seed_all()


def register():
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn not in lst:
            lst.append(fn)
    try:
        _seed_all()
    except AttributeError:      # bpy.data is restricted while the add-on registers
        pass


def unregister():
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn in lst:
            lst.remove(fn)
    _prev.clear()
    _pending.clear()
