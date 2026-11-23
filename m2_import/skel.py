"""Parsing of external ``.skel`` skeleton files (Legion+)."""

import os
import struct

from .reader import BinaryReader, scan_chunks
from .model import (M2Bone, M2Sequence, M2Attachment, M2AnimTrack, M2Model,
                    repair_zero_blend_times)
from . import anim_data

# Modern struct sizes (the .skel format is Legion+, always modern).
_SEQUENCE_SIZE = 64
_BONE_TRACK_SIZE = 20


def find_skel_file(m2_path):
    """Return ``<model>.skel`` next to the ``.m2`` if it exists, else None."""
    base = m2_path[:-3] if m2_path.lower().endswith(".m2") else m2_path
    candidate = base + ".skel"
    return candidate if os.path.isfile(candidate) else None


def _chunk_reader(data, chunks, name):
    """A reader whose base is the named chunk's data start, or None."""
    if name not in chunks:
        return None
    off, _size = chunks[name]
    r = BinaryReader(data, base=off)
    r.seek(off)                  # header arrays sit at the chunk data start
    return r


def _read_sequences(reader, arr, data_len):
    if arr.count == 0:
        return []
    if arr.base_offset(reader) + arr.count * _SEQUENCE_SIZE > data_len:
        print("[M2] warning: .skel sequence table overruns file.")
        return []

    def one(r):
        start = r.tell()
        s = M2Sequence()
        s.id = r.u16()
        s.variation_index = r.u16()
        s.duration = r.u32()
        s.end_timestamp = s.duration
        s.movespeed = r.f32()
        s.flags = r.u32()
        s.frequency = r.i16()
        r.u16()                  # padding
        r.u32(); r.u32()         # replay range
        s.blend_time_in = r.u16()
        s.blend_time_out = r.u16()
        bmin = (r.f32(), r.f32(), r.f32())
        bmax = (r.f32(), r.f32(), r.f32())
        s.bounds = (bmin, bmax, r.f32())
        s.variation_next = r.i16()
        s.alias_next = r.u16()
        r.seek(start + _SEQUENCE_SIZE)
        return s

    return repair_zero_blend_times(arr.read(reader, one))


def _read_attachments(reader, arr):
    if arr.count == 0:
        return []

    def one(r):
        a = M2Attachment()
        a.id = r.u32()
        a.bone = r.u16()
        r.u16()                  # unknown
        a.position = r.vec3()
        r.skip(_BONE_TRACK_SIZE)  # animate_attached M2Track header
        return a

    return arr.read(reader, one)


def _read_bones(reader, arr, sequences, loader):
    vec3, quat = anim_data.make_value_readers(False)

    def one(r):
        b = M2Bone()
        b.key_bone_id = r.i32()
        b.flags = r.u32()
        b.parent = r.i16()
        b.submesh_id = r.u16()
        b.name_crc = r.u32()     # boneNameCRC (always present in skel)
        b.translation = anim_data.read_track(r, False, vec3, sequences, loader)
        b.rotation = anim_data.read_track(r, False, quat, sequences, loader)
        b.scale = anim_data.read_track(r, False, vec3, sequences, loader)
        b.pivot = r.vec3()
        return b

    return arr.read(reader, one)


def load_skel(skel_path, model, name_base):
    """Populate ``model`` bones/sequences/attachments/global loops from a skel."""
    with open(skel_path, "rb") as f:
        data = f.read()
    chunks = scan_chunks(data)
    loader = anim_data.AnimLoader(name_base)

    # Raw SKL1 / SKPD / BFID, kept so an allied-race export can write them back.
    model.skel_chunks = {}
    for name in (b"SKL1", b"SKPD", b"BFID"):
        if name in chunks:
            off, size = chunks[name]
            model.skel_chunks[name.decode("ascii")] = data[off:off + size]

    # SKS1 first: bone track decoding needs the sequence flags to tell resident
    # animations from external ones.
    r = _chunk_reader(data, chunks, b"SKS1")
    if r is not None:
        a_global = r.m2array()
        a_seq = r.m2array()
        a_lookup = r.m2array()  # sequence_lookups (animation lookup table)
        model.global_loops = a_global.read_u32(r)
        model.sequences = _read_sequences(r, a_seq, len(data))
        model.seq_lookup = a_lookup.read_u16(r)

    r = _chunk_reader(data, chunks, b"SKB1")
    if r is not None:
        a_bones = r.m2array()
        a_kbl = r.m2array()      # key_bone_lookup - REQUIRED for character bake
        model.bones = _read_bones(r, a_bones, model.sequences, loader)
        # Populate key_bone_lookup from .skel. Without this, the character
        model.key_bone_lookup = a_kbl.read_u16(r)

    r = _chunk_reader(data, chunks, b"SKA1")
    if r is not None:
        a_att = r.m2array()
        a_att_lookup = r.m2array()   # attachment_lookup_table
        model.attachments = _read_attachments(r, a_att)
        model.attachment_lookup = a_att_lookup.read_u16(r)

    print("[M2] skeleton: %d bones, %d sequences, %d attachments"
          % (len(model.bones), len(model.sequences), len(model.attachments)))


# ---------------------------------------------------------------------------
# Allied races: SKPD parent skeleton
# ---------------------------------------------------------------------------
# Set by the import operator ("Parent Skeleton" option); used before any search.
parent_skel_override = ""

_listfile_cache = {}


def parent_fid_of(model):
    """FileDataID of the parent .skel (SKPD bytes 8..12), or 0."""
    raw = (getattr(model, "skel_chunks", None) or {}).get("SKPD")
    if raw and len(raw) >= 12:
        return struct.unpack_from("<I", raw, 8)[0]
    return 0


def _listfile_path(fid):
    """Relative path for ``fid`` from the community listfile, or ''."""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    try:                                  # Listfile preference (add-on preferences)
        from . import prefs
        if prefs.path("listfile"):
            cands.append(prefs.path("listfile"))
    except Exception:  # noqa: BLE001 - not running inside Blender
        pass
    for rel in ((".." , ".docs", "Lists", "community-listfile.csv"),
                (".docs", "Lists", "community-listfile.csv")):
        cands.append(os.path.normpath(os.path.join(here, *rel)))
    for lf in cands:
        if not os.path.isfile(lf):
            continue
        key = "%d;" % fid
        try:
            with open(lf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith(key):
                        return line[len(key):].strip()
        except OSError:
            pass
    return ""


def find_parent_skel(child_skel_path, fid):
    """Locate the parent .skel for ``fid`` near the child's export root."""
    if parent_skel_override and os.path.isfile(parent_skel_override):
        return parent_skel_override
    cached = _listfile_cache.get(fid)
    if cached and os.path.isfile(cached):
        return cached
    child_dir = os.path.dirname(os.path.abspath(child_skel_path))
    # wow.export / CASC layout: <root>/character/<race>/<gender>/<file>
    roots = []
    up = child_dir
    for _ in range(6):
        roots.append(up)
        up = os.path.dirname(up)
    rel = _listfile_path(fid)
    if rel:
        for r in roots:
            cand = os.path.join(r, rel.replace("/", os.sep))
            if os.path.isfile(cand):
                _listfile_cache[fid] = cand
                return cand
    # Manifests that wow.export writes next to each model name their skeleton
    # FID. Search the sibling race folders under the same character/ directory.
    import json
    scan_root = os.path.dirname(os.path.dirname(child_dir))
    try:
        for dirpath, _dirs, files in os.walk(scan_root):
            for fn in files:
                if not fn.lower().endswith(".manifest.json"):
                    continue
                try:
                    with open(os.path.join(dirpath, fn), "r", encoding="utf-8") as f:
                        man = json.load(f)
                except (OSError, ValueError):
                    continue
                sk = man.get("skeleton") or {}
                if int(sk.get("fileDataID", 0) or 0) == fid and sk.get("file"):
                    cand = os.path.join(dirpath, sk["file"])
                    if os.path.isfile(cand):
                        _listfile_cache[fid] = cand
                        return cand
    except OSError:
        pass
    return ""


def parse_psbc(raw):
    """PSBC -> [((min3), (max3), radius)] per parent sequence."""
    if not raw or len(raw) < 16:
        return []
    count, off = struct.unpack_from("<II", raw, 0)
    out = []
    for i in range(count):
        p = off + i * 28
        if p + 28 > len(raw):
            break
        v = struct.unpack_from("<7f", raw, p)
        out.append((tuple(v[0:3]), tuple(v[3:6]), v[6]))
    return out


def parse_pedc(raw):
    """PEDC -> per event, per parent sequence: list of timestamps."""
    if not raw or len(raw) < 16:
        return []
    n_events, off = struct.unpack_from("<II", raw, 0)
    out = []
    for e in range(n_events):
        _it, _gs, cnt, toff = struct.unpack_from("<HhII", raw, off + e * 12)
        per_seq = []
        for si in range(cnt):
            c, o = struct.unpack_from("<II", raw, toff + si * 8)
            per_seq.append(list(struct.unpack_from("<%dI" % c, raw, o)) if c else [])
        out.append(per_seq)
    return out


def _pick_track(child, parent, merged_index):
    """Merge one bone track. ``merged_index`` = [(origin, own_idx)] per sequence.

    The child .skel is what gets re-exported, so its track layout wins: a child
    global-sequence track is kept as is, otherwise the track is per-sequence and
    each sequence takes its keys from whichever skeleton owns it. (A parent
    global track under a plain child track has no per-sequence keys to inherit.)"""
    t = M2AnimTrack()
    if child is not None and child.global_sequence >= 0:
        t.interpolation_type = child.interpolation_type
        t.global_sequence = child.global_sequence
        t.timelines = [list(x) for x in child.timelines]
        return t
    c_data = child is not None and not child.is_empty()
    p_plain = parent is not None and parent.global_sequence < 0
    if c_data or not p_plain:
        t.interpolation_type = child.interpolation_type if child is not None else 0
    else:
        t.interpolation_type = parent.interpolation_type
    t.global_sequence = -1
    for origin, idx in merged_index:
        src = parent if origin == "parent" else child
        tl = []
        if (src is not None and src.global_sequence < 0
                and 0 <= idx < len(src.timelines)):
            tl = list(src.timelines[idx])
        t.timelines.append(tl)
    return t


def merge_parent(model, parent):
    """Fold ``parent`` (an M2Model loaded from the parent .skel) into the child
    ``model`` so every inherited animation is present. Returns True if merged."""
    if len(model.bones) != len(parent.bones):
        print("[M2] parent skeleton has %d bones, child %d: not merging animations"
              % (len(parent.bones), len(model.bones)))
        return False
    for i, (cb, pb) in enumerate(zip(model.bones, parent.bones)):
        if cb.name_crc != pb.name_crc:
            print("[M2] parent skeleton bone %d differs (CRC): not merging animations" % i)
            return False
    if list(parent.global_loops) != list(model.global_loops)[:len(parent.global_loops)]:
        print("[M2] warning: parent global sequences are not a prefix of the child's")

    child_seqs = list(model.sequences)
    parent_seqs = list(parent.sequences)
    ckey = {}
    for ci, s in enumerate(child_seqs):
        ckey.setdefault((s.id, s.variation_index), ci)

    merged, merged_index = [], []
    child_map = {}                      # child index -> merged index
    used_child = set()
    for pi, ps in enumerate(parent_seqs):
        ci = ckey.get((ps.id, ps.variation_index))
        if ci is not None and ci not in used_child:
            s = child_seqs[ci]
            s.origin, s.parent_index = "override", pi
            child_map[ci] = len(merged)
            used_child.add(ci)
            merged_index.append(("child", ci))
        else:
            s = ps
            s.origin, s.parent_index = "parent", pi
            merged_index.append(("parent", pi))
        merged.append(s)
    for ci, s in enumerate(child_seqs):
        if ci in used_child:
            continue
        s.origin, s.parent_index = "own", -1
        child_map[ci] = len(merged)
        merged_index.append(("child", ci))
        merged.append(s)

    # Child sequences link by child index: remap into the merged table.
    for i, s in enumerate(merged):
        if s.origin == "parent":
            continue
        if 0 <= s.variation_next < len(child_seqs):
            s.variation_next = child_map.get(s.variation_next, -1)
        an = int(getattr(s, "alias_next", i))
        s.alias_next = child_map.get(an, i) if an < len(child_seqs) else i

    # Per-parent-sequence boxes (PSBC) and events (PEDC) from the child .m2.
    aux = model.aux_chunks or {}
    boxes = parse_psbc(aux.get("PSBC"))
    model.parent_bounds = boxes
    for s in merged:
        if s.origin == "parent" and 0 <= s.parent_index < len(boxes):
            s.bounds = boxes[s.parent_index]
    pedc = parse_pedc(aux.get("PEDC"))
    for ei, ev in enumerate(model.events):
        own = list(ev.timestamps)
        stamps = []
        for origin, idx in merged_index:
            if origin == "parent":
                src = pedc[ei][idx] if ei < len(pedc) and idx < len(pedc[ei]) else []
            else:
                src = own[idx] if idx < len(own) else []
            stamps.append(list(src))
        ev.timestamps = stamps

    for cb, pb in zip(model.bones, parent.bones):
        cb.translation = _pick_track(cb.translation, pb.translation, merged_index)
        cb.rotation = _pick_track(cb.rotation, pb.rotation, merged_index)
        cb.scale = _pick_track(cb.scale, pb.scale, merged_index)

    model.sequences = merged
    lookup = {}
    for i, s in enumerate(merged):
        lookup.setdefault(s.id, i)
    model.seq_lookup = [lookup.get(i, 0xFFFF)
                        for i in range(max(lookup) + 1 if lookup else 1)]
    return True


def load_with_parents(skel_path, model, name_base, depth=0):
    """``load_skel`` plus the SKPD parent chain merged in. Returns the model."""
    load_skel(skel_path, model, name_base)
    fid = parent_fid_of(model)
    if not fid or depth > 4:
        return model
    ppath = find_parent_skel(skel_path, fid)
    if not ppath:
        print("[M2] .skel has parent FileDataID %d but the parent .skel was not found: "
              "only this model's own animations are loaded (set 'Parent Skeleton' "
              "on import)" % fid)
        return model
    print("[M2] parent skeleton %s" % os.path.basename(ppath))
    parent = M2Model()
    pbase = ppath[:-5] if ppath.lower().endswith(".skel") else ppath
    load_with_parents(ppath, parent, pbase, depth + 1)
    n_child = len(model.sequences)
    if merge_parent(model, parent):
        model.parent_skel = {"fid": fid, "path": ppath, "nseq": len(parent.sequences),
                             "name": os.path.basename(pbase)}
        print("[M2] merged parent animations: %d own/override + %d inherited = %d sequences"
              % (n_child, len(model.sequences) - n_child, len(model.sequences)))
    return model
