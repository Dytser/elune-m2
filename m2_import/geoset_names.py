"""Readable labels for character geosets (geoset id = group * 100 + variant).

Group meanings follow the community CharGeosets documentation. They only make
sense for player-style character models; creatures and objects use ids freely,
so those keep the plain ``geoset_NNNN`` name.
"""

GROUP_NAMES = {
    0: "Hair", 1: "Facial1", 2: "Facial2", 3: "Facial3", 4: "Gloves",
    5: "Boots", 6: "Tail", 7: "Ears", 8: "Sleeves", 9: "Kneepads",
    10: "Chest", 11: "Legs", 12: "Tabard", 13: "Robe", 14: "Loincloth",
    15: "Cloak", 16: "FacialJewelry", 17: "EyeEffects", 18: "Belt",
    19: "Skeleton", 20: "Feet", 21: "Head", 22: "Torso",
    23: "HandAttachments", 24: "HeadAttachments", 25: "Blindfolds",
    26: "Shoulders", 27: "Helm", 28: "UpperArm",
    32: "Head",            # 3201/3202: tongue + head (confirmed by hand)
    33: "Eyes",
}


def is_character(section_ids):
    """Character-style models use ids >= 100 (variant 1+ of groups 1+)."""
    return any(i >= 100 for i in section_ids)


def label(section_id):
    """'Body' for id 0, else the group name ('Boots'); '' if unknown."""
    if section_id == 0:
        return "Body"
    return GROUP_NAMES.get(section_id // 100, "")
