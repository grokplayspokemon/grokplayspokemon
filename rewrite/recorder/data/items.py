from enum import Enum


MAX_ITEM_CAPACITY = 20
# Starts at 0x1


class Items(Enum):
    MASTER_BALL = 0x01
    ULTRA_BALL = 0x02
    GREAT_BALL = 0x03
    POKE_BALL = 0x04
    TOWN_MAP = 0x05
    BICYCLE = 0x06
    SURFBOARD = 0x07  #
    SAFARI_BALL = 0x08
    POKEDEX = 0x09
    MOON_STONE = 0x0A
    ANTIDOTE = 0x0B
    BURN_HEAL = 0x0C
    ICE_HEAL = 0x0D
    AWAKENING = 0x0E
    PARLYZ_HEAL = 0x0F
    FULL_RESTORE = 0x10
    MAX_POTION = 0x11
    HYPER_POTION = 0x12
    SUPER_POTION = 0x13
    POTION = 0x14
    BOULDERBADGE = 0x15
    CASCADEBADGE = 0x16
    SAFARI_BAIT = 0x15  # overload
    SAFARI_ROCK = 0x16  # overload
    THUNDERBADGE = 0x17
    RAINBOWBADGE = 0x18
    SOULBADGE = 0x19
    MARSHBADGE = 0x1A
    VOLCANOBADGE = 0x1B
    EARTHBADGE = 0x1C
    ESCAPE_ROPE = 0x1D
    REPEL = 0x1E
    OLD_AMBER = 0x1F
    FIRE_STONE = 0x20
    THUNDER_STONE = 0x21
    WATER_STONE = 0x22
    HP_UP = 0x23
    PROTEIN = 0x24
    IRON = 0x25
    CARBOS = 0x26
    CALCIUM = 0x27
    RARE_CANDY = 0x28
    DOME_FOSSIL = 0x29
    HELIX_FOSSIL = 0x2A
    SECRET_KEY = 0x2B
    UNUSED_ITEM = 0x2C  # "?????"
    BIKE_VOUCHER = 0x2D
    X_ACCURACY = 0x2E
    LEAF_STONE = 0x2F
    CARD_KEY = 0x30
    NUGGET = 0x31
    PP_UP_2 = 0x32
    POKE_DOLL = 0x33
    FULL_HEAL = 0x34
    REVIVE = 0x35
    MAX_REVIVE = 0x36
    GUARD_SPEC = 0x37
    SUPER_REPEL = 0x38
    MAX_REPEL = 0x39
    DIRE_HIT = 0x3A
    COIN = 0x3B
    FRESH_WATER = 0x3C
    SODA_POP = 0x3D
    LEMONADE = 0x3E
    S_S_TICKET = 0x3F
    GOLD_TEETH = 0x40
    X_ATTACK = 0x41
    X_DEFEND = 0x42
    X_SPEED = 0x43
    X_SPECIAL = 0x44
    COIN_CASE = 0x45
    OAKS_PARCEL = 0x46
    ITEMFINDER = 0x47
    SILPH_SCOPE = 0x48
    POKE_FLUTE = 0x49
    LIFT_KEY = 0x4A
    EXP_ALL = 0x4B
    OLD_ROD = 0x4C
    GOOD_ROD = 0x4D
    SUPER_ROD = 0x4E
    PP_UP = 0x4F
    ETHER = 0x50
    MAX_ETHER = 0x51
    ELIXER = 0x52
    MAX_ELIXER = 0x53
    FLOOR_B2F = 0x54
    FLOOR_B1F = 0x55
    FLOOR_1F = 0x56
    FLOOR_2F = 0x57
    FLOOR_3F = 0x58
    FLOOR_4F = 0x59
    FLOOR_5F = 0x5A
    FLOOR_6F = 0x5B
    FLOOR_7F = 0x5C
    FLOOR_8F = 0x5D
    FLOOR_9F = 0x5E
    FLOOR_10F = 0x5F
    FLOOR_11F = 0x60
    FLOOR_B4F = 0x61
    HM_01 = 0xC4
    HM_02 = 0xC5
    HM_03 = 0xC6
    HM_04 = 0xC7
    HM_05 = 0xC8
    TM_01 = 0xC9
    TM_02 = 0xCA
    TM_03 = 0xCB
    TM_04 = 0xCC
    TM_05 = 0xCD
    TM_06 = 0xCE
    TM_07 = 0xCF
    TM_08 = 0xD0
    TM_09 = 0xD1
    TM_10 = 0xD2
    TM_11 = 0xD3
    TM_12 = 0xD4
    TM_13 = 0xD5
    TM_14 = 0xD6
    TM_15 = 0xD7
    TM_16 = 0xD8
    TM_17 = 0xD9
    TM_18 = 0xDA
    TM_19 = 0xDB
    TM_20 = 0xDC
    TM_21 = 0xDD
    TM_22 = 0xDE
    TM_23 = 0xDF
    TM_24 = 0xE0
    TM_25 = 0xE1
    TM_26 = 0xE2
    TM_27 = 0xE3
    TM_28 = 0xE4
    TM_29 = 0xE5
    TM_30 = 0xE6
    TM_31 = 0xE7
    TM_32 = 0xE8
    TM_33 = 0xE9
    TM_34 = 0xEA
    TM_35 = 0xEB
    TM_36 = 0xEC
    TM_37 = 0xED
    TM_38 = 0xEE
    TM_39 = 0xEF
    TM_40 = 0xF0
    TM_41 = 0xF1
    TM_42 = 0xF2
    TM_43 = 0xF3
    TM_44 = 0xF4
    TM_45 = 0xF5
    TM_46 = 0xF6
    TM_47 = 0xF7
    TM_48 = 0xF8
    TM_49 = 0xF9
    TM_50 = 0xFA


USEFUL_ITEMS = {
    Items.LEMONADE,
    Items.FRESH_WATER,
    Items.SODA_POP,
    Items.BICYCLE,
    Items.BIKE_VOUCHER,
}

REQUIRED_ITEMS = {
    Items.SECRET_KEY,
    # Items.ITEM_2C,
    Items.CARD_KEY,
    Items.S_S_TICKET,
    Items.GOLD_TEETH,
    Items.OAKS_PARCEL,
    Items.SILPH_SCOPE,
    Items.POKE_FLUTE,
    Items.LIFT_KEY,
    Items.HM_01,
    Items.HM_03,
    Items.HM_04,
}

KEY_ITEMS = {
    Items.TOWN_MAP,
    Items.BICYCLE,
    Items.SURFBOARD,
    Items.SAFARI_BALL,
    Items.POKEDEX,
    Items.BOULDERBADGE,
    Items.CASCADEBADGE,
    Items.THUNDERBADGE,
    Items.RAINBOWBADGE,
    Items.SOULBADGE,
    Items.MARSHBADGE,
    Items.VOLCANOBADGE,
    Items.EARTHBADGE,
    Items.OLD_AMBER,
    Items.DOME_FOSSIL,
    Items.HELIX_FOSSIL,
    Items.SECRET_KEY,
    # Items.ITEM_2C,
    Items.BIKE_VOUCHER,
    Items.CARD_KEY,
    Items.S_S_TICKET,
    Items.GOLD_TEETH,
    Items.COIN_CASE,
    Items.OAKS_PARCEL,
    Items.ITEMFINDER,
    Items.SILPH_SCOPE,
    Items.POKE_FLUTE,
    Items.LIFT_KEY,
    Items.OLD_ROD,
    Items.GOOD_ROD,
    Items.SUPER_ROD,
}

HM_ITEMS = {
    Items.HM_01,
    Items.HM_02,
    Items.HM_03,
    Items.HM_04,
    Items.HM_05,
}