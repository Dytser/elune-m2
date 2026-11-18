"""Sort an M2 skeleton's bones into a tree of Blender bone collections.

Pure Python (no bpy) so it can be tested on its own: ``classify`` returns
``{bone_index: [collection key, ...]}`` and ``apply`` creates the collections.

Tree (a bone may sit in several collections):

    Root               root / main bones                            (hidden)
    Spine              spine, neck and the head bone itself
    Arms               shoulder .. hand (the hand bone is also in Hands)
      Hands            hand + fingers and anything below the hand   (hidden)
    Head               everything hanging off the head bone         (hidden)
    Legs               pelvis, legs, feet and below
    Jiggle             breast bones and any other soft-body/jiggle bones
    Other                                                           (hidden)
      Attachment Points  bones that only exist to mount things
      Unclear          wings, tails, capes, unknown...
"""
import re

ROOT = "Root"
SPINE = "Spine"
ARMS = "Arms"
HANDS = "Hands"
HEAD = "Head"
LEGS = "Legs"
JIGGLE = "Jiggle"
OTHER = "Other"
ATTACH = "Attachment Points"
UNCLEAR = "Unclear"

# path (tuple) for every collection, parent first
PATHS = {
    ROOT: (ROOT,),
    SPINE: (SPINE,),
    ARMS: (ARMS,),
    HANDS: (ARMS, HANDS),
    HEAD: (HEAD,),
    LEGS: (LEGS,),
    JIGGLE: (JIGGLE,),
    OTHER: (OTHER,),
    ATTACH: (OTHER, ATTACH),
    UNCLEAR: (OTHER, UNCLEAR),
}
# Bone colour per collection (a bone is coloured by its first collection, so
# the hand bone, which is in Arms and Hands, reads as an arm bone).
COLORS = {
    ROOT: (0.85, 0.85, 0.85),
    SPINE: (0.95, 0.80, 0.10),
    ARMS: (0.20, 0.45, 0.95),
    HANDS: (0.20, 0.85, 0.90),
    HEAD: (0.25, 0.80, 0.30),
    LEGS: (0.95, 0.50, 0.10),
    JIGGLE: (0.95, 0.35, 0.70),
    ATTACH: (0.60, 0.35, 0.90),
    UNCLEAR: (0.45, 0.45, 0.45),
}
COLORS[OTHER] = COLORS[UNCLEAR]


def _mix(c, t):
    """Blend colour ``c`` toward white by ``t`` (0..1)."""
    return tuple(v + (1.0 - v) * t for v in c)


def _color_bone(b, rgb):
    """Give a Bone / PoseBone a custom colour set; silently skip if unsupported."""
    try:
        b.color.palette = "CUSTOM"
        b.color.custom.normal = rgb
        b.color.custom.select = _mix(rgb, 0.45)
        b.color.custom.active = _mix(rgb, 0.8)
    except Exception:  # noqa: BLE001 - old Blender / stand-in objects
        pass


HIDDEN_BY_DEFAULT = {ROOT, HANDS, HEAD, OTHER, ATTACH, UNCLEAR}

_SPINE_STEMS = {"Spine", "SpineLow", "SpineMid", "SpineUp", "Waist", "Chest",
                "UpperBodyParent", "Neck", "NeckParent", "NeckChild1", "Head"}
_ARM_STEMS = {"Arm", "Arm2", "UpArm", "LoArm", "LowArm", "UpperArm", "LowerArm",
              "Upperarm", "Lowerarm", "Forearm", "Shoulder", "Elbow",
              "Elbow_child", "Elbow_upper", "Wrist_upper", "Wrist_child"}
_LEG_STEMS = {"Pelvis", "LowerBodyParent", "Leg", "Thigh", "Calf", "UpLeg", "LoLeg", "UpperLeg",
              "LowerLeg", "Knee", "Knee_upper", "Foot", "Hip"}
_HAND_STEMS = {"Hand"}
_FOOT_STEMS = {"Foot"}
_ROOT_STEMS = {"Root", "Main", "Body"}
_KNOWN_STEMS = (_SPINE_STEMS | _ARM_STEMS | _LEG_STEMS | _HAND_STEMS
                | _ROOT_STEMS | {"Jaw"})
_JIGGLE_RE = re.compile(r"^(Breast|Jiggle)", re.I)   # extend as jiggle bones are added
_FINGER_RE = re.compile(r"(Finger|Thumb)")
_EVENT_RE = re.compile(r"[A-Z][A-Z0-9]{2}")     # DTH, FSD, CST, FL0, ...

# Key-bone ids that only exist to mount something (BTH, CSR, CSL, Breath, Name,
# NameMount, CHD, CCH, Buckle, Weapon_R, Weapon_L).
_ATTACH_KEYS = set(range(18, 26)) | {52, 66, 67}
_ROOT_KEYS = {26, 54}
_HEAD_KEY = 6
_HAND_KEYS = {64, 65}


def _stem(name):
    """'UpArm_L' / 'UpArm_L_01' -> 'UpArm'; 'Spine_02' -> 'Spine'."""
    m = re.fullmatch(r"(.+?)_([LR])(?:_\d+)*", name)
    if m:
        return m.group(1)
    m = re.fullmatch(r"(.+?)(?:_\d+)+", name)
    return m.group(1) if m else name


def classify(model, names):
    """Return ``{bone_index: [collection key, ...]}`` for every bone."""
    bones = model.bones
    n = len(bones)
    stems = [_stem(names[i]) for i in range(n)]
    parent = [b.parent if 0 <= b.parent < n else -1 for b in bones]
    keys = [int(b.key_bone_id) for b in bones]

    mount = {a.bone for a in (getattr(model, "attachments", None) or [])
             if 0 <= a.bone < n}

    # base class of each bone from its own name / key id
    base = [None] * n
    for i in range(n):
        s = stems[i]
        chain_mount = i in mount and re.search(r"_\d+$", names[i]) is not None
        if keys[i] in _ROOT_KEYS or s in _ROOT_STEMS:
            base[i] = "root"
        elif chain_mount or (_EVENT_RE.fullmatch(names[i]) and keys[i] < 0
                             and s not in _KNOWN_STEMS):
            base[i] = "mount"       # event bone (DTH, FSD, CST...) / mounted chain
        elif _JIGGLE_RE.match(s):
            base[i] = "jiggle"
        elif keys[i] == _HEAD_KEY or s == "Head":
            base[i] = "head"
        elif keys[i] in _HAND_KEYS or s in _HAND_STEMS:
            base[i] = "hand"
        elif _FINGER_RE.search(s):
            base[i] = "finger"
        elif s in _SPINE_STEMS:
            base[i] = "spine"
        elif s in _ARM_STEMS:
            base[i] = "arm"
        elif s in _FOOT_STEMS:
            base[i] = "foot"
        elif s == "Pelvis" or s == "LowerBodyParent":
            base[i] = "pelvis"
        elif s in _LEG_STEMS:
            base[i] = "leg"
    for i in range(n):
        if base[i] is None and (keys[i] in _ATTACH_KEYS or i in mount):
            base[i] = "mount"

    # unclassified bones inherit from the nearest classified ancestor:
    # below the head -> Head, below a hand/finger -> Hands, below an arm ->
    # Arms, below a leg/foot -> Legs. Below spine/pelvis/root -> Other (that is
    # where wings, tails and capes hang).
    inherit = {"head": "head_child", "hand": "hand_child",
               "finger": "hand_child", "hand_child": "hand_child",
               "arm": "arm_child", "arm_child": "arm_child",
               "foot": "leg_child", "leg": "leg_child", "leg_child": "leg_child",
               "head_child": "head_child", "jiggle": "jiggle_child",
               "jiggle_child": "jiggle_child"}
    final = list(base)
    for i in range(n):
        if final[i] is not None:
            continue
        p, guard = parent[i], 0
        while p >= 0 and guard < n:
            guard += 1
            if base[p] in ("spine", "pelvis", "root"):
                break
            if base[p] in inherit:
                final[i] = inherit[base[p]]
                break
            p = parent[p]

    out = {}
    for i in range(n):
        c = final[i]
        if c == "root":
            out[i] = [ROOT]
        elif c == "spine":
            out[i] = [SPINE]
        elif c == "head":
            out[i] = [SPINE]            # the head bone sits with the spine
        elif c in ("arm", "arm_child"):
            out[i] = [ARMS]
        elif c == "hand":
            out[i] = [ARMS, HANDS]
        elif c in ("finger", "hand_child"):
            out[i] = [HANDS]
        elif c in ("pelvis", "leg", "foot", "leg_child"):
            out[i] = [LEGS]
        elif c in ("jiggle", "jiggle_child"):
            out[i] = [JIGGLE]
        elif c == "head_child":
            out[i] = [HEAD]
        elif c == "mount":
            out[i] = [ATTACH]
        else:
            out[i] = [UNCLEAR]
    return out


def apply(arm_data, names, membership, pose=None):
    """Create the collections on ``arm_data`` and assign the bones.

    Only non-empty collections (and their ancestors) are created. Bones are
    also coloured by collection (``pose`` = the armature object's ``pose`` so
    pose-mode colours are set too).
    """
    used = set()
    for cols in membership.values():
        for c in cols:
            used.update(PATHS[c])
    made = {}

    def get(label_path):
        if label_path in made:
            return made[label_path]
        parent = get(label_path[:-1]) if len(label_path) > 1 else None
        coll = arm_data.collections.new(label_path[-1], parent=parent)
        made[label_path] = coll
        return coll

    order = [ROOT, SPINE, ARMS, HANDS, HEAD, LEGS, JIGGLE, OTHER, ATTACH, UNCLEAR]
    for key in order:
        path = PATHS[key]
        if path[-1] in used:
            coll = get(path)
            coll.is_visible = key not in HIDDEN_BY_DEFAULT
    for i, cols in membership.items():
        bone = arm_data.bones.get(names[i])
        if bone is None:
            continue
        for c in cols:
            made[PATHS[c]].assign(bone)
        rgb = COLORS[cols[0]]
        _color_bone(bone, rgb)
        pb = pose.bones.get(names[i]) if pose is not None else None
        if pb is not None:
            _color_bone(pb, rgb)
    return made
