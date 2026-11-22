"""Group armour texture components into Type -> Armor -> Colour. No Blender needed.

File names look like ``cloth_a_01black_sleeve_au_u`` (classic) or
``plate_legiondungeon_c_03_holy_sleeve_au_u_5730784`` (retail), i.e.::

    <type> _ <name tokens> _ <NN><colour> [_ colour tokens] _ <piece> _ <region code> [_ gender] [_ fileid]

* **Type**   cloth / leather / mail / plate / robe / tabard / armor / other
* **Armor**  the name tokens plus ``NN`` (``plate_b_01``): every piece and colour of one set
* **Colour** whatever follows ``NN`` before the piece word (``black``, ``holy``, or none)

Files that don't follow the pattern (bare FileDataIDs, ...) land in *Other*,
one armor per file.

On top of that, ``outfit_names.json`` (built by tools/build_outfit_names.py from the
game's item tables) names each armor and files it under Source (raid / dungeon / set /
other / unmapped) > Expansion. Without that file everything is simply *Unmapped*.
"""

import json
import os
import re
from collections import Counter

from . import outfit

TYPES = (
    ("cloth", "Cloth"), ("leather", "Leather"), ("mail", "Mail"), ("plate", "Plate"),
    ("robe", "Robe"), ("tabard", "Tabard"), ("armor", "Armor (generic)"), ("other", "Other"),
)
_TYPE_WORDS = {"cloth", "leather", "mail", "plate", "robe", "tabard", "armor"}
_PIECES = {"sleeve", "glove", "gloves", "chest", "pant", "pants", "boot", "boots", "belt",
           "bracer", "robe", "buckle", "tabard", "dress", "shirt", "combo"}
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


def _layer(base):
    """Paint order when several items share one region: garment, then boots/gloves/bracers, then belt."""
    t = set(base.lower().split("_"))
    if {"belt", "buckle"} & t:
        return 2
    if {"glove", "gloves", "boot", "boots", "bracer"} & t:
        return 1
    return 0


def _priority(base):
    """When a region has several pieces of one armor, prefer the main garment."""
    t = base.lower().split("_")
    if "glove" in t or "gloves" in t:
        return 1
    if {"bracer", "belt", "buckle"} & set(t):
        return 2
    return 0


SOURCES = (
    ("raid", "Raid"), ("dungeon", "Dungeon"), ("set", "Sets (other sources)"),
    ("other", "Other items"), ("unmapped", "Unmapped (file names only)"),
)
SOURCE_LABEL = dict(SOURCES)
TIERS = ("Classic", "Burning Crusade", "Wrath of the Lich King", "Cataclysm",
         "Mists of Pandaria", "Warlords of Draenor", "Legion", "Battle for Azeroth",
         "Shadowlands", "Dragonflight", "The War Within", "Midnight", "Current Season")
NO_TIER = "Unsorted"
_REGION_PREF = ("torso_upper", "torso_lower", "arm_upper", "leg_upper", "foot", "hand")
_names = []


def _load_names():
    if not _names:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outfit_names.json")
        try:
            with open(path, encoding="utf8") as f:
                _names.append(json.load(f))
        except (OSError, ValueError):
            _names.append({"entries": [], "tex": {}})
    return _names[0]


def _tier_rank(t):
    return TIERS.index(t) if t in TIERS else len(TIERS)


_STOP = {"the", "of", "a", "an", "and", "to"}


def _common_name(names):
    """Shared words of several piece names ('Glorious Belt', 'Glorious Gauntlets' -> 'Glorious'),
    taken from the front, else from the back ('... of the Highlord'). '' if there is none."""
    if len(names) < 2:
        return ""
    toks = [n.split() for n in names]
    for rev in (False, True):
        seq = [t[::-1] if rev else t for t in toks]
        common = []
        for words in zip(*seq):
            if len({w.lower() for w in words}) != 1:
                break
            common.append(words[0])
        while common and common[-1 if not rev else -1].lower() in _STOP:
            common.pop()
        if common and not all(w.lower() in _STOP for w in common) and sum(map(len, common)) > 3:
            return " ".join(common[::-1] if rev else common)
    return ""


class Index:
    """Everything found under a components folder.

    The unit is one *variant*: an armor set in one colour (``plate_horde_c_04`` + ``gold``).
    Colours of one file-name set are often unrelated items, so each is named and
    filed (source / expansion) on its own. The browser then lists, per source/expansion,
    the file-name sets that have a variant there, with those variants as the Colour list.
    """

    def __init__(self, root, mod=""):
        """``root``: folder or tuple of folders (first wins); ``mod``: the one of them that is a
        mod, so its outfits can be told apart."""
        self.root = root
        self.only_mod = False       # restrict every query to outfits the mod touches
        names = _load_names()
        entries, tex = names["entries"], names["tex"]
        self.base_item = {}     # component base name -> item name (for the region search)
        # gid ("kind/skey/color") -> {"kind","label","regions":{region: base},"source","tier","tip","find"}
        self.groups = {}
        hits = {}
        for key, *_ in outfit.REGIONS:
            modded = set(outfit.component_names(mod, key)) if mod else set()
            for base in sorted(outfit.component_names(root, key), key=_priority):
                kind, skey, label, color = parse(base)
                gid = "%s/%s/%s" % (kind, skey, color)
                g = self.groups.setdefault(gid, {"kind": kind, "label": label, "color": color, "regions": {},
                                                   "gkey": kind + "/" + skey})
                g["regions"].setdefault(key, []).append(base)
                if base in modded:
                    g["modded"] = True
                ei = tex.get(base)
                if ei is not None:
                    hits.setdefault(gid, {}).setdefault(key, []).append(entries[ei])
                    self.base_item[base] = entries[ei]["n"]
        for gid, g in self.groups.items():
            self._describe(g, hits.get(gid, {}))
        self._by = {}      # (source, tier) -> [gid]
        for gid, g in self.groups.items():
            self._by.setdefault((g["source"], g["tier"]), []).append(gid)

    @staticmethod
    def _describe(g, hits):
        colour = g["color"].replace("_", " ").title()
        flat = [e for k in _REGION_PREF for e in hits.get(k, [])] + \
               [e for k, v in hits.items() if k not in _REGION_PREF for e in v]
        if not flat:
            name = g["label"]
            label = name + (" - " + colour if colour else "")
            g.update(name=name, label=label, source="unmapped", tier=NO_TIER, find=label.lower() + " " + g["kind"],
                     tip=label)
            return
        src = ("raid" if any(e["c"] == "raid" for e in flat)
               else "dungeon" if any(e["c"] == "dungeon" for e in flat)
               else "set" if any(e["s"] for e in flat) else "other")
        pool = [e for e in flat if e["c"] == src] or flat
        setn = Counter(e["s"] for e in pool if e["s"]).most_common(1)
        inst = Counter(e["i"] for e in pool if e["i"]).most_common(1)
        tier = Counter(e["t"] for e in pool if e["t"]).most_common(1)
        label = setn[0][0] if setn else ""
        if not label:
            label = _common_name({e["n"] for e in flat})
            if label and g["kind"] in ("cloth", "leather", "mail", "plate"):
                label += " " + g["kind"].title()        # 'Glorious' -> 'Glorious Plate'
        label = label or pool[0]["n"]
        inst = inst[0][0] if inst else ""
        g["name"] = label + (" (%s)" % inst if inst else "")
        g["label"] = g["name"] + (" - " + colour if colour else "")
        g["source"] = src
        g["tier"] = tier[0][0] if tier else NO_TIER
        boss = next((e["b"] for e in pool if e["b"]), "")
        items = sorted({e["n"] for e in flat})
        g["tip"] = "; ".join(x for x in (
            label, inst, boss and "drops from " + boss, ", ".join(items[:4])) if x)
        g["find"] = " ".join([g["label"], g["kind"], g["tier"], SOURCE_LABEL[src]] + items).lower()

    # --- browsing -----------------------------------------------------------
    def _ok(self, gid, kind):
        g = self.groups[gid]
        return (kind in ("", "ALL") or g["kind"] == kind) and (g.get("modded") or not self.only_mod)

    def kinds(self):
        have = {g["kind"] for g in self.groups.values()}
        return [("ALL", "All types", "")] + [(k, label, "") for k, label in TYPES if k in have]

    def _gids(self, source, tier, kind="ALL"):
        """Variant ids in a source/expansion; ``ALL`` for either matches everything."""
        return [gid for (s, t), v in self._by.items()
                if source in ("ALL", "", s) and tier in ("ALL", "", t)
                for gid in v if self._ok(gid, kind)]

    def variants(self, source="ALL", tier="ALL", kind="ALL"):
        """Every variant id (armor + colour) in the filters, sorted by label."""
        return sorted(self._gids(source, tier, kind), key=lambda g: self.groups[g]["label"].lower())

    def sources(self, kind="ALL"):
        out = [("ALL", "All sources", "%d armors" % len(self._gids("ALL", "ALL", kind)))]
        for k, label in SOURCES:
            n = len(self._gids(k, "ALL", kind))
            if n:
                out.append((k, label, "%d armors" % n))
        return out

    def tiers(self, source, kind="ALL"):
        out = [("ALL", "All expansions", "%d armors" % len(self._gids(source, "ALL", kind)))]
        for t in sorted({t for s, t in self._by if source in ("ALL", "", s)}, key=_tier_rank):
            n = len(self._gids(source, t, kind))
            if n:
                out.append((t, t, "%d armors" % n))
        return out

    def _sets(self, source, tier, kind):
        """{file-name set: [variant ids]} for a source/expansion."""
        out = {}
        for gid in self._gids(source, tier, kind):
            out.setdefault(self.groups[gid]["gkey"], []).append(gid)
        return out

    def armors(self, source, tier, kind="ALL"):
        """[(set id, label, tip)]: one entry per file-name set; its colours are ``colors()``."""
        out = []
        for gkey, gids in self._sets(source, tier, kind).items():
            names = Counter(self.groups[g]["name"] for g in gids)
            top = sorted(names, key=lambda n: (-names[n], n))[0]
            g0 = self.groups[gids[0]]
            kname = dict(TYPES).get(g0["kind"], g0["kind"])
            label = top if top.lower().endswith(kname.lower()) else "%s - %s" % (top, kname)
            if len(gids) > 1:
                label += "  (%d colours)" % len(gids)
            out.append((gkey, label, "; ".join(sorted(names))[:300]))
        return sorted(out, key=lambda x: x[1].lower())

    def colors(self, source, tier, gkey):
        """[(variant id, label, tip)] of one set within a source/expansion."""
        out = []
        for gid in self._sets(source, tier, "ALL").get(gkey, []):
            g = self.groups[gid]
            colour = g["color"].replace("_", " ").title() or "Default"
            out.append((gid, "%s - %s" % (colour, g["name"]), g["tip"]))
        return sorted(out, key=lambda x: x[1].lower())

    def regions(self, gid):
        g = self.groups.get(gid)
        return {k: outfit.LAYER_SEP.join(sorted(v, key=_layer)) for k, v in g["regions"].items()} if g else {}

    def search(self, query, kind="ALL", source="ALL", tier="ALL", limit=200):
        """[(gid, display text)] of variants (within the type/source/expansion filters) whose
        label/set/item/instance/tier/type contains every word."""
        words = query.lower().split()
        pool = set(self._gids(source, tier, kind))
        hits = [(gid, self.groups[gid]) for gid in pool
                if all(w in self.groups[gid]["find"] for w in words)]
        hits.sort(key=lambda x: (_tier_rank(x[1]["tier"]), x[1]["label"].lower()))
        out, seen = [], set()
        for gid, g in hits[:limit]:
            text = "%s - %s  [%s / %s]" % (g["label"], dict(TYPES).get(g["kind"], g["kind"]),
                                           SOURCE_LABEL[g["source"]].split(" (")[0], g["tier"])
            if text in seen:
                text += " #" + gid.split("/", 1)[1]
            seen.add(text)
            out.append((gid, text))
        return out

    def match_bases(self, region_bases, query):
        """Component base names matching ``query`` by file name or by item name."""
        words = query.lower().split()
        return [b for b in region_bases
                if all(w in b.lower() or w in self.base_item.get(b, "").lower() for w in words)]


_cache = {}


def get_index(root, mod=""):
    key = (root, mod)
    if key not in _cache:
        _cache[key] = Index(tuple(r for r in (mod, root) if r), mod)
    return _cache[key]


def clear_cache():
    _cache.clear()
