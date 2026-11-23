"""Stable fingerprint of a Blender action's keyframes.

Used to tell whether an inherited (parent-skeleton) animation was edited after
import: the hash is stamped on the clip at import and recomputed at export.
"""

import hashlib


def _fcurves(action):
    out = []
    if hasattr(action, "layers") and len(getattr(action, "layers", [])):
        for layer in action.layers:
            for strip in layer.strips:
                for cb in (getattr(strip, "channelbags", None) or []):
                    out.extend(cb.fcurves)
    if not out:
        out = list(getattr(action, "fcurves", []))
    return out


def fingerprint(action):
    """sha1 over every fcurve's (path, index, frame, value), rounded."""
    rows = []
    for fc in _fcurves(action):
        pts = tuple((round(kp.co[0], 3), round(kp.co[1], 5))
                    for kp in fc.keyframe_points)
        rows.append((fc.data_path, fc.array_index, pts))
    rows.sort(key=lambda r: (r[0], r[1]))
    return hashlib.sha1(repr(rows).encode("utf-8")).hexdigest()
