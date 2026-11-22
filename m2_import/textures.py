"""Find a model's textures on disk and wire them into its Blender materials.

Lookup order for each texture (first hit wins):

1. For every search root, a ``.png``, then a ``.webp``, then a ``.blp`` at the
   texture's path inside the M2 (``Character\\NightElf\\Female\\X.blp``).
   Roots are the folder holding the .m2 and each of its parent folders, then the
   Game Export Folder from the add-on preferences, so a loose extract laid out like the game's data
   tree is found without any setup.
2. Retail models carry a FileDataID instead of a path. Those are looked up by
   ``<id>.png`` / ``.webp`` / ``.blp`` next to the model or in the texture
   folder, and, if a community listfile (``id;path``) is given, by the path it
   maps the id to.

PNG/WebP are used as they are. A BLP is decoded here (no Pillow needed) and
saved as a PNG next to the source BLP, so the next import finds that PNG first.
Everything outside :class:`TextureContext.apply` works without Blender.
"""

import os
import re
import struct
import zlib

import numpy as np

IMAGE_EXTS = (".png", ".webp", ".blp")

# M2 render-flag blend modes
_BLEND_OPAQUE, _BLEND_ALPHA_KEY = 0, 1
# M2Texture.flags: 0x1 wrap U, 0x2 wrap V
_WRAP = 0x3


# ---------------------------------------------------------------------------
# BLP decoding
# ---------------------------------------------------------------------------
class BLPError(Exception):
    pass


def _rgb565(v):
    r = (v >> 11) & 31
    g = (v >> 5) & 63
    b = v & 31
    return np.stack(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)), -1)


def _dxt_color(blk, dxt1):
    """Decode the 8-byte colour half of a DXT block -> (rgb (n,16,3), a (n,16))."""
    n = len(blk)
    ends = np.ascontiguousarray(blk[:, :4]).view("<u2").astype(np.int32)
    c0, c1 = ends[:, 0], ends[:, 1]
    p0, p1 = _rgb565(c0), _rgb565(c1)
    four = (c0 > c1) | (not dxt1)          # DXT3/5 always use the 4-colour mode
    f = four[:, None]
    p2 = np.where(f, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(f, (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)                      # (n,4,3)
    a_pal = np.full((n, 4), 255, np.int32)
    a_pal[~four, 3] = 0                                      # DXT1 punch-through
    bits = np.ascontiguousarray(blk[:, 4:8]).view("<u4")[:, 0].astype(np.int64)
    idx = (bits[:, None] >> (2 * np.arange(16))) & 3         # (n,16)
    rows = np.arange(n)[:, None]
    return pal[rows, idx], a_pal[rows, idx]


def _dxt_alpha(blk, kind):
    """Alpha for DXT3 ('dxt3') or DXT5 blocks (8 bytes) -> (n,16)."""
    if kind == "dxt3":
        nib = np.stack((blk & 15, blk >> 4), -1).reshape(len(blk), 16)
        return nib.astype(np.int32) * 17
    a0 = blk[:, 0].astype(np.int32)
    a1 = blk[:, 1].astype(np.int32)
    bits = np.zeros(len(blk), np.uint64)
    for i in range(6):
        bits |= blk[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    idx = ((bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64)))
           & np.uint64(7)).astype(np.int64)
    t = np.zeros((len(blk), 8), np.int32)
    t[:, 0], t[:, 1] = a0, a1
    hi = a0 > a1
    for j in range(6):
        t[:, 2 + j] = np.where(hi, ((6 - j) * a0 + (1 + j) * a1) // 7,
                               np.where(j < 4, ((4 - j) * a0 + (1 + j) * a1) // 5,
                                        255 if j == 5 else 0))
    return t[np.arange(len(blk))[:, None], idx]


def _decode_dxt(data, off, w, h, kind):
    bw, bh = (w + 3) // 4, (h + 3) // 4
    bs = 8 if kind == "dxt1" else 16
    blk = np.frombuffer(data, np.uint8, bw * bh * bs, off).reshape(-1, bs)
    if kind == "dxt1":
        rgb, a = _dxt_color(blk, True)
    else:
        rgb, _ = _dxt_color(blk[:, 8:], False)
        a = _dxt_alpha(blk[:, :8], kind)
    px = np.concatenate([rgb, a[..., None]], -1).astype(np.uint8)   # (n,16,4)
    px = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return px[:h, :w]


def _decode_paletted(data, off, w, h, palette, alpha_bits):
    n = w * h
    idx = np.frombuffer(data, np.uint8, n, off)
    pal = np.frombuffer(palette, np.uint8, 1024).reshape(256, 4)
    out = np.empty((n, 4), np.uint8)
    out[:, :3] = pal[idx][:, 2::-1]                      # file order is B,G,R
    a_off = off + n
    if alpha_bits == 8:
        out[:, 3] = np.frombuffer(data, np.uint8, n, a_off)
    elif alpha_bits == 4:
        raw = np.frombuffer(data, np.uint8, (n + 1) // 2, a_off)
        a = np.empty(len(raw) * 2, np.uint8)
        a[0::2], a[1::2] = raw & 15, raw >> 4
        out[:, 3] = a[:n] * 17
    elif alpha_bits == 1:
        raw = np.frombuffer(data, np.uint8, (n + 7) // 8, a_off)
        out[:, 3] = np.unpackbits(raw, bitorder="little")[:n] * 255
    else:
        out[:, 3] = 255
    return out.reshape(h, w, 4)


def decode_blp(data):
    """Decode mip 0 of a BLP1/BLP2 file -> (width, height, uint8 RGBA (h,w,4))."""
    magic = bytes(data[:4])
    try:
        if magic == b"BLP2":
            _ver, enc, a_depth, a_enc, _mips, w, h = struct.unpack_from("<IBBBBII", data, 4)
            off = struct.unpack_from("<16I", data, 20)[0]
            palette = data[148:148 + 1024]
            if enc == 1:
                return w, h, _decode_paletted(data, off, w, h, palette, a_depth)
            if enc == 2:
                kind = {0: "dxt1", 1: "dxt3", 7: "dxt5"}.get(a_enc)
                if kind is None:
                    raise BLPError("unknown DXT alpha encoding %d" % a_enc)
                return w, h, _decode_dxt(data, off, w, h, kind)
            if enc == 3:
                bgra = np.frombuffer(data, np.uint8, w * h * 4, off).reshape(h, w, 4)
                return w, h, np.ascontiguousarray(bgra[..., [2, 1, 0, 3]])
            raise BLPError("unknown BLP2 encoding %d" % enc)
        if magic == b"BLP1":
            content, a_bits, w, h = struct.unpack_from("<4I", data, 4)
            off = struct.unpack_from("<16I", data, 28)[0]
            if content != 1:
                raise BLPError("JPEG-compressed BLP1 is not supported")
            return w, h, _decode_paletted(data, off, w, h, data[156:156 + 1024], a_bits)
    except (struct.error, ValueError) as exc:
        raise BLPError("truncated or corrupt BLP (%s)" % exc)
    raise BLPError("not a BLP file")


def write_png(path, rgba):
    """Write (h,w,4) uint8 as PNG (RGB when fully opaque). Atomic replace."""
    h, w, _ = rgba.shape
    opaque = bool((rgba[..., 3] == 255).all())
    pix = rgba[..., :3] if opaque else rgba
    ch = pix.shape[2]
    raw = np.zeros((h, w * ch + 1), np.uint8)
    raw[:, 1:] = pix.reshape(h, w * ch)

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2 if opaque else 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
           + chunk(b"IEND", b""))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(png)
    os.replace(tmp, path)


def blp_to_png(blp_path, png_path):
    with open(blp_path, "rb") as f:
        data = f.read()
    w, h, rgba = decode_blp(data)
    write_png(png_path, rgba)
    return w, h


# ---------------------------------------------------------------------------
# Locating files
# ---------------------------------------------------------------------------
class _FS:
    """Case-insensitive path lookup (loose extracts rarely match WoW's casing)."""

    def __init__(self):
        self._ls = {}

    def _list(self, d):
        if d not in self._ls:
            try:
                self._ls[d] = {n.lower(): n for n in os.listdir(d)}
            except OSError:
                self._ls[d] = {}
        return self._ls[d]

    def find(self, base, rel):
        cur = base
        for part in rel.replace("\\", "/").split("/"):
            if not part:
                continue
            name = self._list(cur).get(part.lower())
            if name is None:
                return None
            cur = os.path.join(cur, name)
        return cur if os.path.isfile(cur) else None

    def find_dir(self, base, rel):
        cur = base
        for part in rel.replace("\\", "/").split("/"):
            name = self._list(cur).get(part.lower())
            if name is None:
                return None
            cur = os.path.join(cur, name)
        return cur if os.path.isdir(cur) else None


def _read_listfile(path, wanted):
    """FileDataID -> path for the ids in ``wanted`` from an ``id;path`` file."""
    out = {}
    if not (path and wanted and os.path.isfile(path)):
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            i = line.find(";")
            if i <= 0:
                continue
            try:
                fid = int(line[:i])
            except ValueError:
                continue
            if fid in wanted:
                out[fid] = line[i + 1:].strip()
                if len(out) == len(wanted):
                    break
    return out


class TextureContext:
    """Per-import texture lookup, image cache and material wiring."""

    def __init__(self, m2_path, texture_root="", listfile="", file_ids=(), log=print):
        self.log = log
        self.fs = _FS()
        m2_dir = os.path.dirname(os.path.abspath(m2_path)) if m2_path else ""
        self.m2_dir = m2_dir
        self.m2_path = os.path.abspath(m2_path) if m2_path else ""
        self._runtime_cache = {}    # texture type -> file or None
        self.texture_root = texture_root or ""
        # The model's own folder and every folder above it come first, so a texture edited
        # next to the .m2 wins; the game export folder is the fallback.
        roots = []
        d = m2_dir
        for _ in range(8):
            if d and d not in roots:
                roots.append(d)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        if self.texture_root and self.texture_root not in roots:
            roots.append(self.texture_root)
        self.roots = roots
        self.id_paths = _read_listfile(listfile, set(int(i) for i in file_ids if i))
        if listfile:
            self.log("listfile: resolved %d of %d texture ids"
                     % (len(self.id_paths), len(set(i for i in file_ids if i))))
        self._cache = {}        # source path -> bpy image (or None)
        self._missing = set()
        self.done = set()       # material names already wired this import
        self.n_found = self.n_converted = self.n_runtime = 0

    # -- lookup -----------------------------------------------------------
    def _by_path(self, rel):
        stem = os.path.splitext(rel.replace("\\", "/"))[0]
        for root in self.roots:
            for ext in IMAGE_EXTS:
                hit = self.fs.find(root, stem + ext)
                if hit:
                    return hit
        return None

    def _by_id(self, fid):
        mapped = self.id_paths.get(fid)
        if mapped:
            hit = self._by_path(mapped)
            if hit:
                return hit
        for root in [r for r in (self.m2_dir, self.texture_root) if r]:
            for ext in IMAGE_EXTS:
                hit = self.fs.find(root, "%d%s" % (fid, ext))
                if hit:
                    return hit
        return None

    def _race_gender(self):
        """('NightElf', 'Female') from the model's path/name, or ('', '')."""
        p = self.m2_path.replace("\\", "/")
        m = re.search(r"character/([^/]+)/(male|female)/", p, re.I)
        if m:
            return m.group(1), m.group(2)
        stem = os.path.splitext(os.path.basename(p))[0]
        m = re.fullmatch(r"(.+?)(male|female)", stem, re.I)
        return (m.group(1), m.group(2)) if m else ("", "")

    # M2Texture.type -> (file-name regex, names to reject) for client-composited textures
    # The numbers may be followed by a suffix (Skin00_08_hd.blp), so match up to the extension.
    _TAIL = r"(\d+)_(\d+)(?:_[a-z0-9]+)*\."
    _RUNTIME_KINDS = {
        1: (r"skin" + _TAIL, ("extra", "naked")),
        6: (r"hair" + _TAIL, ("facial",)),
        7: (r"(?:facial|beard)\w*?" + _TAIL, ()),
        8: (r"skinextra" + _TAIL, ()),
        19: (r"eyes?" + _TAIL, ("glow", "reflect")),
    }

    def find_runtime_texture(self, ttype):
        """File for a client-supplied texture type (skin, hair, eyes, ...) or None.

        The M2 names no file for these; the game picks one from the race's customization
        set (Skin00_08, Hair01_03, Eyes00_02, ...). Any number is accepted: the lowest wins,
        looked for in the model's folder, the folder above it (where hair and eyes live)
        and Character/<Race>[/<Gender>] under every search root.
        """
        if ttype in self._runtime_cache:
            return self._runtime_cache[ttype]
        self._runtime_cache[ttype] = None
        spec = self._RUNTIME_KINDS.get(ttype)
        if spec is None:
            return None
        pat, reject = re.compile(spec[0]), spec[1]
        race, gender = self._race_gender()
        m2stem = os.path.splitext(os.path.basename(self.m2_path))[0].lower()
        prefix = (race + gender).lower()
        squash = lambda n: n.replace("_", "")
        folders = []

        def add(d):
            if d and d not in folders and os.path.isdir(d):
                folders.append(d)

        if self.m2_dir:
            add(self.m2_dir)
            add(os.path.dirname(self.m2_dir))
        for root in self.roots:
            if race:
                add(self.fs.find_dir(root, "Character/%s/%s" % (race, gender)))
                add(self.fs.find_dir(root, "Character/%s" % race))
        best = None
        for fi, folder in enumerate(folders):
            for low, name in self.fs._list(folder).items():
                m = pat.search(low)
                ext = os.path.splitext(low)[1]
                if not m or ext not in IMAGE_EXTS or any(r in low for r in reject):
                    continue
                same = squash(low).startswith(squash(m2stem)) or bool(prefix and squash(low).startswith(prefix))
                key = (fi, not same, IMAGE_EXTS.index(ext), int(m.group(1)), int(m.group(2)))
                if best is None or key < best[0]:
                    best = (key, os.path.join(folder, name))
        if best:
            self._runtime_cache[ttype] = best[1]
        else:
            seen = sorted({n for f in folders for n in self.fs._list(f).values()
                           if os.path.splitext(n)[1].lower() in IMAGE_EXTS
                           and any(w in n.lower() for w in ("skin", "hair", "eye"))})
            self.log("type %d texture: no match. searched %s; similar files: %s"
                     % (ttype, folders or "(no folders)", ", ".join(seen[:12]) or "none"))
        return self._runtime_cache[ttype]

    def find(self, filename="", fid=0):
        if filename:
            return self._by_path(filename)
        if fid:
            return self._by_id(fid)
        return None

    # -- images -----------------------------------------------------------
    def image_for(self, filename="", fid=0):
        """A bpy.types.Image for the texture, or None if it can't be found."""
        label = filename or ("FileDataID %d" % fid)
        src = self.find(filename, fid)
        if src is None:
            if label not in self._missing:
                self._missing.add(label)
                self.log("texture not found: %s" % label)
            return None
        return self.image_for_path(src)

    def image_for_path(self, src):
        """A bpy image for a located file (BLP converted to PNG first)."""
        if src in self._cache:
            return self._cache[src]
        import bpy
        path, pack = src, False
        if src.lower().endswith(".blp"):
            png = os.path.splitext(src)[0] + ".png"
            try:
                blp_to_png(src, png)
            except OSError:
                # Read-only source folder: convert in temp and pack the result.
                png = os.path.join(bpy.app.tempdir, os.path.basename(png))
                try:
                    blp_to_png(src, png)
                    pack = True
                except (OSError, BLPError) as exc:
                    self.log("could not convert %s: %s" % (src, exc))
                    self._cache[src] = None
                    return None
            except BLPError as exc:
                self.log("could not convert %s: %s" % (src, exc))
                self._cache[src] = None
                return None
            self.n_converted += 1
            self.log("converted %s -> %s" % (os.path.basename(src), os.path.basename(png)))
            path = png
        try:
            img = bpy.data.images.load(path, check_existing=True)
            if pack:
                img.pack()
        except RuntimeError as exc:
            self.log("could not load %s: %s" % (path, exc))
            img = None
        self._cache[src] = img
        if img is not None:
            self.n_found += 1
        return img

    # -- materials --------------------------------------------------------
    def apply(self, mat, layers, blend_mode):
        """Rebuild ``mat``'s node tree around the batch's texture layers.

        ``layers``: list of dicts ``filename``, ``fid``, ``type``, ``flags``,
        ``coord`` (UV set). Only the first layer that resolves drives Base
        Color; the rest are added as unconnected image nodes for reference,
        since how M2 combines layers depends on the shader id.
        """
        if mat.name in self.done:
            return
        self.done.add(mat.name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        bsdf.inputs["Roughness"].default_value = 1.0
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

        driven = False
        for i, layer in enumerate(layers):
            img = None
            if layer.get("type"):          # client-composited: skin, hair, eyes, ...
                src = self.find_runtime_texture(layer["type"])
                if src:
                    img = self.image_for_path(src)
                    if img is not None:
                        self.log("type %d texture: %s" % (layer["type"], os.path.basename(src)))
            if img is None and layer.get("type"):   # supplied by the client at runtime
                self.n_runtime += 1
                continue
            if img is None:
                img = self.image_for(layer.get("filename", ""), layer.get("fid", 0))
            if img is None:
                continue
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            tex.label = "Layer %d" % (i + 1)
            tex.extension = "REPEAT" if layer.get("flags", 0) & _WRAP else "EXTEND"
            tex.location = (-420, -300 * i)
            if layer.get("coord", 0) == 1:
                uv = nt.nodes.new("ShaderNodeUVMap")
                uv.uv_map = "UVMap2"
                uv.location = (-640, -300 * i)
                nt.links.new(uv.outputs["UV"], tex.inputs["Vector"])
            if driven:
                continue
            driven = True
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            if blend_mode >= _BLEND_ALPHA_KEY:
                alpha = tex.outputs["Alpha"]
                if blend_mode == _BLEND_ALPHA_KEY:      # alpha test, cutoff 0.5
                    gt = nt.nodes.new("ShaderNodeMath")
                    gt.operation = "GREATER_THAN"
                    gt.inputs[1].default_value = 0.5
                    gt.location = (-200, -200)
                    nt.links.new(alpha, gt.inputs[0])
                    alpha = gt.outputs["Value"]
                nt.links.new(alpha, bsdf.inputs["Alpha"])

    def summary(self):
        return ("textures: %d loaded (%d converted from BLP), %d missing, "
                "%d runtime-supplied layer(s) skipped"
                % (self.n_found, self.n_converted, len(self._missing), self.n_runtime))
