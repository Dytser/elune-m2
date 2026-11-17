"""The one place that maps WoW model space <-> Blender space.

WoW models face +X (with +Y to their left). Blender's convention is a model
facing -Y, with its left on +X. So on import everything is rotated -90 degrees
about Z:   (x, y, z) -> (y, -x, z)
and on export it is rotated back. ``mirror_x`` (the old "Mirror X" option) is
applied in WoW space, before the rotation on import / after it on export.

Set ``FACE_NEG_Y = False`` to get the old behaviour (WoW axes used unchanged).
"""

FACE_NEG_Y = True


# --- positions / directions -------------------------------------------------
def pos(p, mirror_x=False):
    """WoW (x, y, z) -> Blender."""
    x, y, z = p[0], p[1], p[2]
    if mirror_x:
        x = -x
    if FACE_NEG_Y:
        return (y, -x, z)
    return (x, y, z)


def pos_inv(p, mirror_x=False):
    """Blender (x, y, z) -> WoW."""
    x, y, z = p[0], p[1], p[2]
    if FACE_NEG_Y:
        x, y = -y, x
    if mirror_x:
        x = -x
    return (x, y, z)


# --- rotations (quaternions) ------------------------------------------------
def quat(q, mirror_x=False):
    """WoW quaternion (x, y, z, w) -> Blender (w, x, y, z)."""
    x, y, z, w = q
    if mirror_x:
        y, z = -y, -z
    if FACE_NEG_Y:
        x, y = y, -x
    return (w, x, y, z)


def quat_inv(wxyz, mirror_x=False):
    """Blender quaternion (w, x, y, z) -> WoW (x, y, z, w)."""
    w, x, y, z = wxyz
    if FACE_NEG_Y:
        x, y = -y, x
    if mirror_x:
        y, z = -y, -z
    return (x, y, z, w)


# --- 3x3 matrices -----------------------------------------------------------
def rot3():
    """The rotation applied on import, as a mathutils 3x3 (identity if off)."""
    from mathutils import Matrix
    if not FACE_NEG_Y:
        return Matrix.Identity(3)
    return Matrix(((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
