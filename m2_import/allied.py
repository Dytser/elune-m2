"""Allied-race export: write only the animations the child skeleton must own.

An allied race's ``.skel`` (SKPD) inherits every animation it does not define
from a parent race's ``.skel``. After import all inherited animations are merged
into the model so they can be seen and edited; on export, "Keep parent link" mode
writes just the child's own / override clips plus the inherited clips that were
edited, and leaves the rest to the parent.

No bpy here: the model is already plain data by the time it gets to this module.
"""

from . import skel as skel_mod
from . import writer
from .model import M2Event

CHILD_FLAG = 0x1000        # sequence overrides / extends the parent .skel
SKEL_ONLY_HEADER_FLAGS = 0x100000   # global flag: bones live in a .skel
DROP_HEADER_FLAGS = 0x2000 | 0x200000   # external .anim chunk flags (we embed)


def select_child_sequences(model):
    """Indices of the sequences a child .skel has to carry."""
    keep = []
    for i, s in enumerate(model.sequences):
        origin = getattr(s, "origin", "own")
        if origin in ("own", "override") or getattr(s, "edited", False):
            keep.append(i)
    return keep


def _source_event_rows(raw_pedc, src_idents):
    """PEDC rows keyed by event identifier (consumed in order for repeats)."""
    rows = skel_mod.parse_pedc(raw_pedc)
    by_ident = {}
    for ident, row in zip(src_idents, rows):
        by_ident.setdefault(ident, []).append(row)
    return by_ident


def build_pedc_chunk(model, parent_nseq, raw_pedc, src_idents):
    """PEDC chunk body: per event, per parent sequence timestamps.

    Starts from the source's PEDC; sequences that stayed inherited take the
    scene's (possibly edited) event timestamps."""
    by_ident = _source_event_rows(raw_pedc, src_idents)
    seqs = model.sequences
    rows = []
    for ev in model.events:
        pool = by_ident.get(ev.identifier) or []
        base = pool.pop(0) if pool else []
        row = [list(base[pi]) if pi < len(base) else [] for pi in range(parent_nseq)]
        for i, s in enumerate(seqs):
            if (getattr(s, "origin", "own") == "parent" and not getattr(s, "edited", False)
                    and 0 <= s.parent_index < parent_nseq):
                row[s.parent_index] = list(ev.timestamps[i]) if i < len(ev.timestamps) else []
        rows.append(row)
    return writer.pack_pedc(rows, parent_nseq)


def make_link_child(model, parent_nseq, raw_pedc, src_idents, log=print):
    """Turn the fully merged ``model`` into the child to write. Returns
    ``(pedc_chunk_bytes, written_names)``."""
    pedc = build_pedc_chunk(model, parent_nseq, raw_pedc, src_idents)

    seqs = model.sequences
    n_full = len(seqs)
    keep = select_child_sequences(model)
    newpos = {old: new for new, old in enumerate(keep)}
    edited = [i for i in keep if getattr(seqs[i], "origin", "own") == "parent"]

    kept = []
    for new, old in enumerate(keep):
        s = seqs[old]
        if getattr(s, "origin", "own") == "parent":
            s.origin = "override"           # an edited inherited clip becomes an override
        an = int(getattr(s, "alias_next", old))
        if an in newpos:
            s.alias_next = newpos[an]       # retail links variations / aliases both ways
        else:
            s.alias_next = new
            if s.flags & 0x40:
                s.flags &= ~0x40            # target stays in the parent: play own keys
        vn = int(getattr(s, "variation_next", -1))
        s.variation_next = newpos.get(vn, -1) if vn >= 0 else -1
        if getattr(s, "pure_alias", False):
            s.flags = ((s.flags | CHILD_FLAG) & ~0x20) & 0xFFFFFFFF
        else:
            s.flags = ((s.flags | 0x20 | CHILD_FLAG) & ~0x800) & 0xFFFFFFFF
        kept.append(s)

    for tr in _all_tracks(model):
        if tr.global_sequence >= 0 or len(tr.timelines) != n_full:
            continue
        tr.timelines = [tr.timelines[i] for i in keep]
    for ev in model.events:
        if len(ev.timestamps) == n_full:
            ev.timestamps = [ev.timestamps[i] for i in keep]

    model.sequences = kept
    lookup = {}
    for i, s in enumerate(kept):
        lookup.setdefault(s.id, i)
    model.seq_lookup = [lookup.get(i, 0xFFFF)
                        for i in range(max(lookup) + 1 if lookup else 1)]
    names = ["%d-%d" % (s.id, s.variation_index) for s in kept]
    log("[M2] allied export: writing %d of %d sequences to the child .skel "
        "(%d inherited clip(s) edited -> overrides)" % (len(kept), n_full, len(edited)))
    return pedc, names


def _all_tracks(model):
    for b in model.bones:
        yield from (t for t in (b.translation, b.rotation, b.scale) if t is not None)
    for group in (model.colors, model.transforms):
        for tup in group:
            yield from (t for t in tup if t is not None)
    yield from (t for t in model.weights if t is not None)
    for c in model.cameras:
        yield from (t for t in (c.position, c.target, c.roll, c.fov) if t is not None)
