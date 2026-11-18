"""Readable, Blender-mirror-friendly bone names from the M2 key-bone IDs.

The M2 ``boneIndicesById`` (key_bone_lookup) table maps a well-known key-bone ID
(see ``E_BoneIndicesID`` in M2.bt) to a bone index. Bones listed there get a
real name; the rest keep the generic ``bone_NNN`` name. Left/right bones use a
``_L`` / ``_R`` suffix so Blender's X-mirror tools work on them.

Every bone also gets ``m2_bone_index`` stamped on it at import: the exporters
use that (via :func:`bone_index`) instead of parsing the name.
"""

import re

from . import bone_crc_names

GENERIC_FMT = "bone_%03d"
INDEX_PROP = "m2_bone_index"

# key-bone ID -> Blender bone name
KEY_BONE_NAMES = {
    0: "Arm_L", 1: "Arm_R",
    2: "Shoulder_L", 3: "Shoulder_R",
    4: "SpineLow", 5: "Waist", 6: "Head", 7: "Jaw",
    8: "IndexFinger_R", 9: "MiddleFinger_R", 10: "PinkyFinger_R",
    11: "RingFinger_R", 12: "Thumb_R",
    13: "IndexFinger_L", 14: "MiddleFinger_L", 15: "PinkyFinger_L",
    16: "RingFinger_L", 17: "Thumb_L",
    18: "BTH", 19: "CSR", 20: "CSL",
    21: "Breath", 22: "Name", 23: "NameMount", 24: "CHD", 25: "CCH",
    26: "Root",
    27: "Wheel1", 28: "Wheel2", 29: "Wheel3", 30: "Wheel4",
    31: "Wheel5", 32: "Wheel6", 33: "Wheel7", 34: "Wheel8",
    35: "FaceAttenuation",
    36: "CapeParent", 37: "CapeChild1", 38: "CapeChild2",
    39: "CapeChild3", 40: "CapeChild4",
    41: "Unk_41", 42: "Unk_42",
    43: "TabardParent", 44: "TabardChild1", 45: "TabardChild2",
    46: "HeadTop1", 47: "HeadTop2",
    48: "UpperBodyParent", 49: "NeckParent", 50: "NeckChild1",
    51: "LowerBodyParent", 52: "Buckle", 53: "Chest", 54: "Main",
    55: "Leg_R", 56: "Leg_L",
    57: "Knee_R", 58: "Knee_L",
    59: "Foot_L", 60: "Foot_R",
    61: "Elbow_R", 62: "Elbow_L",
    63: "Elbow_child_L",
    64: "Hand_R", 65: "Hand_L",
    66: "Weapon_R", 67: "Weapon_L",
    68: "Wrist_child_L", 69: "Wrist_child_R",
    70: "Knee_upper_R", 71: "Knee_upper_L",
    72: "Arm2_R", 73: "Arm2_L",
    74: "Elbow_upper_R", 75: "Elbow_upper_L",
    76: "Forearm_R", 77: "Forearm_L",
    78: "Wrist_upper_R", 79: "Wrist_upper_L",
}

_GENERIC_RE = re.compile(r"bone_(\d+)")


# Attachment id -> key-bone style name. Only the body-part mounts (the ones that
# sit on a specific, sided bone) are used; see the M2 attachment id table.
ATTACH_BONE_NAMES = {
    1: "Hand_R", 2: "Hand_L",
    3: "Elbow_R", 4: "Elbow_L",
    5: "Shoulder_R", 6: "Shoulder_L",
    7: "Knee_R", 8: "Knee_L",
    9: "Hip_R", 10: "Hip_L",
}

# Name for a bone found structurally above a Forearm. "UpArm" matches what the
# succubus's own bones are called (confirmed by CRC); change it here if you'd
# rather have UpperArm.
UPPER_ARM_STEM = "UpArm"

# Real-name stems that read badly as a prefix for inferred chains.
_STEM_ALIASES = {"SpineLow": "Spine", "SpineMid": "Spine", "SpineUp": "Spine",
                 "Waist": "Spine", "Chest": "Spine", "UpperBodyParent": "Spine",
                 "LowerBodyParent": "Pelvis", "NeckParent": "Neck",
                 "NeckChild1": "Neck",
                 "Root": "Body", "Main": "Body"}


def compute_bone_names(model, infer=False, normalize=False):
    """Return a list with one unique Blender bone name per M2 bone."""
    return compute_bone_names_ex(model, infer, normalize)[0]


def compute_bone_names_ex(model, infer=False, normalize=False):
    """Like :func:`compute_bone_names`, but returns ``(names, inferred, orig)``.

    ``inferred`` is the set of bone indices whose name was guessed or recovered
    rather than read from the file (empty unless ``infer`` is True). ``orig``
    maps bone index -> the name it had before ``normalize`` renamed it.
    """
    names = _real_names(model)
    inferred = set()
    orig = {}
    if infer:
        try:
            inferred = _infer_names(model, names)
        except Exception as e:  # noqa: BLE001 - a guess must never break import
            print("[M2] bone name inference failed: %r" % (e,), flush=True)
            names = _real_names(model)
            inferred = set()
    if normalize:
        try:
            orig = _normalize_body(model, names, inferred)
        except Exception as e:  # noqa: BLE001
            print("[M2] bone name normalization failed: %r" % (e,), flush=True)
            orig = {}
    return names, inferred, orig


# Canonical body names, so meshes and weights port between models by name.
SPINE_FMT = "Spine_%02d"      # Spine_01 = lowest spine bone, counting up
NECK_FIRST = "Neck"
NECK_FMT = "Neck_%02d"        # second neck bone onwards: Neck_02, Neck_03, ...
PELVIS_NAME = "Pelvis"
_ROOTISH_NAMES = {"Root", "Main"}
_ROOTISH_KEYS = {26, 54}      # key-bone ids: Root, Main
_KEY_HEAD = 6
_KEY_LOWER_BODY_PARENT = 51
_KEY_ARM_SHOULDER = {0, 1, 2, 3}


def _normalize_body(model, names, inferred):
    """Rename the spine, neck and pelvis to fixed names. Returns {index: old name}.

    Works from the hierarchy, not the stored names, because rigs disagree on
    names (SpineLow/SpineMid/SpineUp, or SpineLow/UpperBodyParent/Chest, with
    helper "Parent" bones inside the chain):

    * spine path = parents from ``Head`` up to the root, minus Root/Main;
    * top of the spine = the highest path bone whose children include the
      arm/shoulder/wing pair (a mirrored pair that does not hang below it);
    * spine = root side up to and including that bone -> Spine_01, Spine_02, ...
    * neck  = between that bone and the head -> Neck, Neck_02, ...
    * pelvis = a bone off the spine path that the leg pair hangs from, if the
      model has one (otherwise the legs hang straight off the spine: no Pelvis).

    If the head or the top of the spine can't be found nothing is renamed.
    """
    bones = model.bones
    n = len(bones)
    if n == 0:
        return {}
    P, parent, _tol, partner = _mirror_partners(bones)

    head = next((i for i, b in enumerate(bones) if b.key_bone_id == _KEY_HEAD), None)
    if head is None:
        head = next((i for i, nm in enumerate(names) if nm == "Head"), None)
    if head is None:
        return {}

    path, seen, i = [], set(), head
    while i != -1 and i not in seen:
        seen.add(i)
        path.append(i)
        i = parent[i]
    path.reverse()                                   # root ... head
    start = 0
    while start < len(path) - 1 and (
            names[path[start]] in _ROOTISH_NAMES
            or bones[path[start]].key_bone_id in _ROOTISH_KEYS):
        start += 1
    body = path[start:-1]                            # spine + neck, no root, no head
    if not body:
        return {}

    children = {}
    for b in range(n):
        children.setdefault(parent[b], []).append(b)

    def carries_arms(p):
        cs = children.get(p, [])
        cset = set(cs)
        for c in cs:
            if bones[c].key_bone_id in _KEY_ARM_SHOULDER:
                return True
            if c in partner and partner[c] in cset and P[c][2] >= P[p][2]:
                return True
        return False

    top = None
    for k, b in enumerate(body):
        if carries_arms(b):
            top = k
    if top is None:
        return {}
    spine, neck = body[:top + 1], body[top + 1:]

    plan = {}
    for k, b in enumerate(spine):
        plan[b] = SPINE_FMT % (k + 1)
    for k, b in enumerate(neck):
        plan[b] = NECK_FIRST if k == 0 else NECK_FMT % (k + 1)

    # Pelvis: a separate bone the legs hang from (not on the spine path).
    on_path = set(path)
    cands = []
    for p in range(n):
        if p in on_path or parent[p] not in on_path:
            continue
        cs = children.get(p, [])
        cset = set(cs)
        if any(c in partner and partner[c] in cset and P[c][2] < P[p][2] for c in cs):
            cands.append(p)
    keyed = [p for p in cands if bones[p].key_bone_id == _KEY_LOWER_BODY_PARENT]
    pick = keyed or cands
    if pick:
        plan[pick[0]] = PELVIS_NAME

    # Apply. Free the planned bones' current names first so they can swap.
    holder = {nm: b for b, nm in enumerate(names)}
    for b in plan:
        if holder.get(names[b]) == b:
            del holder[names[b]]
    orig = {}
    for b, new in plan.items():
        j = holder.get(new)
        if j is not None and j != b:
            if j in inferred:                        # a guess loses to a rule
                names[j] = GENERIC_FMT % j
                inferred.discard(j)
                del holder[new]
            else:
                print("[M2] bone name '%s' already used by bone %d: bone %d keeps "
                      "'%s'" % (new, j, b, names[b]), flush=True)
                holder[names[b]] = b
                continue
        if names[b] != new:
            orig[b] = names[b]
            names[b] = new
        holder[new] = b
    return orig


def _split_side(name):
    """'Arm_L' -> ('Arm', 'L'); 'Spine_03' -> ('Spine_03', '')."""
    m = re.fullmatch(r"(.+?)_([LR])(?:_\d+)?", name)
    if m:
        return m.group(1), m.group(2)
    return name, ""


def _mirror_partners(bones):
    """Find left/right bone pairs: ``(pivots, parents, tolerance, partner map)``.

    A pair is two bones whose pivots mirror across the Y=0 plane and whose
    parents are the same bone or a pair themselves.
    """
    n = len(bones)
    P = [tuple(b.pivot) for b in bones]
    parent = [b.parent if 0 <= b.parent < n else -1 for b in bones]
    extent = max((max(p[a] for p in P) - min(p[a] for p in P)) for a in range(3)) or 1.0
    tol = extent * 0.004

    cand = {}
    for i in range(n):
        x, y, z = P[i]
        if abs(y) <= tol:
            continue
        best, bd = None, None
        for j in range(n):
            if j == i:
                continue
            xj, yj, zj = P[j]
            if abs(xj - x) > tol or abs(zj - z) > tol or abs(yj + y) > tol:
                continue
            d = abs(xj - x) + abs(zj - z) + abs(yj + y)
            if bd is None or d < bd:
                best, bd = j, d
        if best is not None:
            cand[i] = best
    partner = {i: j for i, j in cand.items() if cand.get(j) == i}
    # Parents must be the same bone or partners of each other, else it's a
    # coincidence of positions rather than a real left/right pair.
    changed = True
    while changed:
        changed = False
        for i in list(partner):
            j = partner[i]
            pi, pj = parent[i], parent[j]
            if pi == pj or partner.get(pi) == pj:
                continue
            del partner[i]
            changed = True
    return P, parent, tol, partner


def _infer_names(model, names):
    """Name unnamed bones from mirror pairs, attachments and the hierarchy.

    Conservative on purpose: a bone only gets a guessed name if it has a
    mirror partner (same pivot with Y negated, parents consistent) or lies on
    the midline path that leads up to such bones. Everything else (tails,
    capes, hair, props, ...) keeps its generic name.
    """
    bones = model.bones
    n = len(bones)
    if n == 0:
        return set()
    generic = [GENERIC_FMT % i for i in range(n)]
    is_real = [names[i] != generic[i] for i in range(n)]
    used = set(names)
    inferred = set()

    P, parent, tol, partner = _mirror_partners(bones)

    # --- which Y sign is "left"? Vote with the real L/R names --------------
    votes = 0
    for i in range(n):
        if not is_real[i] or abs(P[i][1]) <= tol:
            continue
        side = _split_side(names[i])[1]
        if side:
            votes += 1 if ((P[i][1] > 0) == (side == "L")) else -1
    left_is_pos_y = votes >= 0

    def side_of(i):
        y = P[i][1]
        if abs(y) <= tol:
            return ""
        return "L" if ((y > 0) == left_is_pos_y) else "R"

    def give(i, name, force_unique=False):
        if is_real[i] or i in inferred:
            return False
        base, k = name, 2
        if name in used:
            if not force_unique:
                return False
            while name in used:
                name = "%s_%d" % (base, k)
                k += 1
        used.add(name)
        names[i] = name
        inferred.add(i)
        return True

    # 0) Dictionary-recovered CRC names (hash matches, so near-certain).
    wanted = {}
    for i in range(n):
        crc = getattr(bones[i], "name_crc", 0) & 0xFFFFFFFF
        if not is_real[i] and crc:
            wanted[i] = crc
    found = bone_crc_names.recover(set(wanted.values()))
    for i, crc in wanted.items():
        if crc in found:
            give(i, found[crc], force_unique=True)

    # 1) Mirror of a real name: Arm_L is real, its partner becomes Arm_R.
    for i in range(n):
        j = partner.get(i)
        if j is None or not is_real[i] or is_real[j]:
            continue
        stem, side = _split_side(names[i])
        if side and side_of(j) and side_of(j) != side:
            give(j, "%s_%s" % (stem, side_of(j)))

    # 2) Attachment mounts (hand, elbow, shoulder, knee, hip).
    for att in getattr(model, "attachments", None) or []:
        want = ATTACH_BONE_NAMES.get(att.id)
        bi = att.bone
        if want is None or not (0 <= bi < n):
            continue
        side = side_of(bi)
        if side and _split_side(want)[1] == side and bi in partner:
            give(bi, want)

    # 2b) Upper arm: an unnamed bone directly above a Forearm is the upper arm
    # (when the rig has no shoulder bone, it is the one off the spine).
    for i in range(n):
        if not (is_real[i] or i in inferred):
            continue
        stem, side = _split_side(names[i])
        p = parent[i]
        if stem == "Forearm" and side and p >= 0 and p in partner:
            give(p, "%s_%s" % (UPPER_ARM_STEM, side))

    # 3) Chains below a named bone: Arm_L -> Arm_L_01, Arm_L_02, ...
    body = set(partner)
    # midline bones that lead up to a body bone are the spine
    for i in list(body):
        p = parent[i]
        seen = 0
        while p != -1 and seen < n:
            seen += 1
            if abs(P[p][1]) <= tol:
                body.add(p)
            p = parent[p]

    children = {}
    for i in range(n):
        children.setdefault(parent[i], []).append(i)

    chain_count = {}        # parent stem -> chains started off it so far
    forced = {}

    def new_chain_stem(parent_stem):
        """Name an unidentified chain after where it attaches (HeadChain1, ...).

        We can't tell hair from horns from cloth, so the name only says what is
        known: which part of the body the chain hangs from.
        """
        base = re.sub(r"Chain\d+$", "", parent_stem) or "Body"
        chain_count[base] = chain_count.get(base, 0) + 1
        return "%sChain%d" % (base, chain_count[base])

    def walk(i, stem, depth, side):
        """Visit bone i; (stem, depth, side) describe its nearest named ancestor."""
        # Parentless pairs are helpers (IK targets, props), not body chains.
        body_ok = i in body and not (i in partner and parent[i] == -1)
        if not is_real[i] and i not in inferred and body_ok:
            s_i = side_of(i)
            if i in partner and i in forced:
                stem2, depth2 = forced[i], 0
            elif i in partner and (not side or s_i != side or not stem):
                stem2, depth2 = new_chain_stem(stem), 0
                forced[partner[i]] = stem2      # mirror chain shares the stem
            else:
                stem2, depth2 = stem, depth
            if i not in partner:
                stem2 = stem2 or "Body"
                tag = "%s_%02d" % (stem2, depth2 + 1)
            else:
                tag = "%s_%s_%02d" % (stem2, s_i, depth2 + 1)
            if give(i, tag, force_unique=True):
                stem, depth, side = stem2, depth2 + 1, s_i
        elif is_real[i] or i in inferred:
            base, sd = _split_side(names[i])
            base = re.sub(r"_\d+$", "", base)
            stem, depth, side = _STEM_ALIASES.get(base, base), 0, sd
        for c in children.get(i, []):
            walk(c, stem, depth, side)

    # iterative-safe: M2 skeletons are shallow, but guard recursion anyway
    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 4 * n + 100))
    try:
        for r in children.get(-1, []):
            walk(r, "", 0, "")
    finally:
        sys.setrecursionlimit(old)
    return inferred


def _real_names(model):
    """Names stored in the file: key-bone tables first, then the name CRCs."""
    n = len(model.bones)
    names = [GENERIC_FMT % i for i in range(n)]
    taken = set()

    def claim(bone_index, key_id):
        if not (0 <= bone_index < n) or bone_index in taken_idx:
            return
        base = KEY_BONE_NAMES.get(key_id)
        if base is None:
            return
        name, k = base, 2
        while name in taken:
            name = "%s_%d" % (base, k)
            k += 1
        taken.add(name)
        taken_idx.add(bone_index)
        names[bone_index] = name

    taken_idx = set()
    for key_id, bone_index in enumerate(getattr(model, "key_bone_lookup", None) or []):
        if bone_index != 0xFFFF:
            claim(bone_index, key_id)
    # Bones that carry a key_bone_id themselves but are absent from the table.
    for i, b in enumerate(model.bones):
        kid = getattr(b, "key_bone_id", -1)
        if kid is not None and kid >= 0:
            claim(i, kid)
    # Name CRC = crc32(bone name). Unlike the key-bone table it is stored for
    # nearly every bone in a modern M2, so it names the spine, neck, legs, ...
    for i, b in enumerate(model.bones):
        if i in taken_idx:
            continue
        crc = getattr(b, "name_crc", 0) & 0xFFFFFFFF
        base = bone_crc_names.KNOWN.get(crc) if crc else None
        if not base:
            continue
        name, k = base, 2
        while name in taken:
            name = "%s_%d" % (base, k)
            k += 1
        taken.add(name)
        taken_idx.add(i)
        names[i] = name
    return names


def bone_index(bone):
    """M2 bone index of a Blender Bone (or bone name string); None if unknown."""
    if not isinstance(bone, str):
        v = bone.get(INDEX_PROP)
        if v is not None:
            return int(v)
        bone = bone.name
    m = _GENERIC_RE.fullmatch(bone)
    return int(m.group(1)) if m else None
