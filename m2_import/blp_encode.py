"""Write BLP2 files (DXT1 / DXT5 with a full mip chain). Pure numpy, no Blender.

The encoder is a fast bounding-box compressor (min/max endpoints, nearest
palette index), which is plenty for armour textures and keeps a whole outfit
exporting in a moment. :func:`textures.decode_blp` reads the result back.
"""

import struct

import numpy as np

_HEADER = 148 + 1024     # fixed BLP2 header incl. the (unused) palette


def _to565(rgb):
    r = np.rint(rgb[:, 0] * 31 / 255).astype(np.int32)
    g = np.rint(rgb[:, 1] * 63 / 255).astype(np.int32)
    b = np.rint(rgb[:, 2] * 31 / 255).astype(np.int32)
    return (r << 11) | (g << 5) | b


def _from565(v):
    r = (v >> 11) & 31
    g = (v >> 5) & 63
    b = v & 31
    return np.stack(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)), -1)


def _color_blocks(px):
    """px (n,16,4) uint8 -> (n,8) uint8 DXT colour blocks (4-colour mode)."""
    n = len(px)
    rgb = px[..., :3].astype(np.float32)
    mx, mn = rgb.max(1), rgb.min(1)
    inset = (mx - mn) / 16.0                         # pull endpoints in a touch
    c0 = _to565(np.clip(mx - inset, 0, 255))
    c1 = _to565(np.clip(mn + inset, 0, 255))
    c0, c1 = np.maximum(c0, c1), np.minimum(c0, c1)  # c0 > c1 -> 4-colour mode
    p0, p1 = _from565(c0), _from565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], 1).astype(np.float32)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)      # (n,16,4)
    idx = d.argmin(-1).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))).sum(1, dtype=np.uint32)
    out = np.empty((n, 8), np.uint8)
    out[:, 0:2] = c0.astype("<u2").view(np.uint8).reshape(n, 2)
    out[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(n, 2)
    out[:, 4:8] = bits.astype("<u4").view(np.uint8).reshape(n, 4)
    return out


def _alpha_blocks(a):
    """a (n,16) uint8 -> (n,8) uint8 DXT5 alpha blocks (8-value mode)."""
    n = len(a)
    af = a.astype(np.float32)
    hi, lo = a.max(1), a.min(1)
    a0, a1 = hi.astype(np.float32), lo.astype(np.float32)
    t = np.stack([((7 - i) * a0 + i * a1) / 7.0 for i in range(8)], 1)  # (n,8)
    idx = np.abs(af[:, :, None] - t[:, None, :]).argmin(-1).astype(np.uint64)
    bits = (idx << (np.uint64(3) * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
    out = np.empty((n, 8), np.uint8)
    out[:, 0], out[:, 1] = hi, lo
    for i in range(6):
        out[:, 2 + i] = ((bits >> np.uint64(8 * i)) & np.uint64(255)).astype(np.uint8)
    return out


def _encode_dxt(rgba, alpha):
    h, w, _ = rgba.shape
    ph, pw = (h + 3) // 4 * 4, (w + 3) // 4 * 4
    if (ph, pw) != (h, w):
        rgba = np.pad(rgba, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    bh, bw = ph // 4, pw // 4
    px = (rgba.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * bw, 16, 4))
    col = _color_blocks(px)
    if not alpha:
        return col.tobytes()
    return np.concatenate([_alpha_blocks(px[..., 3]), col], 1).tobytes()


def _half(rgba):
    h, w, _ = rgba.shape
    nh, nw = max(h // 2, 1), max(w // 2, 1)
    if h % 2 == 0 and w % 2 == 0:
        f = rgba.astype(np.float32)
        f = (f[0::2, 0::2] + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2]) / 4.0
        return np.rint(f).astype(np.uint8)
    ys = (np.arange(nh) * h // nh)
    xs = (np.arange(nw) * w // nw)
    return rgba[ys][:, xs]


def encode_blp(rgba, mips=True):
    """uint8 RGBA (h,w,4) -> BLP2 bytes. Opaque images use DXT1, others DXT5."""
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w, _ = rgba.shape
    alpha = bool((rgba[..., 3] != 255).any())
    levels, cur = [], rgba
    while True:
        levels.append(_encode_dxt(cur, alpha))
        if not mips or (cur.shape[0] == 1 and cur.shape[1] == 1) or len(levels) == 16:
            break
        cur = _half(cur)
    offsets, sizes, pos = [0] * 16, [0] * 16, _HEADER
    for i, lv in enumerate(levels):
        offsets[i], sizes[i] = pos, len(lv)
        pos += len(lv)
    head = b"BLP2" + struct.pack("<IBBBBII", 1, 2, 8 if alpha else 0,
                                 7 if alpha else 0, 1 if len(levels) > 1 else 0, w, h)
    head += struct.pack("<16I", *offsets) + struct.pack("<16I", *sizes)
    head += bytes(1024)
    return head + b"".join(levels)
