"""Check for / download the community listfile (wowdev/wow-listfile).

Nothing here runs by itself: the network is only used when the user presses
"Check for update" or "Download" in the add-on preferences. Requests run on a
worker thread so Blender never freezes; results are picked up by a timer.
"""

import json
import os
import threading
import urllib.request

import bpy
from bpy.types import Operator

from . import prefs

API_LATEST = "https://api.github.com/repos/wowdev/wow-listfile/releases/latest"
ASSET_NAME = "community-listfile.csv"
UA = {"User-Agent": "blender-addon", "Accept": "application/vnd.github+json"}

# Shared UI state, written by the worker thread, read by draw().
state = {"busy": False, "msg": "", "remote": None, "progress": 0.0}


def default_path():
    """Where the file goes when the Listfile preference is empty."""
    folder = bpy.utils.extension_path_user(prefs.ADDON_ID, create=True)
    return os.path.join(folder, ASSET_NAME)


def target_path():
    return prefs.path("listfile") or default_path()


def _meta_path(p):
    return p + ".version.json"


def local_tag(p):
    try:
        with open(_meta_path(p), "r", encoding="utf-8") as f:
            return json.load(f).get("tag", "")
    except (OSError, ValueError):
        return ""


def _fetch_latest():
    req = urllib.request.Request(API_LATEST, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        rel = json.load(r)
    for a in rel.get("assets", []):
        if a.get("name") == ASSET_NAME:
            return {"tag": rel.get("tag_name", ""), "url": a["browser_download_url"],
                    "size": a.get("size", 0), "date": (a.get("updated_at") or "")[:10]}
    raise RuntimeError("release has no %s asset" % ASSET_NAME)


def _download(info, dest):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + ".part"
    req = urllib.request.Request(info["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                state["progress"] = got / total
    os.replace(part, dest)
    with open(_meta_path(dest), "w", encoding="utf-8") as f:
        json.dump({"tag": info["tag"]}, f)


def _run(job):
    try:
        job()
    except Exception as e:  # noqa: BLE001 - report any network/file error in the UI
        state["msg"] = "Failed: %s" % e
    finally:
        state["busy"] = False


def _start(job, first_msg):
    if state["busy"]:
        return
    state.update(busy=True, msg=first_msg, progress=0.0)
    threading.Thread(target=_run, args=(job,), daemon=True).start()

    def poll():
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "PREFERENCES":
                    area.tag_redraw()
        return 0.5 if state["busy"] else None
    bpy.app.timers.register(poll, first_interval=0.5)


class M2_OT_listfile_check(Operator):
    bl_idname = "m2.listfile_check"
    bl_label = "Check for Update"
    bl_description = "Ask GitHub (wowdev/wow-listfile) for the latest community listfile"

    def execute(self, context):
        def job():
            info = _fetch_latest()
            state["remote"] = info
            have = local_tag(target_path()) if os.path.isfile(target_path()) else ""
            if not os.path.isfile(target_path()):
                state["msg"] = "Not downloaded yet. Latest: %s" % info["tag"]
            elif have == info["tag"]:
                state["msg"] = "Up to date (%s)." % have
            else:
                state["msg"] = "Update available: %s (yours: %s)" % (info["tag"], have or "unknown")
        _start(job, "Checking...")
        return {"FINISHED"}


class M2_OT_listfile_download(Operator):
    bl_idname = "m2.listfile_download"
    bl_label = "Download / Update"
    bl_description = "Download the latest community listfile to the Listfile path " \
                     "(a default location is used and filled in if the path is empty)"

    def execute(self, context):
        pr = prefs.get()
        dest = target_path()
        if not prefs.path("listfile"):
            pr.listfile = dest

        def job():
            info = state["remote"] or _fetch_latest()
            state["remote"] = info
            state["msg"] = "Downloading %s..." % info["tag"]
            _download(info, dest)
            state["msg"] = "Downloaded %s (%d MB)." % (info["tag"], os.path.getsize(dest) >> 20)
        _start(job, "Starting download...")
        return {"FINISHED"}


def draw(box, pr):
    row = box.row(align=True)
    row.enabled = not state["busy"]
    row.operator("m2.listfile_check", icon="URL")
    row.operator("m2.listfile_download", icon="IMPORT")
    if state["busy"] and state["progress"]:
        box.progress(factor=state["progress"], type="BAR", text="%d%%" % (state["progress"] * 100))
    if state["msg"]:
        box.label(text=state["msg"])
    elif os.path.isfile(target_path()):
        box.label(text="Installed: %s" % (local_tag(target_path()) or "version unknown"))


_classes = (M2_OT_listfile_check, M2_OT_listfile_download)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
