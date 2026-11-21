"""Outfit texture atlas: merge armour texture components, split them back out.

Character armour is stored as small per-region textures (``ArmUpperTexture``,
``TorsoLowerTexture`` ...). The game composites them onto one body atlas; here
they are pasted onto the same atlas so the whole outfit can be painted at once,
then each region is cut back out and written as PNG/BLP.

The atlas is the character skin: 2:1 (1024x512 at 1x). Only its left half holds the
body (arms/hands, then torso/legs/feet, 256px each); the right half is the face and
is left empty and untouched. Rectangles below are for 1024 wide and scale with it.
Everything here works without Blender (images are numpy RGBA, top row first).
"""

import os
import struct
import zlib

import numpy as np

from . import blp_encode, textures

# key, label, folder on disk, filename code, (x, y, w, h) on a 1024x512 atlas
REGIONS = (
    ("arm_upper",   "Arm Upper",   "ArmUpperTexture",   "au", (0,   0,   256, 128)),
    ("arm_lower",   "Arm Lower",   "ArmLowerTexture",   "al", (0,   128, 256, 128)),
    ("hand",        "Hand",        "HandTexture",       "ha", (0,   256, 256, 64)),
    ("torso_upper", "Torso Upper", "TorsoUpperTexture", "tu", (256, 0,   256, 128)),
    ("torso_lower", "Torso Lower", "TorsoLowerTexture", "tl", (256, 128, 256, 64)),
    ("leg_upper",   "Leg Upper",   "LegUpperTexture",   "lu", (256, 192, 256, 128)),
    ("leg_lower",   "Leg Lower",   "LegLowerTexture",   "ll", (256, 320, 256, 128)),
    ("foot",        "Foot",        "FootTexture",       "fo", (256, 448, 256, 64)),
)
REGION_BY_KEY = {r[0]: r for r in REGIONS}
_EXTS = (".png", ".blp")
_GENDER_ORDER = {"m": ("m", "u", "f"), "f": ("f", "u", "m")}


def region_rect(key, atlas):
    """Pixel rect (x, y, w, h), top-left origin, of a region on an ``atlas``-wide (2:1) atlas."""
    s = atlas / 1024.0
    x, y, w, h = REGION_BY_KEY[key][4]
    return int(x * s), int(y * s), int(w * s), int(h * s)


# ---------------------------------------------------------------------------
# Reading images
# ---------------------------------------------------------------------------
class PNGError(Exception):
    pass


def read_png(path):
    """Decode a non-interlaced PNG -> uint8 RGBA (h,w,4)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PNGError("not a PNG")
    pos, idat, pal, trns, hdr = 8, [], None, None, None
    while pos + 8 <= len(data):
        ln, tag = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            pal = np.frombuffer(body, np.uint8).reshape(-1, 3)
        elif tag == b"tRNS":
            trns = np.frombuffer(body, np.uint8)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
    if hdr is None:
        raise PNGError("missing IHDR")
    w, h, depth, ctype, _c, _f, interlace = hdr
    if interlace:
        raise PNGError("interlaced PNG not supported")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if chans is None or depth not in (8, 16) or (ctype == 3 and depth != 8):
        raise PNGError("unsupported PNG format (type %d, %d-bit)" % (ctype, depth))
    bpp = chans * depth // 8
    stride = w * bpp
    raw = np.frombuffer(zlib.decompress(b"".join(idat)), np.uint8).reshape(h, stride + 1)
    out = np.zeros((h, stride), np.uint8)
    prev = np.zeros(stride, np.uint8)
    for y in range(h):
        ft, line = raw[y, 0], raw[y, 1:].copy()
        if ft == 1:                                   # Sub
            line = np.cumsum(line.reshape(-1, bpp), 0, dtype=np.uint8).reshape(-1)
        elif ft == 2:                                 # Up
            line = line + prev
        elif ft == 3:                                 # Average
            for i in range(stride):
                a = int(line[i - bpp]) if i >= bpp else 0
                line[i] = (int(line[i]) + ((a + int(prev[i])) >> 1)) & 255
        elif ft == 4:                                 # Paeth
            for i in range(stride):
                a = int(line[i - bpp]) if i >= bpp else 0
                b = int(prev[i])
                c = int(prev[i - bpp]) if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (int(line[i]) + pr) & 255
        elif ft != 0:
            raise PNGError("bad filter type %d" % ft)
        out[y] = line
        prev = line
    px = out.reshape(h, w, chans * (depth // 8))
    if depth == 16:
        px = px[..., 0::2]
    rgba = np.empty((h, w, 4), np.uint8)
    if ctype == 3:
        if pal is None:
            raise PNGError("missing PLTE")
        rgba[..., :3] = pal[np.minimum(px[..., 0], len(pal) - 1)]
        rgba[..., 3] = 255
        if trns is not None:
            lut = np.full(256, 255, np.uint8)
            lut[:len(trns)] = trns
            rgba[..., 3] = lut[px[..., 0]]
    elif ctype == 0:
        rgba[..., :3] = px[..., :1]
        rgba[..., 3] = 255
    elif ctype == 4:
        rgba[..., :3] = px[..., :1]
        rgba[..., 3] = px[..., 1]
    elif ctype == 2:
        rgba[..., :3] = px
        rgba[..., 3] = 255
    else:
        rgba[:] = px
    return rgba


def read_image(path):
    """PNG or BLP file -> uint8 RGBA (h,w,4)."""
    if path.lower().endswith(".blp"):
        with open(path, "rb") as f:
            return textures.decode_blp(f.read())[2]
    return read_png(path)


def resize(rgba, w, h):
    """Resize by pixel repeat (up) / box average (down) for whole factors, else nearest."""
    sh, sw, _ = rgba.shape
    if (sw, sh) == (w, h):
        return rgba
    if w % sw == 0 and h % sh == 0:
        return np.repeat(np.repeat(rgba, h // sh, 0), w // sw, 1)
    if sw % w == 0 and sh % h == 0:
        ky, kx = sh // h, sw // w
        f = rgba.reshape(h, ky, w, kx, 4).astype(np.float32).mean((1, 3))
        return np.rint(f).astype(np.uint8)
    return rgba[(np.arange(h) * sh // h)][:, (np.arange(w) * sw // w)]


# ---------------------------------------------------------------------------
# Finding components
# ---------------------------------------------------------------------------
_list_cache = {}


def _listing(folder):
    """{lowercase filename: real filename} for a folder, cached."""
    if folder not in _list_cache:
        try:
            _list_cache[folder] = {n.lower(): n for n in os.listdir(folder)}
        except OSError:
            _list_cache[folder] = {}
    return _list_cache[folder]


def clear_cache():
    _list_cache.clear()


def region_folder(root, key):
    """The on-disk folder for a region inside ``root`` (case-insensitive), or ''."""
    name = _listing(root).get(REGION_BY_KEY[key][2].lower())
    return os.path.join(root, name) if name else ""


def component_names(root, key):
    """Sorted base names (no extension, no _m/_f/_u) available for a region."""
    folder = region_folder(root, key)
    out = set()
    for low in _listing(folder) if folder else ():
        stem, ext = os.path.splitext(low)
        if ext in _EXTS:
            out.add(stem[:-2] if stem[-2:-1] == "_" and stem[-1:] in "mfu" else stem)
    return sorted(out)


def find_component(root, key, name, gender="m"):
    """Resolve ``name`` to ``(path, gender_suffix)``. Prefers ``gender``, then unisex."""
    folder = region_folder(root, key)
    if not (folder and name):
        return None
    files = _listing(folder)
    for g in _GENDER_ORDER.get(gender, _GENDER_ORDER["m"]) + ("",):
        for ext in _EXTS:
            hit = files.get("%s%s%s" % (name, "_" + g if g else "", ext))
            if hit:
                return os.path.join(folder, hit), g
    return None


# ---------------------------------------------------------------------------
# Atlas build / split
# ---------------------------------------------------------------------------
def build_atlas(root, slots, atlas, gender="m", log=print):
    """Paste each ``{region key: component name}`` onto a blank atlas.

    Returns ``(rgba, used)`` where ``used`` maps region key -> path pasted.
    """
    img = np.zeros((atlas // 2, atlas, 4), np.uint8)
    used = {}
    for key, name in slots.items():
        if not name:
            continue
        hit = find_component(root, key, name, gender)
        if hit is None:
            log("component not found: %s (%s)" % (name, REGION_BY_KEY[key][1]))
            continue
        try:
            src = read_image(hit[0])
        except (OSError, PNGError, textures.BLPError, zlib.error) as exc:
            log("could not read %s: %s" % (hit[0], exc))
            continue
        x, y, w, h = region_rect(key, atlas)
        img[y:y + h, x:x + w] = resize(src, w, h)
        used[key] = hit[0]
    return img, used


def split_atlas(rgba, root, slots, out_dir, gender="m", suffix="",
                native_size=False, write_png=True, write_blp=True, log=print):
    """Cut the selected regions out of ``rgba`` and save them under ``out_dir``.

    Files go to ``out_dir/Item/TextureComponents/<Region>Texture/<name>_<g>.blp``
    (the game's own layout). Returns the list of written paths.
    """
    atlas = rgba.shape[1]
    written = []
    for key, name in slots.items():
        if not name:
            continue
        hit = find_component(root, key, name, gender)
        g = hit[1] if hit else ("u" if gender not in ("m", "f") else gender)
        x, y, w, h = region_rect(key, atlas)
        piece = rgba[y:y + h, x:x + w]
        if native_size and hit:
            try:
                sh, sw, _ = read_image(hit[0]).shape
                piece = resize(piece, sw, sh)
            except (OSError, PNGError, textures.BLPError, zlib.error):
                pass
        folder = os.path.join(out_dir, "Item", "TextureComponents", REGION_BY_KEY[key][2])
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, "%s%s%s" % (name, suffix, "_" + g if g else ""))
        if write_png:
            textures.write_png(base + ".png", piece)
            written.append(base + ".png")
        if write_blp:
            with open(base + ".blp", "wb") as f:
                f.write(blp_encode.encode_blp(piece))
            written.append(base + ".blp")
    return written
