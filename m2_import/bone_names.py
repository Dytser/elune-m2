"""Readable, Blender-mirror-friendly bone names from the M2 key-bone IDs.

The M2 ``boneIndicesById`` (key_bone_lookup) table maps a well-known key-bone ID
(see ``E_BoneIndicesID`` in M2.bt) to a bone index. Bones listed there get a
real name; the rest keep the generic ``bone_NNN`` name. Left/right bones use a
``_L`` / ``_R`` suffix so Blender's X-mirror tools work on them.

Every bone also gets ``m2_bone_index`` stamped on it at import: the exporters
use that (via :func:`bone_index`) instead of parsing the name.
"""

import re

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


def compute_bone_names(model):
    """Return a list with one unique Blender bone name per M2 bone."""
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
