"""Group armour texture components into Type -> Armor -> Colour. No Blender needed.

File names look like ``cloth_a_01black_sleeve_au_u`` (classic) or
``plate_legiondungeon_c_03_holy_sleeve_au_u_5730784`` (retail), i.e.::

    <type> _ <name tokens> _ <NN><colour> [_ colour tokens] _ <piece> _ <region code> [_ gender] [_ fileid]

* **Type**   cloth / leather / mail / plate / robe / tabard / armor / other
* **Armor**  the name tokens plus ``NN`` (``plate_b_01``): every piece and colour of one set
* **Colour** whatever follows ``NN`` before the piece word (``black``, ``holy``, or none)

Files that don't follow the pattern (bare FileDataIDs, ...) land in *Other*,
one armor per file.
"""

import re

from . import outfit

TYPES = (
    ("cloth", "Cloth"), ("leather", "Leather"), ("mail", "Mail"), ("plate", "Plate"),
    ("robe", "Robe"), ("tabard", "Tabard"), ("armor", "Armor (generic)"), ("other", "Other"),
)
_TYPE_WORDS = {"cloth", "leather", "mail", "plate", "robe", "tabard", "armor"}
_PIECES = {"sleeve", "glove", "gloves", "chest", "pant", "pants", "boot", "boots", "belt",
           "bracer", "robe", "buckle", "tabard", "dress", "shirt"}
_CODES = {r[3] for r in outfit.REGIONS}
_NUM = re.compile(r"^(\d\d)([a-z]*)$")
_FID = re.compile(r"^\d{4,}$")
DEFAULT_COLOR = "~"           # enum id for "no colour token"


def parse(base, code=None):
    """``base`` file name (no extension/gender) -> (type, set_key, set_label, colour)."""
    t = base.lower().split("_")
    while len(t) > 1 and _FID.match(t[-1]):
        t.pop()
    if len(t) > 1 and t[-1] in ("m", "f", "u"):
        t.pop()
    if len(t) > 1 and t[-1] in _CODES:
        t.pop()
    if len(t) > 1 and t[-1] in ("m", "f", "u"):      # ..._tl_m_<fileid>
        t.pop()
    if len(t) > 1 and t[-1] in _CODES:
        t.pop()
    # type: first type word within the first three tokens
    kind, ti = "other", -1
    for i, tok in enumerate(t[:3]):
        if tok in _TYPE_WORDS:
            kind, ti = tok, i
            break
    rest = t[ti + 1:] if ti >= 0 else t
    # NN token splits name from colour
    ni = next((i for i, tok in enumerate(rest) if _NUM.match(tok)), -1)
    if ni >= 0:
        digits, letters = _NUM.match(rest[ni]).groups()
        name = rest[:ni] + [digits]
        tail = ([letters] if letters else []) + rest[ni + 1:]
        color = []
        for tok in tail:
            if tok in _PIECES:
                break
            if tok != "be" and tok not in ("m", "f", "u"):
                color.append(tok)
        return kind, "_".join(name) if ti < 0 else kind + "_" + "_".join(name), \
            " ".join(name).title(), "_".join(color)
    # no NN: name is everything before the piece word
    pi = next((i for i in range(len(rest) - 1, -1, -1) if rest[i] in _PIECES), len(rest))
    name = rest[:pi] or rest
    key = "_".join(name)
    label = " ".join(name).title()
    if name and _FID.match(name[0]):
        label = "FileDataID " + name[0]
    return kind, kind + "_" + key if ti >= 0 else key, label, ""


def _priority(base):
    """When a region has several pieces of one armor, prefer the main garment."""
    t = base.lower().split("_")
    if "glove" in t or "gloves" in t:
        return 1
    if {"bracer", "belt", "buckle"} & set(t):
        return 2
    return 0


class Index:
    """Everything found under a components folder."""

    def __init__(self, root):
        self.root = root
        # type -> set_key -> {"label", "colors": {color: {region: base name}}}
        self.sets = {k: {} for k, _ in TYPES}
        for key, *_ in outfit.REGIONS:
            for base in sorted(outfit.component_names(root, key), key=_priority):
                kind, skey, label, color = parse(base)
                s = self.sets[kind].setdefault(skey, {"label": label, "colors": {}})
                s["colors"].setdefault(color, {}).setdefault(key, base)

    def types(self):
        return [(k, label, "%d armors" % len(self.sets[k])) for k, label in TYPES if self.sets[k]]

    def armors(self, kind):
        return sorted(((skey, s["label"]) for skey, s in self.sets.get(kind, {}).items()),
                      key=lambda x: x[1])

    def colors(self, kind, skey):
        s = self.sets.get(kind, {}).get(skey)
        return sorted(s["colors"]) if s else []

    def regions(self, kind, skey, color):
        s = self.sets.get(kind, {}).get(skey)
        return dict(s["colors"].get(color, {})) if s else {}


_cache = {}


def get_index(root):
    if root not in _cache:
        _cache[root] = Index(root)
    return _cache[root]


def clear_cache():
    _cache.clear()
