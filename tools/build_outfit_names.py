"""Build m2_import/outfit_names.json from Wago DB2 CSV exports + the community listfile.

Usage: python tools/build_outfit_names.py [lists_dir] [out_json]

Chain: item -> ItemModifiedAppearance -> ItemAppearance -> ItemDisplayInfo
       -> ItemDisplayInfoMaterialRes -> TextureFileData -> listfile path -> component base name.
Category: raid/dungeon via JournalEncounterItem -> JournalEncounter -> JournalInstance -> Map.
Output maps each component base name (no extension, no _m/_f/_u) to the best-matching item.
"""
import csv, glob, json, os, re, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LISTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", ".docs", "Lists")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "m2_import", "outfit_names.json")
ARMOR = {1: "cloth", 2: "leather", 3: "mail", 4: "plate"}
EXPANSIONS = ["Classic", "Burning Crusade", "Wrath of the Lich King", "Cataclysm", "Mists of Pandaria",
              "Warlords of Draenor", "Legion", "Battle for Azeroth", "Shadowlands", "Dragonflight",
              "The War Within", "Midnight"]


def rows(table, build):
    hits = [p for p in glob.glob(os.path.join(LISTS, "%s.%s.*.csv" % (table, build)))
            if not re.search(r"-\d+\.csv$", p)]
    if not hits:
        return []
    with open(hits[0], encoding="utf8", newline="") as f:
        return list(csv.DictReader(f))


def component_bases():
    out = {}
    with open(os.path.join(LISTS, "community-listfile.csv"), encoding="utf8") as f:
        for line in f:
            fid, _, path = line.rstrip("\n").partition(";")
            if path.startswith("item/texturecomponents/") and path.endswith(".blp"):
                b = path.rsplit("/", 1)[1][:-4]
                if b[-2:-1] == "_" and b[-1] in "mfu":
                    b = b[:-2]
                out[int(fid)] = b
    return out


def main():
    bases = component_bases()
    # retail-only journal data
    tiers = {r["ID"]: r["Name_lang"] for r in rows("JournalTier", "12.1.7")}
    inst_tier = {r["JournalInstanceID"]: tiers.get(r["JournalTierID"], "") for r in rows("JournalTierXInstance", "12.1.7")}
    maptype = {r["ID"]: int(r["InstanceType"]) for r in rows("Map", "12.1.7")}
    inst = {}
    for r in rows("JournalInstance", "12.1.7"):
        kind = {1: "dungeon", 2: "raid"}.get(maptype.get(r["MapID"]), "other")
        inst[r["ID"]] = (r["Name_lang"], kind, inst_tier.get(r["ID"], ""))
    enc_inst = {r["ID"]: r["JournalInstanceID"] for r in rows("JournalEncounter", "12.1.7")}
    enc_name = {r["ID"]: r["Name_lang"] for r in rows("JournalEncounter", "12.1.7")}
    item_src = {}
    for r in rows("JournalEncounterItem", "12.1.7"):
        i = enc_inst.get(r["JournalEncounterID"])
        if i in inst:
            item_src.setdefault(r["ItemID"], (inst[i], enc_name.get(r["JournalEncounterID"], "")))

    cands = {}   # base -> (score, entry)
    users = defaultdict(set)   # base -> item names using it
    for build, full in (("1.60", "classic"), ("12.1.7", "retail")):
        tf = defaultdict(list)
        for r in rows("TextureFileData", build):
            tf[r["MaterialResourcesID"]].append(int(r["FileDataID"]))
        di = defaultdict(list)
        for r in rows("ItemDisplayInfoMaterialRes", build):
            di[r["ItemDisplayInfoID"]].append(r["MaterialResourcesID"])
        ap = {r["ID"]: r["ItemDisplayInfoID"] for r in rows("ItemAppearance", build)}
        ima_item = {}
        item_aps = defaultdict(set)
        for r in rows("ItemModifiedAppearance", build):
            item_aps[r["ItemID"]].add(r["ItemAppearanceID"])
            ima_item[r["ID"]] = r["ItemID"]
        sparse = rows("ItemSparse", build)
        name = {r["ID"]: r["Display_lang"] for r in sparse}
        xpac = {r["ID"]: int(r["ExpansionID"]) for r in sparse}
        set_names = {r["ID"]: r["Name_lang"] for r in rows("ItemSet", build)}
        sparse_set = {r["ID"]: set_names.get(r["ItemSet"], "") for r in sparse if r["ItemSet"] != "0"}
        sub = {r["ID"]: (int(r["ClassID"]), int(r["SubclassID"])) for r in rows("Item", build)}
        item_set = {}
        for r in rows("ItemSet", build):
            for k in range(17):
                it = r.get("ItemID_%d" % k)
                if it and it != "0":
                    item_set.setdefault(it, r["Name_lang"])
        for it, nm in sparse_set.items():
            if nm:
                item_set.setdefault(it, nm)
        tm_name = {r["ID"]: r["Name_lang"] for r in rows("TransmogSet", build)}
        for r in rows("TransmogSetItem", build):
            it = ima_item.get(r["ItemModifiedAppearanceID"])
            if it and tm_name.get(r["TransmogSetID"]):
                item_set.setdefault(it, tm_name[r["TransmogSetID"]])
        for item, aps in item_aps.items():
            if not name.get(item):
                continue
            cls, sc = sub.get(item, (0, 0))
            src = item_src.get(item)
            entry = {"n": name[item], "a": ARMOR.get(sc) if cls == 4 else "",
                     "s": item_set.get(item, ""),
                     "c": src[0][1] if src else "other",
                     "i": src[0][0] if src else "",
                     "t": src[0][2] if src else EXPANSIONS[xpac[item]] if 0 <= xpac.get(item, -1) < len(EXPANSIONS) else "",
                     "b": src[1] if src else ""}
            score = (2 if src else 0) + (1 if entry["s"] else 0) + (0.5 if full == "retail" else 0)
            for a in aps:
                for m in di.get(ap.get(a), ()):
                    for f in tf.get(m, ()):
                        b = bases.get(f)
                        if b:
                            users[b].add(name[item])
                        if b and (b not in cands or score > cands[b][0]):
                            cands[b] = (score, entry)
    # dedupe entries
    table, index, tex = [], {}, {}
    for b, (_, e) in sorted(cands.items()):
        key = json.dumps(e, sort_keys=True)
        if key not in index:
            index[key] = len(table)
            table.append(e)
        tex[b] = index[key]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf8") as f:
        json.dump({"entries": table, "tex": tex,
                   "shared": {b: len(u) for b, u in users.items() if len(u) > 1 and b in tex}}, f, separators=(",", ":"), ensure_ascii=False)
    from collections import Counter
    print("component bases in listfile:", len(set(bases.values())), "named:", len(tex), "entries:", len(table))
    print("by category:", Counter(e["c"] for e in (table[i] for i in tex.values())))
    print("size KB:", os.path.getsize(OUT) // 1024)


main()
