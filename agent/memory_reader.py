# memory_reader.py
from collections import deque
import logging
logger = logging.getLogger(__name__)

import numpy as np

from game_data.red_memory_battle import *
from game_data.red_memory_menus import *
from game_data.red_memory_env import *
from game_data.red_memory_items import *
from game_data.red_memory_map import *
from game_data.red_memory_player import *
from game_data.red_env_constants import *
from game_data.red_gym_map import *
from game_data.red_gym_world import *
from game_data.red_env_constants import *


from dataclasses import dataclass
from enum import IntEnum, IntFlag
# from game_data.red_memory_battle import (
#     ENEMY_PARTY_COUNT, 
#     ENEMY_PARTY_SPECIES, 
#     ENEMYS_POKEMON_TYPES, 
#     POKEMON_MATCH_TYPES,
#     PLAYERS_MOVE_POWER,
#     PLAYERS_MOVE_TYPE,
#     PLAYERS_MOVE_PP,
#     PLAYERS_MOVE_NUM,
# )

class StatusCondition(IntFlag):
    NONE = 0
    SLEEP_MASK = 0b111  # Bits 0-2
    SLEEP = 0b001  # For name display purposes
    POISON = 0b1000  # Bit 3
    BURN = 0b10000  # Bit 4
    FREEZE = 0b100000  # Bit 5
    PARALYSIS = 0b1000000  # Bit 6
    
    @property
    def is_asleep(self) -> bool:
        """Check if the Pokémon is asleep (any value in bits 0-2)"""
        # For sleep, we directly check if any bits in positions 0-2 are set (values 1-7)
        return bool(int(self) & 0b111)
    
    def get_status_name(self) -> str:
        """Get a human-readable status name"""
        if self.is_asleep:
            return "SLEEP"
        elif self & StatusCondition.PARALYSIS:
            return "PARALYSIS"
        elif self & StatusCondition.FREEZE:
            return "FREEZE"
        elif self & StatusCondition.BURN:
            return "BURN"
        elif self & StatusCondition.POISON:
            return "POISON"
        return "OK"
    
class Tileset(IntEnum):
    """Maps tileset IDs to their names"""

    OVERWORLD = 0x00
    REDS_HOUSE_1 = 0x01
    MART = 0x02
    FOREST = 0x03
    REDS_HOUSE_2 = 0x04
    DOJO = 0x05
    POKECENTER = 0x06
    GYM = 0x07
    HOUSE = 0x08
    FOREST_GATE = 0x09
    MUSEUM = 0x0A
    UNDERGROUND = 0x0B
    GATE = 0x0C
    SHIP = 0x0D
    SHIP_PORT = 0x0E
    CEMETERY = 0x0F
    INTERIOR = 0x10
    CAVERN = 0x11
    LOBBY = 0x12
    MANSION = 0x13
    LAB = 0x14
    CLUB = 0x15
    FACILITY = 0x16
    PLATEAU = 0x17

class PokemonType(IntEnum):
    NORMAL = 0x00
    FIGHTING = 0x01
    FLYING = 0x02
    POISON = 0x03
    GROUND = 0x04
    ROCK = 0x05
    BUG = 0x07
    GHOST = 0x08
    FIRE = 0x14
    WATER = 0x15
    GRASS = 0x16
    ELECTRIC = 0x17
    PSYCHIC = 0x18
    ICE = 0x19
    DRAGON = 0x1A


class PokemonID(IntEnum):
    """Maps Pokemon species IDs to their names"""

    RHYDON = 0x01
    KANGASKHAN = 0x02
    NIDORAN_M = 0x03
    CLEFAIRY = 0x04
    SPEAROW = 0x05
    VOLTORB = 0x06
    NIDOKING = 0x07
    SLOWBRO = 0x08
    IVYSAUR = 0x09
    EXEGGUTOR = 0x0A
    LICKITUNG = 0x0B
    EXEGGCUTE = 0x0C
    GRIMER = 0x0D
    GENGAR = 0x0E
    NIDORAN_F = 0x0F
    NIDOQUEEN = 0x10
    CUBONE = 0x11
    RHYHORN = 0x12
    LAPRAS = 0x13
    ARCANINE = 0x14
    MEW = 0x15
    GYARADOS = 0x16
    SHELLDER = 0x17
    TENTACOOL = 0x18
    GASTLY = 0x19
    SCYTHER = 0x1A
    STARYU = 0x1B
    BLASTOISE = 0x1C
    PINSIR = 0x1D
    TANGELA = 0x1E
    MISSINGNO_1F = 0x1F
    MISSINGNO_20 = 0x20
    GROWLITHE = 0x21
    ONIX = 0x22
    FEAROW = 0x23
    PIDGEY = 0x24
    SLOWPOKE = 0x25
    KADABRA = 0x26
    GRAVELER = 0x27
    CHANSEY = 0x28
    MACHOKE = 0x29
    MR_MIME = 0x2A
    HITMONLEE = 0x2B
    HITMONCHAN = 0x2C
    ARBOK = 0x2D
    PARASECT = 0x2E
    PSYDUCK = 0x2F
    DROWZEE = 0x30
    GOLEM = 0x31
    MISSINGNO_32 = 0x32
    MAGMAR = 0x33
    MISSINGNO_34 = 0x34
    ELECTABUZZ = 0x35
    MAGNETON = 0x36
    KOFFING = 0x37
    MISSINGNO_38 = 0x38
    MANKEY = 0x39
    SEEL = 0x3A
    DIGLETT = 0x3B
    TAUROS = 0x3C
    MISSINGNO_3D = 0x3D
    MISSINGNO_3E = 0x3E
    MISSINGNO_3F = 0x3F
    FARFETCHD = 0x40
    VENONAT = 0x41
    DRAGONITE = 0x42
    MISSINGNO_43 = 0x43
    MISSINGNO_44 = 0x44
    MISSINGNO_45 = 0x45
    DODUO = 0x46
    POLIWAG = 0x47
    JYNX = 0x48
    MOLTRES = 0x49
    ARTICUNO = 0x4A
    ZAPDOS = 0x4B
    DITTO = 0x4C
    MEOWTH = 0x4D
    KRABBY = 0x4E
    MISSINGNO_4F = 0x4F
    MISSINGNO_50 = 0x50
    MISSINGNO_51 = 0x51
    VULPIX = 0x52
    NINETALES = 0x53
    PIKACHU = 0x54
    RAICHU = 0x55
    MISSINGNO_56 = 0x56
    MISSINGNO_57 = 0x57
    DRATINI = 0x58
    DRAGONAIR = 0x59
    KABUTO = 0x5A
    KABUTOPS = 0x5B
    HORSEA = 0x5C
    SEADRA = 0x5D
    MISSINGNO_5E = 0x5E
    MISSINGNO_5F = 0x5F
    SANDSHREW = 0x60
    SANDSLASH = 0x61
    OMANYTE = 0x62
    OMASTAR = 0x63
    JIGGLYPUFF = 0x64
    WIGGLYTUFF = 0x65
    EEVEE = 0x66
    FLAREON = 0x67
    JOLTEON = 0x68
    VAPOREON = 0x69
    MACHOP = 0x6A
    ZUBAT = 0x6B
    EKANS = 0x6C
    PARAS = 0x6D
    POLIWHIRL = 0x6E
    POLIWRATH = 0x6F
    WEEDLE = 0x70
    KAKUNA = 0x71
    BEEDRILL = 0x72
    MISSINGNO_73 = 0x73
    DODRIO = 0x74
    PRIMEAPE = 0x75
    DUGTRIO = 0x76
    VENOMOTH = 0x77
    DEWGONG = 0x78
    MISSINGNO_79 = 0x79
    MISSINGNO_7A = 0x7A
    CATERPIE = 0x7B
    METAPOD = 0x7C
    BUTTERFREE = 0x7D
    MACHAMP = 0x7E
    MISSINGNO_7F = 0x7F
    GOLDUCK = 0x80
    HYPNO = 0x81
    GOLBAT = 0x82
    MEWTWO = 0x83
    SNORLAX = 0x84
    MAGIKARP = 0x85
    MISSINGNO_86 = 0x86
    MISSINGNO_87 = 0x87
    MUK = 0x88
    MISSINGNO_89 = 0x89
    KINGLER = 0x8A
    CLOYSTER = 0x8B
    MISSINGNO_8C = 0x8C
    ELECTRODE = 0x8D
    CLEFABLE = 0x8E
    WEEZING = 0x8F
    PERSIAN = 0x90
    MAROWAK = 0x91
    MISSINGNO_92 = 0x92
    HAUNTER = 0x93
    ABRA = 0x94
    ALAKAZAM = 0x95
    PIDGEOTTO = 0x96
    PIDGEOT = 0x97
    STARMIE = 0x98
    BULBASAUR = 0x99
    VENUSAUR = 0x9A
    TENTACRUEL = 0x9B
    MISSINGNO_9C = 0x9C
    GOLDEEN = 0x9D
    SEAKING = 0x9E
    MISSINGNO_9F = 0x9F
    MISSINGNO_A0 = 0xA0
    MISSINGNO_A1 = 0xA1
    MISSINGNO_A2 = 0xA2
    PONYTA = 0xA3
    RAPIDASH = 0xA4
    RATTATA = 0xA5
    RATICATE = 0xA6
    NIDORINO = 0xA7
    NIDORINA = 0xA8
    GEODUDE = 0xA9
    PORYGON = 0xAA
    AERODACTYL = 0xABAA
    MISSINGNO_AC = 0xAC
    MAGNEMITE = 0xAD
    MISSINGNO_AE = 0xAE
    MISSINGNO_AF = 0xAF
    CHARMANDER = 0xB0
    SQUIRTLE = 0xB1
    CHARMELEON = 0xB2
    WARTORTLE = 0xB3
    CHARIZARD = 0xB4
    MISSINGNO_B5 = 0xB5
    FOSSIL_KABUTOPS = 0xB6
    FOSSIL_AERODACTYL = 0xB7
    MON_GHOST = 0xB8
    ODDISH = 0xB9
    GLOOM = 0xBA
    VILEPLUME = 0xBB
    BELLSPROUT = 0xBC
    WEEPINBELL = 0xBD
    VICTREEBEL = 0xBE


class Move(IntEnum):
    """Maps move IDs to their names"""

    POUND = 0x01
    KARATE_CHOP = 0x02
    DOUBLESLAP = 0x03
    COMET_PUNCH = 0x04
    MEGA_PUNCH = 0x05
    PAY_DAY = 0x06
    FIRE_PUNCH = 0x07
    ICE_PUNCH = 0x08
    THUNDERPUNCH = 0x09
    SCRATCH = 0x0A
    VICEGRIP = 0x0B
    GUILLOTINE = 0x0C
    RAZOR_WIND = 0x0D
    SWORDS_DANCE = 0x0E
    CUT = 0x0F
    GUST = 0x10
    WING_ATTACK = 0x11
    WHIRLWIND = 0x12
    FLY = 0x13
    BIND = 0x14
    SLAM = 0x15
    VINE_WHIP = 0x16
    STOMP = 0x17
    DOUBLE_KICK = 0x18
    MEGA_KICK = 0x19
    JUMP_KICK = 0x1A
    ROLLING_KICK = 0x1B
    SAND_ATTACK = 0x1C
    HEADBUTT = 0x1D
    HORN_ATTACK = 0x1E
    FURY_ATTACK = 0x1F
    HORN_DRILL = 0x20
    TACKLE = 0x21
    BODY_SLAM = 0x22
    WRAP = 0x23
    TAKE_DOWN = 0x24
    THRASH = 0x25
    DOUBLE_EDGE = 0x26
    TAIL_WHIP = 0x27
    POISON_STING = 0x28
    TWINEEDLE = 0x29
    PIN_MISSILE = 0x2A
    LEER = 0x2B
    BITE = 0x2C
    GROWL = 0x2D
    ROAR = 0x2E
    SING = 0x2F
    SUPERSONIC = 0x30
    SONICBOOM = 0x31
    DISABLE = 0x32
    ACID = 0x33
    EMBER = 0x34
    FLAMETHROWER = 0x35
    MIST = 0x36
    WATER_GUN = 0x37
    HYDRO_PUMP = 0x38
    SURF = 0x39
    ICE_BEAM = 0x3A
    BLIZZARD = 0x3B
    PSYBEAM = 0x3C
    BUBBLEBEAM = 0x3D
    AURORA_BEAM = 0x3E
    HYPER_BEAM = 0x3F
    PECK = 0x40
    DRILL_PECK = 0x41
    SUBMISSION = 0x42
    LOW_KICK = 0x43
    COUNTER = 0x44
    SEISMIC_TOSS = 0x45
    STRENGTH = 0x46
    ABSORB = 0x47
    MEGA_DRAIN = 0x48
    LEECH_SEED = 0x49
    GROWTH = 0x4A
    RAZOR_LEAF = 0x4B
    SOLARBEAM = 0x4C
    POISONPOWDER = 0x4D
    STUN_SPORE = 0x4E
    SLEEP_POWDER = 0x4F
    PETAL_DANCE = 0x50
    STRING_SHOT = 0x51
    DRAGON_RAGE = 0x52
    FIRE_SPIN = 0x53
    THUNDERSHOCK = 0x54
    THUNDERBOLT = 0x55
    THUNDER_WAVE = 0x56
    THUNDER = 0x57
    ROCK_THROW = 0x58
    EARTHQUAKE = 0x59
    FISSURE = 0x5A
    DIG = 0x5B
    TOXIC = 0x5C
    CONFUSION = 0x5D
    PSYCHIC = 0x5E
    HYPNOSIS = 0x5F
    MEDITATE = 0x60
    AGILITY = 0x61
    QUICK_ATTACK = 0x62
    RAGE = 0x63
    TELEPORT = 0x64
    NIGHT_SHADE = 0x65
    MIMIC = 0x66
    SCREECH = 0x67
    DOUBLE_TEAM = 0x68
    RECOVER = 0x69
    HARDEN = 0x6A
    MINIMIZE = 0x6B
    SMOKESCREEN = 0x6C
    CONFUSE_RAY = 0x6D
    WITHDRAW = 0x6E
    DEFENSE_CURL = 0x6F
    BARRIER = 0x70
    LIGHT_SCREEN = 0x71
    HAZE = 0x72
    REFLECT = 0x73
    FOCUS_ENERGY = 0x74
    BIDE = 0x75
    METRONOME = 0x76
    MIRROR_MOVE = 0x77
    SELFDESTRUCT = 0x78
    EGG_BOMB = 0x79
    LICK = 0x7A
    SMOG = 0x7B
    SLUDGE = 0x7C
    BONE_CLUB = 0x7D
    FIRE_BLAST = 0x7E
    WATERFALL = 0x7F
    CLAMP = 0x80
    SWIFT = 0x81
    SKULL_BASH = 0x82
    SPIKE_CANNON = 0x83
    CONSTRICT = 0x84
    AMNESIA = 0x85
    KINESIS = 0x86
    SOFTBOILED = 0x87
    HI_JUMP_KICK = 0x88
    GLARE = 0x89
    DREAM_EATER = 0x8A
    POISON_GAS = 0x8B
    BARRAGE = 0x8C
    LEECH_LIFE = 0x8D
    LOVELY_KISS = 0x8E
    SKY_ATTACK = 0x8F
    TRANSFORM = 0x90
    BUBBLE = 0x91
    DIZZY_PUNCH = 0x92
    SPORE = 0x93
    FLASH = 0x94
    PSYWAVE = 0x95
    SPLASH = 0x96
    ACID_ARMOR = 0x97
    CRABHAMMER = 0x98
    EXPLOSION = 0x99
    FURY_SWIPES = 0x9A
    BONEMERANG = 0x9B
    REST = 0x9C
    ROCK_SLIDE = 0x9D
    HYPER_FANG = 0x9E
    SHARPEN = 0x9F
    CONVERSION = 0xA0
    TRI_ATTACK = 0xA1
    SUPER_FANG = 0xA2
    SLASH = 0xA3
    SUBSTITUTE = 0xA4
    STRUGGLE = 0xA5


class MapLocation(IntEnum):
    """Maps location IDs to their names"""

    PALLET_TOWN = 0x00
    VIRIDIAN_CITY = 0x01
    PEWTER_CITY = 0x02
    CERULEAN_CITY = 0x03
    LAVENDER_TOWN = 0x04
    VERMILION_CITY = 0x05
    CELADON_CITY = 0x06
    FUCHSIA_CITY = 0x07
    CINNABAR_ISLAND = 0x08
    INDIGO_PLATEAU = 0x09
    SAFFRON_CITY = 0x0A
    UNUSED_0B = 0x0B
    ROUTE_1 = 0x0C
    ROUTE_2 = 0x0D
    ROUTE_3 = 0x0E
    ROUTE_4 = 0x0F
    ROUTE_5 = 0x10
    ROUTE_6 = 0x11
    ROUTE_7 = 0x12
    ROUTE_8 = 0x13
    ROUTE_9 = 0x14
    ROUTE_10 = 0x15
    ROUTE_11 = 0x16
    ROUTE_12 = 0x17
    ROUTE_13 = 0x18
    ROUTE_14 = 0x19
    ROUTE_15 = 0x1A
    ROUTE_16 = 0x1B
    ROUTE_17 = 0x1C
    ROUTE_18 = 0x1D
    ROUTE_19 = 0x1E
    ROUTE_20 = 0x1F
    ROUTE_21 = 0x20
    ROUTE_22 = 0x21
    ROUTE_23 = 0x22
    ROUTE_24 = 0x23
    ROUTE_25 = 0x24
    PLAYERS_HOUSE_1F = 0x25
    PLAYERS_HOUSE_2F = 0x26
    RIVALS_HOUSE = 0x27
    OAKS_LAB = 0x28
    VIRIDIAN_POKECENTER = 0x29
    VIRIDIAN_MART = 0x2A
    VIRIDIAN_SCHOOL = 0x2B
    VIRIDIAN_HOUSE = 0x2C
    VIRIDIAN_GYM = 0x2D
    DIGLETTS_CAVE_ROUTE2 = 0x2E
    VIRIDIAN_FOREST_NORTH_GATE = 0x2F
    ROUTE_2_HOUSE = 0x30
    ROUTE_2_GATE = 0x31
    VIRIDIAN_FOREST_SOUTH_GATE = 0x32
    VIRIDIAN_FOREST = 0x33
    MUSEUM_1F = 0x34
    MUSEUM_2F = 0x35
    PEWTER_GYM = 0x36
    PEWTER_HOUSE_1 = 0x37
    PEWTER_MART = 0x38
    PEWTER_HOUSE_2 = 0x39
    PEWTER_POKECENTER = 0x3A
    MT_MOON_1F = 0x3B
    MT_MOON_B1F = 0x3C
    MT_MOON_B2F = 0x3D
    CERULEAN_TRASHED_HOUSE = 0x3E
    CERULEAN_TRADE_HOUSE = 0x3F
    CERULEAN_POKECENTER = 0x40
    CERULEAN_GYM = 0x41
    BIKE_SHOP = 0x42
    CERULEAN_MART = 0x43
    MT_MOON_POKECENTER = 0x44
    ROUTE_5_GATE = 0x46
    UNDERGROUND_PATH_ROUTE5 = 0x47
    DAYCARE = 0x48
    ROUTE_6_GATE = 0x49
    UNDERGROUND_PATH_ROUTE6 = 0x4A
    ROUTE_7_GATE = 0x4C
    UNDERGROUND_PATH_ROUTE7 = 0x4D
    ROUTE_8_GATE = 0x4F
    UNDERGROUND_PATH_ROUTE8 = 0x50
    ROCK_TUNNEL_POKECENTER = 0x51
    ROCK_TUNNEL_1F = 0x52
    POWER_PLANT = 0x53
    ROUTE_11_GATE_1F = 0x54
    DIGLETTS_CAVE_ROUTE11 = 0x55
    ROUTE_11_GATE_2F = 0x56
    ROUTE_12_GATE_1F = 0x57
    BILLS_HOUSE = 0x58
    VERMILION_POKECENTER = 0x59
    FAN_CLUB = 0x5A
    VERMILION_MART = 0x5B
    VERMILION_GYM = 0x5C
    VERMILION_HOUSE_1 = 0x5D
    VERMILION_DOCK = 0x5E
    SS_ANNE_1F = 0x5F
    SS_ANNE_2F = 0x60
    SS_ANNE_3F = 0x61
    SS_ANNE_B1F = 0x62
    SS_ANNE_BOW = 0x63
    SS_ANNE_KITCHEN = 0x64
    SS_ANNE_CAPTAINS_ROOM = 0x65
    SS_ANNE_1F_ROOMS = 0x66
    SS_ANNE_2F_ROOMS = 0x67
    SS_ANNE_B1F_ROOMS = 0x68
    VICTORY_ROAD_1F = 0x6C
    LANCE = 0x71
    HALL_OF_FAME = 0x76
    UNDERGROUND_PATH_NS = 0x77
    CHAMPIONS_ROOM = 0x78
    UNDERGROUND_PATH_WE = 0x79
    CELADON_MART_1F = 0x7A
    CELADON_MART_2F = 0x7B
    CELADON_MART_3F = 0x7C
    CELADON_MART_4F = 0x7D
    CELADON_MART_ROOF = 0x7E
    CELADON_MART_ELEVATOR = 0x7F
    CELADON_MANSION_1F = 0x80
    CELADON_MANSION_2F = 0x81
    CELADON_MANSION_3F = 0x82
    CELADON_MANSION_ROOF = 0x83
    CELADON_MANSION_ROOF_HOUSE = 0x84
    CELADON_POKECENTER = 0x85
    CELADON_GYM = 0x86
    GAME_CORNER = 0x87
    CELADON_MART_5F = 0x88
    GAME_CORNER_PRIZE_ROOM = 0x89
    CELADON_DINER = 0x8A
    CELADON_HOUSE = 0x8B
    CELADON_HOTEL = 0x8C
    LAVENDER_POKECENTER = 0x8D
    POKEMON_TOWER_1F = 0x8E
    POKEMON_TOWER_2F = 0x8F
    POKEMON_TOWER_3F = 0x90
    POKEMON_TOWER_4F = 0x91
    POKEMON_TOWER_5F = 0x92
    POKEMON_TOWER_6F = 0x93
    POKEMON_TOWER_7F = 0x94
    LAVENDER_HOUSE_1 = 0x95
    LAVENDER_MART = 0x96
    LAVENDER_HOUSE_2 = 0x97
    FUCHSIA_MART = 0x98
    FUCHSIA_HOUSE_1 = 0x99
    FUCHSIA_POKECENTER = 0x9A
    FUCHSIA_HOUSE_2 = 0x9B
    SAFARI_ZONE_ENTRANCE = 0x9C
    FUCHSIA_GYM = 0x9D
    FUCHSIA_MEETING_ROOM = 0x9E
    SEAFOAM_ISLANDS_B1F = 0x9F
    SEAFOAM_ISLANDS_B2F = 0xA0
    SEAFOAM_ISLANDS_B3F = 0xA1
    SEAFOAM_ISLANDS_B4F = 0xA2
    VERMILION_HOUSE_2 = 0xA3
    VERMILION_HOUSE_3 = 0xA4
    POKEMON_MANSION_1F = 0xA5
    CINNABAR_GYM = 0xA6
    CINNABAR_LAB_1 = 0xA7
    CINNABAR_LAB_2 = 0xA8
    CINNABAR_LAB_3 = 0xA9
    CINNABAR_LAB_4 = 0xAA
    CINNABAR_POKECENTER = 0xAB
    CINNABAR_MART = 0xAC
    INDIGO_PLATEAU_LOBBY = 0xAE
    COPYCATS_HOUSE_1F = 0xAF
    COPYCATS_HOUSE_2F = 0xB0
    FIGHTING_DOJO = 0xB1
    SAFFRON_GYM = 0xB2
    SAFFRON_HOUSE_1 = 0xB3
    SAFFRON_MART = 0xB4
    SILPH_CO_1F = 0xB5
    SAFFRON_POKECENTER = 0xB6
    SAFFRON_HOUSE_2 = 0xB7
    ROUTE_15_GATE_1F = 0xB8
    ROUTE_15_GATE_2F = 0xB9
    ROUTE_16_GATE_1F = 0xBA
    ROUTE_16_GATE_2F = 0xBB
    ROUTE_16_HOUSE = 0xBC
    ROUTE_12_HOUSE = 0xBD
    ROUTE_18_GATE_1F = 0xBE
    ROUTE_18_GATE_2F = 0xBF
    SEAFOAM_ISLANDS_1F = 0xC0
    ROUTE_22_GATE = 0xC1
    VICTORY_ROAD_2F = 0xC2
    ROUTE_12_GATE_2F = 0xC3
    VERMILION_HOUSE_4 = 0xC4
    DIGLETTS_CAVE = 0xC5
    VICTORY_ROAD_3F = 0xC6
    ROCKET_HIDEOUT_B1F = 0xC7
    ROCKET_HIDEOUT_B2F = 0xC8
    ROCKET_HIDEOUT_B3F = 0xC9
    ROCKET_HIDEOUT_B4F = 0xCA
    ROCKET_HIDEOUT_ELEVATOR = 0xCB
    SILPH_CO_2F = 0xCF
    SILPH_CO_3F = 0xD0
    SILPH_CO_4F = 0xD1
    SILPH_CO_5F = 0xD2
    SILPH_CO_6F = 0xD3
    SILPH_CO_7F = 0xD4
    SILPH_CO_8F = 0xD5
    POKEMON_MANSION_2F = 0xD6
    POKEMON_MANSION_3F = 0xD7
    POKEMON_MANSION_B1F = 0xD8
    SAFARI_ZONE_EAST = 0xD9
    SAFARI_ZONE_NORTH = 0xDA
    SAFARI_ZONE_WEST = 0xDB
    SAFARI_ZONE_CENTER = 0xDC
    SAFARI_ZONE_CENTER_REST_HOUSE = 0xDD
    SAFARI_ZONE_SECRET_HOUSE = 0xDE
    SAFARI_ZONE_WEST_REST_HOUSE = 0xDF
    SAFARI_ZONE_EAST_REST_HOUSE = 0xE0
    SAFARI_ZONE_NORTH_REST_HOUSE = 0xE1
    CERULEAN_CAVE_2F = 0xE2
    CERULEAN_CAVE_B1F = 0xE3
    CERULEAN_CAVE_1F = 0xE4
    NAME_RATERS_HOUSE = 0xE5
    CERULEAN_BADGE_HOUSE = 0xE6
    ROCK_TUNNEL_B1F = 0xE8
    SILPH_CO_9F = 0xE9
    SILPH_CO_10F = 0xEA
    SILPH_CO_11F = 0xEB
    SILPH_CO_ELEVATOR = 0xEC
    TRADE_CENTER = 0xEF
    COLOSSEUM = 0xF0
    LORELEI = 0xF5
    BRUNO = 0xF6
    AGATHA = 0xF7


class Badge(IntFlag):
    """Flags for gym badges"""

    BOULDER = 1 << 0
    CASCADE = 1 << 1
    THUNDER = 1 << 2
    RAINBOW = 1 << 3
    SOUL = 1 << 4
    MARSH = 1 << 5
    VOLCANO = 1 << 6
    EARTH = 1 << 7

@dataclass
class PokemonData:
    """Complete Pokemon data structure"""

    species_id: int
    species_name: str
    current_hp: int
    max_hp: int
    level: int
    status: StatusCondition
    type1: PokemonType
    type2: PokemonType | None
    moves: list[str]  # Move names
    move_pp: list[int]  # PP for each move
    trainer_id: int
    nickname: str | None = None
    experience: int | None = None
    
    @property
    def is_asleep(self) -> bool:
        """Check if the Pokémon is asleep"""
        return self.status.is_asleep
        
    @property
    def status_name(self) -> str:
        """Return a human-readable status name"""
        if self.is_asleep:
            return "SLEEP"
        elif self.status & StatusCondition.PARALYSIS:
            return "PARALYSIS"
        elif self.status & StatusCondition.FREEZE:
            return "FREEZE"
        elif self.status & StatusCondition.BURN:
            return "BURN"
        elif self.status & StatusCondition.POISON:
            return "POISON"
        else:
            return "OK"

class PokemonRedReader:
    """Reads and interprets memory values from Pokemon Red"""

    def __init__(self, memory_view):
        """Initialize with a PyBoy memory view object"""
        self.memory = memory_view
        self.observation = {}
    
    def read_memory(self, addr: str | int) -> int:
        if isinstance(addr, str):
            return self.memory[self.symbol_lookup(addr)[1]]
        return self.memory[addr]
    
    
    def write_memory(self, addr: str | int, value: int):
        if isinstance(addr, str):
            self.memory[self.symbol_lookup(addr)[1]] = value
        else:
            self.memory[addr] = value

    def read_money(self) -> int:
        """Read the player's money in Binary Coded Decimal format"""
        b1 = self.memory[0xD349]  # Least significant byte
        b2 = self.memory[0xD348]  # Middle byte
        b3 = self.memory[0xD347]  # Most significant byte
        money = (
            ((b3 >> 4) * 100000)
            + ((b3 & 0xF) * 10000)
            + ((b2 >> 4) * 1000)
            + ((b2 & 0xF) * 100)
            + ((b1 >> 4) * 10)
            + (b1 & 0xF)
        )
        return money

    def _convert_text(self, bytes_data: list[int]) -> str:
        """Convert Pokemon text format to ASCII"""
        result = ""
        for b in bytes_data:
            if b == 0x50:  # End marker
                break
            elif b == 0x4E:  # Line break
                result += "\n"
            # Main character ranges
            elif 0x80 <= b <= 0x99:  # A-Z
                result += chr(b - 0x80 + ord("A"))
            elif 0xA0 <= b <= 0xB9:  # a-z
                result += chr(b - 0xA0 + ord("a"))
            elif 0xF6 <= b <= 0xFF:  # Numbers 0-9
                result += str(b - 0xF6)
            # Punctuation characters (9A-9F)
            elif b == 0x9A:  # (
                result += "("
            elif b == 0x9B:  # )
                result += ")"
            elif b == 0x9C:  # :
                result += ":"
            elif b == 0x9D:  # ;
                result += ";"
            elif b == 0x9E:  # [
                result += "["
            elif b == 0x9F:  # ]
                result += "]"
            # Special characters
            elif b == 0x7F:  # Space
                result += " "
            elif b == 0x6D:  # : (also appears here)
                result += ":"
            elif b == 0x54:  # POKé control character
                result += "POKé"
            elif b == 0xBA:  # é
                result += "é"
            elif b == 0xBB:  # 'd
                result += "'d"
            elif b == 0xBC:  # 'l
                result += "'l"
            elif b == 0xBD:  # 's
                result += "'s"
            elif b == 0xBE:  # 't
                result += "'t"
            elif b == 0xBF:  # 'v
                result += "'v"
            elif b == 0xE1:  # PK
                result += "Pk"
            elif b == 0xE2:  # MN
                result += "Mn"
            elif b == 0xE3:  # -
                result += "-"
            elif b == 0xE6:  # ?
                result += "?"
            elif b == 0xE7:  # !
                result += "!"
            elif b == 0xE8:  # .
                result += "."
            elif b == 0xE9:  # .
                result += "."
            # E-register special characters
            elif b == 0xE0:  # '
                result += "'"
            elif b == 0xE1:  # PK
                result += "POKé"
            elif b == 0xE2:  # MN
                result += "MON"
            elif b == 0xE3:  # -
                result += "-"
            elif b == 0xE4:  # 'r
                result += "'r"
            elif b == 0xE5:  # 'm
                result += "'m"
            elif b == 0xE6:  # ?
                result += "?"
            elif b == 0xE7:  # !
                result += "!"
            elif b == 0xE8:  # .
                result += "."
            elif b == 0xE9:  # ア
                result += "ア"
            elif b == 0xEA:  # ウ
                result += "ウ"
            elif b == 0xEB:  # エ
                result += "エ"
            elif b == 0xEC:  # ▷
                result += "▷"
            elif b == 0xED:  # ►
                result += "►"
            elif b == 0xEE:  # ▼
                result += "▼"
            elif b == 0xEF:  # ♂
                result += "♂"
            # F-register special characters
            elif b == 0xF0:  # ♭
                result += "♭"
            elif b == 0xF1:  # ×
                result += "×"
            elif b == 0xF2:  # .
                result += "."
            elif b == 0xF3:  # /
                result += "/"
            elif b == 0xF4:  # ,
                result += ","
            elif b == 0xF5:  # ♀
                result += "♀"
            # Numbers 0-9 (0xF6-0xFF)
            elif 0xF6 <= b <= 0xFF:
                result += str(b - 0xF6)
            else:
                # For debugging, show the hex value of unknown characters
                result += f"[{b:02X}]"
        return result.strip()

    def read_player_name(self) -> str:
        """Read the player's name"""
        name_bytes = self.memory[0xD158:0xD163]
        return self._convert_text(name_bytes)

    def read_rival_name(self) -> str:
        """Read rival's name"""
        name_bytes = self.memory[0xD34A:0xD351]
        return self._convert_text(name_bytes)

    def read_badges(self) -> list[str]:
        """Read obtained badges as list of names"""
        badge_byte = self.memory[0xD356]
        badges = []

        if badge_byte & Badge.BOULDER:
            badges.append("BOULDER")
        if badge_byte & Badge.CASCADE:
            badges.append("CASCADE")
        if badge_byte & Badge.THUNDER:
            badges.append("THUNDER")
        if badge_byte & Badge.RAINBOW:
            badges.append("RAINBOW")
        if badge_byte & Badge.SOUL:
            badges.append("SOUL")
        if badge_byte & Badge.MARSH:
            badges.append("MARSH")
        if badge_byte & Badge.VOLCANO:
            badges.append("VOLCANO")
        if badge_byte & Badge.EARTH:
            badges.append("EARTH")

        if badges:
            return badges
        else:
            return ["none"]

    def read_party_size(self) -> int:
        """Read number of Pokemon in party"""
        return self.memory[0xD163]

    def read_party_pokemon(self) -> list[PokemonData]:
        """Read all Pokemon currently in the party with full data"""
        party = []
        party_size = self.read_party_size()

        # Base addresses for party Pokemon data
        base_addresses = [0xD16B, 0xD197, 0xD1C3, 0xD1EF, 0xD21B, 0xD247]
        nickname_addresses = [0xD2B5, 0xD2C0, 0xD2CB, 0xD2D6, 0xD2E1, 0xD2EC]

        for i in range(party_size):
            addr = base_addresses[i]

            # Read experience (3 bytes)
            exp = (
                (self.memory[addr + 0x1A] << 16)
                + (self.memory[addr + 0x1B] << 8)
                + self.memory[addr + 0x1C]
            )

            # Read moves and PP
            moves = []
            move_pp = []
            for j in range(4):
                move_id = self.memory[addr + 8 + j]
                if move_id != 0:
                    moves.append(Move(move_id).name.replace("_", " "))
                    move_pp.append(self.memory[addr + 0x1D + j])

            # Read nickname
            nickname = self._convert_text(
                self.memory[nickname_addresses[i] : nickname_addresses[i] + 11]
            )

            type1 = PokemonType(self.memory[addr + 5])
            type2 = PokemonType(self.memory[addr + 6])
            # If both types are the same, only show one type
            if type1 == type2:
                type2 = None

            try:
                species_id = self.memory[addr]
                # Use PokemonID enum to map ID to species name
                species_name = PokemonID(species_id).name.replace("_", " ")
            except (ValueError, KeyError):
                # Skip unknown species IDs
                continue
            status_value = self.memory[addr + 4]
            
            pokemon = PokemonData(
                species_id=self.memory[addr],
                species_name=species_name,
                current_hp=(self.memory[addr + 1] << 8) + self.memory[addr + 2],
                max_hp=(self.memory[addr + 0x22] << 8) + self.memory[addr + 0x23],
                level=self.memory[addr + 0x21],  # Using actual level
                status=StatusCondition(status_value),
                type1=type1,
                type2=type2,
                moves=moves,
                move_pp=move_pp,
                trainer_id=(self.memory[addr + 12] << 8) + self.memory[addr + 13],
                nickname=nickname,
                experience=exp,
            )
            party.append(pokemon)

        return party

    def read_game_time(self) -> tuple[int, int, int]:
        """Read game time as (hours, minutes, seconds)"""
        hours = (self.memory[0xDA40] << 8) + self.memory[0xDA41]
        minutes = self.memory[0xDA42]
        seconds = self.memory[0xDA44]
        return (hours, minutes, seconds)

    def read_location(self) -> str:
        """Read current location name"""
        map_id = self.memory[0xD35E]
        return MapLocation(map_id).name.replace("_", " ")

    def read_current_map_id(self) -> int:
        """Read the current raw map ID from memory (wCurMapID at 0xD35E)"""
        return self.memory[0xD35E]

    def read_tileset(self) -> str:
        """Read current map's tileset name"""
        tileset_id = self.memory[0xD367]
        try:
            return Tileset(tileset_id).name.replace("_", " ")
        except ValueError:
            return f"UNKNOWN_TILESET_{tileset_id:02X}"

    def read_raw_tileset_id(self) -> int | None:
        """Read current map's raw tileset ID"""
        try:
            return self.memory[0xD367]
        except IndexError:
            return None
        
    def read_tileset_enum(self) -> Tileset:
        """Reads the current tileset ID and returns the corresponding Tileset enum member."""
        # Ensure Tileset enum is imported: from enum import Enum (if not already)
        # Ensure logger is available: import logging; logger = logging.getLogger(__name__)
        tileset_id = self.memory[0xD367]
        # Find the Tileset enum member matching the value
        for member in Tileset:
            if member.value == tileset_id:
                return member
        logger.warning(f"Unknown tileset ID: {tileset_id}")
        # Return a default or handle the error appropriately
        # Returning OVERWORLD as a fallback, but ideally, all tilesets should be in the enum.
        return Tileset.OVERWORLD

    def read_map_tile_id(self, x: int, y: int) -> int:
        """Read the visual tile ID at the given map coordinates (not screen coordinates)."""
        # Based on https://github.com/pret/pokered/blob/master/ram/wram.asm#L1431
        # wTileMap = $CC26 (pointer to current map tile data)
        # Need map width to calculate offset
        map_width = self.memory[0xD35E] # wCurMapWidth
        tile_map_ptr = (self.memory[0xCC27] << 8) | self.memory[0xCC26]
        offset = y * map_width + x
        tile_id = self.memory[tile_map_ptr + offset]
        return tile_id

    def read_coordinates(self) -> tuple[int, int]:
        """Read player's current X,Y coordinates"""
        return (self.memory[0xD362], self.memory[0xD361])

    def read_player_direction(self) -> int:
        """Read the player's facing direction from memory (wSpritePlayerStateData1FacingDirection at 0xC109)"""
        return self.memory[0xC109]

    def read_coins(self) -> int:
        """Read game corner coins"""
        return (self.memory[0xD5A4] << 8) + self.memory[0xD5A5]

    def read_item_count(self) -> int:
        """Read number of items in inventory"""
        return self.memory[0xD31D]

    def read_items(self) -> list[tuple[str, int]]:
        """Read all items in inventory with proper item names"""
        # Revised mapping based on the game's internal item numbering
        ITEM_NAMES = {
            0x01: "MASTER BALL",
            0x02: "ULTRA BALL",
            0x03: "GREAT BALL",
            0x04: "POKé BALL",
            0x05: "TOWN MAP",
            0x06: "BICYCLE",
            0x07: "???",
            0x08: "SAFARI BALL",
            0x09: "POKéDEX",
            0x0A: "MOON STONE",
            0x0B: "ANTIDOTE",
            0x0C: "BURN HEAL",
            0x0D: "ICE HEAL",
            0x0E: "AWAKENING",
            0x0F: "PARLYZ HEAL",
            0x10: "FULL RESTORE",
            0x11: "MAX POTION",
            0x12: "HYPER POTION",
            0x13: "SUPER POTION",
            0x14: "POTION",
            0x1D: "ESCAPE ROPE",
            0x1E: "REPEL",
            0x1F: "OLD AMBER",
            0x20: "FIRE STONE",
            0x21: "THUNDERSTONE",
            0x22: "WATER STONE",
            0x23: "HP UP",
            0x24: "PROTEIN",
            0x25: "IRON",
            0x26: "CARBOS",
            0x27: "CALCIUM",
            0x28: "RARE CANDY",
            0x29: "DOME FOSSIL",
            0x2A: "HELIX FOSSIL",
            0x2B: "SECRET KEY",
            0x2C: "???",  # Blank item
            0x2D: "BIKE VOUCHER",
            0x2E: "X ACCURACY",
            0x2F: "LEAF STONE",
            0x30: "CARD KEY",
            0x31: "NUGGET",
            0x32: "PP UP",
            0x33: "POKé DOLL",
            0x34: "FULL HEAL",
            0x35: "REVIVE",
            0x36: "MAX REVIVE",
            0x37: "GUARD SPEC",
            0x38: "SUPER REPEL",
            0x39: "MAX REPEL",
            0x3A: "DIRE HIT",
            0x3B: "COIN",
            0x3C: "FRESH WATER",
            0x3D: "SODA POP",
            0x3E: "LEMONADE",
            0x3F: "S.S. TICKET",
            0x40: "GOLD TEETH",
            0x41: "X ATTACK",
            0x42: "X DEFEND",
            0x43: "X SPEED",
            0x44: "X SPECIAL",
            0x45: "COIN CASE",
            0x46: "OAK's PARCEL",
            0x47: "ITEMFINDER",
            0x48: "SILPH SCOPE",
            0x49: "POKé FLUTE",
            0x4A: "LIFT KEY",
            0x4B: "EXP.ALL",
            0x4C: "OLD ROD",
            0x4D: "GOOD ROD",
            0x4E: "SUPER ROD",
            0x4F: "PP UP",
            0x50: "ETHER",
            0x51: "MAX ETHER",
            0x52: "ELIXER",
            0x53: "MAX ELIXER",
        }

        items = []
        count = self.read_item_count()

        for i in range(count):
            item_id = self.memory[0xD31E + (i * 2)]
            quantity = self.memory[0xD31F + (i * 2)]

            # Handle TMs (0xC9-0xFE)
            if 0xC9 <= item_id <= 0xFE:
                tm_num = item_id - 0xC8
                item_name = f"TM{tm_num:02d}"
            # Handle HMs (0x01-0x05 in different range - need to test)
            # Not implementing HMs until we confirm their IDs
            elif item_id in ITEM_NAMES:
                item_name = ITEM_NAMES[item_id]
            else:
                item_name = f"UNKNOWN_{item_id:02X}"

            items.append((item_name, quantity))

        return items

    def read_dialog(self) -> str:
        """Read any dialog text currently on screen by scanning the tilemap buffer"""
        # Tilemap buffer is from C3A0 to C507
        buffer_start = 0xC3A0
        buffer_end = 0xC507

        # Get all bytes from the buffer
        buffer_bytes = [self.memory[addr] for addr in range(buffer_start, buffer_end)]

        # Look for sequences of text (ignoring long sequences of 0x7F/spaces)
        text_lines = []
        current_line = []
        space_count = 0
        last_was_border = False

        for b in buffer_bytes:
            if b == 0x7C:  # ║ character
                if last_was_border:
                    # If the last character was a border and this is ║, treat as newline
                    text = self._convert_text(current_line)
                    if text.strip():
                        text_lines.append(text)
                    current_line = []
                    space_count = 0
                else:
                    # current_line.append(b)
                    pass
                last_was_border = True
            elif b == 0x7F:  # Space
                space_count += 1
                current_line.append(b)  # Always keep spaces
                last_was_border = False
            # All text characters: uppercase, lowercase, special chars, punctuation, symbols
            elif (
                # Box drawing (0x79-0x7E)
                # (0x79 <= b <= 0x7E)
                # or
                # Uppercase (0x80-0x99)
                (0x80 <= b <= 0x99)
                or
                # Punctuation (0x9A-0x9F)
                (0x9A <= b <= 0x9F)
                or
                # Lowercase (0xA0-0xB9)
                (0xA0 <= b <= 0xB9)
                or
                # Contractions (0xBA-0xBF)
                (0xBA <= b <= 0xBF)
                or
                # Special characters in E-row (0xE0-0xEF)
                (0xE0 <= b <= 0xEF)
                or
                # Special characters in F-row (0xF0-0xF5)
                (0xF0 <= b <= 0xF5)
                or
                # Numbers (0xF6-0xFF)
                (0xF6 <= b <= 0xFF)
                or
                # Line break
                b == 0x4E
            ):
                space_count = 0
                current_line.append(b)
                last_was_border = (
                    0x79 <= b <= 0x7E
                )  # Track if this is a border character

            # If we see a lot of spaces, might be end of line
            if space_count > 10 and current_line:
                text = self._convert_text(current_line)
                if text.strip():  # Only add non-empty lines
                    text_lines.append(text)
                current_line = []
                space_count = 0
                last_was_border = False

        # Add final line if any
        if current_line:
            text = self._convert_text(current_line)
            if text.strip():
                text_lines.append(text)

        # Join into a single string
        text = "\n".join(text_lines)
        import re  # for filtering gibberish
        # Filter out numeric-gibberish and overly long lines
        filtered = []
        for line in text_lines:
            # Drop lines longer than 100 chars
            if len(line) > 100:
                continue
            # Drop lines consisting of 5 or more digits (e.g., memory dumps)
            if re.fullmatch(r"\d{5,}", line.strip()):
                continue
            filtered.append(line)
        text = "\n".join(filtered)
        # Post-process for name entry context if any valid text remains
        if text and ("lower case" in text.lower() or "UPPER CASE" in text):
            text = text.replace("♭", "ED\n")
        return text

    def read_pokedex_caught_count(self) -> int:
        """Read how many unique Pokemon species have been caught"""
        # Pokedex owned flags are stored in D2F7-D309
        # Each byte contains 8 flags for 8 Pokemon
        # Total of 19 bytes = 152 Pokemon
        caught_count = 0
        for addr in range(0xD2F7, 0xD30A):
            byte = self.memory[addr]
            # Count set bits in this byte
            caught_count += bin(byte).count("1")
        return caught_count

    def read_enemy_party_size(self) -> int:
        """Read number of Pokemon in the enemy trainer's party"""
        return self.memory[ENEMY_PARTY_COUNT]

    def read_enemy_party_species(self) -> list[int]:
        """Read species IDs of all Pokemon in the enemy trainer's party"""
        size = self.read_enemy_party_size()
        species_list = []
        for i in range(size):
            addr = ENEMY_PARTY_SPECIES[i]
            species_list.append(self.memory[addr])
        return species_list

    def read_enemy_current_pokemon_types(self) -> tuple[PokemonType, PokemonType | None]:
        """Read primary and secondary types of the current battling enemy Pokemon"""
        type1_val = self.memory[ENEMYS_POKEMON_TYPES[0]]
        type2_val = self.memory[ENEMYS_POKEMON_TYPES[1]]
        type1 = PokemonType(type1_val)
        type2 = PokemonType(type2_val)
        if type1 == type2:
            type2 = None
        return type1, type2

    def get_type_effectiveness(self, move_type: PokemonType, target_type: PokemonType) -> float:
        """Return damage multiplier for a move type against a target Pokemon type"""
        return POKEMON_MATCH_TYPES.get((move_type.value, target_type.value), 1.0)

    def read_enemy_party_species_names(self) -> list[str]:
        """Return species names of the enemy trainer's party"""
        species_ids = self.read_enemy_party_species()
        names = []
        for sid in species_ids:
            try:
                # Use PokemonID enum for species names
                names.append(PokemonID(sid).name)
            except (ValueError, KeyError):
                names.append(f"UNKNOWN_{sid}")
        return names

    def read_players_move_num(self) -> int:
        """Read number of player moves available in battle menu"""
        return self.memory[PLAYERS_MOVE_NUM]

    def read_players_move_power(self) -> int:
        """Read the power of the currently selected player move"""
        return self.memory[PLAYERS_MOVE_POWER]

    def read_players_move_type(self) -> PokemonType:
        """Read the type of the currently selected player move"""
        return PokemonType(self.memory[PLAYERS_MOVE_TYPE])

    def calculate_effective_power(self, move_type_value: int, target_type: PokemonType) -> float:
        """Calculate the effective power of a move type against a target type"""
        move_type_enum = PokemonType(move_type_value)
        base_power = self.memory[PLAYERS_MOVE_POWER]
        multiplier = self.get_type_effectiveness(move_type_enum, target_type)
        return base_power * multiplier
    
    def get_battle_type(self):
        battle_type = self.memory[BATTLE_TYPE]
        if battle_type == 255:
            battle_type = BattleTypes.DIED  # Died in battle, reassigned to 4 to save bits as 4-255 unused
        return battle_type
    
    def is_in_pre_battle(self):
        return self.memory[CURRENT_OPPONENT] != 0xFF
    
    def get_menu_state(self):
        text_on_screen = self.memory[TEXT_FONT_ON_LOADED]
        if text_on_screen:
            cursor_location, state = self.get_item_menu_context()

            # when text is on screen but menu reg's are clear, we can't be in a menu
            if cursor_location == RedRamMenuKeys.MENU_CLEAR:
                return self.env.GameState.TALKING
            
            # In a sub-box that requires fetching count of menu pos, such as mart items
            sub_state = self._get_menu_item_state(cursor_location)
            if sub_state != RedRamSubMenuValues.UNKNOWN_MENU:
                return sub_state

            # check the bigger of the two submenu's, they have the same val's, to see if we are in a submenu
            sub_state = self._get_sub_menu_state(cursor_location)
            if sub_state != RedRamSubMenuValues.UNKNOWN_MENU:
                return sub_state
            
            # check HM menu overlays
            sub_state = self._get_hm_menu_state(cursor_location)
            if sub_state != RedRamSubMenuValues.UNKNOWN_MENU:
                return sub_state

            return state
        else:
            self.env.ram_interface.write_memory(TEXT_MENU_CURSOR_LOCATION[0], 0x00)
            self.env.ram_interface.write_memory(TEXT_MENU_CURSOR_LOCATION[1], 0x00)
            for i in range(POKEMART_AVAIL_SIZE):
                self.env.ram_interface.write_memory(POKEMART_ITEMS + i, 0x00)

        return self.env.GameState.GAME_STATE_UNKNOWN

    # Insert get_observation into PokemonRedReader
    def get_observation(self, emulator, game):
        """
        Assemble a human-readable observation dict using enums and lookup tables.
        """
        # Import lookup for market items
        from game_data.red_memory_items import ITEM_LOOKUP

        obs = {}
        # Screen placeholder (dimensions)
        img = emulator.get_screenshot()
        # obs["screen"] = f"Image {img.size}"

        # Coordinates & map name
        y, x, m_id = emulator.get_standard_coords() or (0, 0, 0)
        # Global coords
        global_x, global_y = emulator.get_global_coords()
        obs["coordinates"] = f"{MapLocation(m_id).name}, (local: {y},{x}), (global: {global_x},{global_y})"

        # Game state name
        gs = int(game.get_game_state()[0])
        try:
            obs["game_state"] = GameState(gs).name
        except ValueError:
            obs["game_state"] = f"UNKNOWN({gs})"

        # Player party
        p_ids = game.player.get_player_lineup_pokemon()
        obs["player_pokemon"] = [PokemonID(pid).name for pid in p_ids]
        obs["player_levels"] = game.player.get_player_lineup_levels()
        types = game.player.get_player_lineup_types()
        obs["player_types"] = [
            PokemonType(t1).name + ("/" + PokemonType(t2).name if t2 else "")
            for t1, t2 in types
        ]
        hp_list = game.player.get_player_lineup_health()
        obs["player_hp"] = [f"{cur}/{max_}" for max_, cur in hp_list]
        mv_list = game.player.get_player_lineup_moves()
        obs["player_moves"] = [
            [Move(mid).name for mid in mv] for mv in mv_list
        ]
        obs["player_xp"] = game.player.get_player_lineup_xp()
        pp_list = game.player.get_player_lineup_pp()
        obs["player_pp"] = pp_list
        stats_list = game.player.get_player_lineup_stats()
        stat_names = ["atk", "def", "spd", "spe"]
        obs["player_stats"] = [
            {n: val for n, val in zip(stat_names, s)} for s in stats_list
        ]
        st_vals = game.player.get_player_lineup_status()
        obs["player_status"] = [
            StatusCondition(s).get_status_name() for s in st_vals
        ]

        # Battle info
        obs["in_battle"] = game.battle._in_battle_state()
        bt = game.battle.get_battle_type()
        try:
            obs["battle_type"] = BattleTypes(bt).name
        except ValueError:
            obs["battle_type"] = f"UNKNOWN({bt})"
        obs["enemies_left"] = game.battle.get_battles_pokemon_left()
        idx = game.battle.get_player_head_index()
        obs["player_head_index"] = idx
        ph = game.battle.get_player_head_pokemon()
        obs["player_head_pokemon"] = PokemonID(ph).name
        sel = game.battle.get_battle_turn_moves()[0]
        try:
            obs["move_selection"] = Move(sel).name
        except ValueError:
            obs["move_selection"] = f"UNKNOWN({sel})"

        # Progress / rewards
        obs["badges"] = game.player.get_badges()
        obs["pokecenters"] = game.world.get_pokecenter_id()

        # Items
        obs["money"] = game.player.get_player_money()
        obs["bag_ids"] = list(game.items.get_bag_item_ids())
        obs["bag_quantities"] = list(game.items.get_bag_item_quantities())

        # World & shop
        audio = game.world.get_playing_audio_track(), game.world.get_overlay_audio_track()
        obs["audio"] = list(audio)
        mart = game.world.get_pokemart_options()
        obs["pokemart_items"] = [ITEM_LOOKUP.get(i, str(i)) for i in mart]
        obs["item_selection_quan"] = int(game.items.get_item_quantity()[0])
        
        # Add collision map for navigation
        try:
            cmap_data = emulator.get_collision_map()
            obs["collision_map"] = emulator.format_collision_map_simple(cmap_data)
        except Exception:
            obs["collision_map"] = None

        print(f"memory_reader.py get_observation: {obs}")

        self.observation = obs
        return obs

    def clear_dialog_buffer(self) -> None:
        """Clear the dialog buffer to prevent stale dialog text."""
        buffer_start = 0xC3A0
        buffer_end = 0xC507
        for addr in range(buffer_start, buffer_end + 1):
            self.write_memory(addr, 0x7F)
        for addr in range(buffer_start, buffer_end + 1):
            print(f"buffer contents:{self.read_memory(addr)}")
        print(f"Dialog buffer cleared from {buffer_start} to {buffer_end}")

class GameState(IntEnum):
    FILTERED_INPUT = 0
    IN_BATTLE = 1
    BATTLE_ANIMATION = 2
    # catch mon
    TALKING = 3
    EXPLORING = 4
    ON_PC = 5
    POKE_CENTER = 6
    MART = 7
    GYM = 8
    START_MENU = 9
    GAME_MENU = 10
    BATTLE_TEXT = 11
    FOLLOWING_NPC = 12
    GAME_STATE_UNKNOWN = 115
    
class Game:
    def __init__(self, pyboy):
        self.ram_interface = PokemonRedReader(pyboy.memory)

        self.world = World(self)
        self.battle = Battle(self)
        self.items = Items(self)
        self.map = Map(self)
        self.menus = Menus(self)
        self.player = Player(self)

        self.game_state = self.GameState.GAME_STATE_UNKNOWN

        self.process_game_states()

    class GameState(IntEnum):
        FILTERED_INPUT = 0
        IN_BATTLE = 1
        BATTLE_ANIMATION = 2
        # catch mon
        TALKING = 3
        EXPLORING = 4
        ON_PC = 5
        POKE_CENTER = 6
        MART = 7
        GYM = 8
        START_MENU = 9
        GAME_MENU = 10
        BATTLE_TEXT = 11
        FOLLOWING_NPC = 12
        GAME_STATE_UNKNOWN = 115

    
    # Order of precedence is important here, we want to check for battle first, then menus
    def process_game_states(self):
        ORDERED_GAME_STATES = [
            self.menus.get_pre_battle_menu_state,  # For menu's that could be in both battle and non-battle states
            self.battle.get_battle_state,
            self.player.is_following_npc,
            self.menus.get_menu_state,
            # TODO: Locations (mart, gym, pokecenter, etc.)
        ]

        for game_state in ORDERED_GAME_STATES:
            self.game_state = game_state()
            if self.game_state != self.GameState.GAME_STATE_UNKNOWN:
                return self.game_state
        
        self.game_state = self.GameState.EXPLORING
    
    def get_game_state(self):
        return np.array([self.game_state], dtype=np.uint8)
        

    def allow_menu_selection(self, input):
        FILTERED_INPUTS = {
            RedRamMenuValues.START_MENU_POKEDEX: {0},
            RedRamMenuValues.START_MENU_SELF: {0},
            RedRamMenuValues.START_MENU_SAVE: {0},
            RedRamMenuValues.START_MENU_OPTION: {0},
            RedRamMenuValues.START_MENU_QUIT: {0},
            RedRamMenuValues.MENU_SELECT_STATS: {0},
            RedRamMenuValues.BATTLE_SELECT_STATS: {0},
            RedRamMenuValues.PC_OAK: {0},
            RedRamMenuValues.NAME_POKEMON_YES: {0},
            RedRamSubMenuValues.PC_SOMEONE_CONFIRM_STATS: {0},
            RedRamSubMenuValues.PC_SOMEONE_CHANGE_BOX: {0},
        }

        filtered_keys = FILTERED_INPUTS.get(self.game_state, None)
        if filtered_keys is None or input not in filtered_keys:
            return True

        return False

class World:
    def __init__(self, env):
        self.env = env
    
    def get_game_milestones(self):
        return np.array([self.env.ram_interface.read_memory(item) for item in GAME_MILESTONES], dtype=np.uint8)
    
    def get_playing_audio_track(self):
        return self.env.ram_interface.read_memory(AUDIO_CURRENT_TRACK_NO_DELAY)
    
    def get_overlay_audio_track(self):
        return self.env.ram_interface.read_memory(AUDIO_OVERLAY_SOUND)
    
    def get_pokemart_options(self):
        mart = np.zeros((POKEMART_AVAIL_SIZE,), dtype=np.uint8)
        for i in range(POKEMART_AVAIL_SIZE):
            item = self.env.ram_interface.read_memory(POKEMART_ITEMS + i)
            if item == 0xFF:
                break

            mart[i] = item

        return mart
        
    # TODO: Need item costs, 0xcf8f wItemPrices isn't valid: http://www.psypokes.com/rby/shopping.php

    def get_pokecenter_id(self):
        return self.env.ram_interface.read_memory(POKECENTER_VISITED)


class Items:
    def __init__(self, env):
        self.env = env
    
    def _get_items_in_range(self, size, index, offset):
        items = [None] * size
        for i in range(size):
            item_val = self.env.ram_interface.read_memory(index + i * offset)
            if item_val == 0xFF:
                items[i] = ""  # Represent empty slots as "Empty"
            elif item_val in ITEM_LOOKUP:
                items[i] = ITEM_LOOKUP[item_val]
            elif item_val == 4:
                items[i] = "Pokeball"  # Add Pokeball to the lookup
            else:
                items[i] = ""  # Handle unknown items
        return items
    
    def get_bag_item_count(self):
        return self.env.ram_interface.read_memory(BAG_TOTAL_ITEMS)

    def get_bag_item_ids(self):
        return np.array(self._get_items_in_range(BAG_SIZE, BAG_ITEMS_INDEX, ITEMS_OFFSET))

    def get_bag_item_quantities(self):
        item_quan = [self.env.ram_interface.read_memory(BAG_ITEM_QUANTITY_INDEX + i * ITEMS_OFFSET) for i in range(self.get_bag_item_count())]
        padded_quan = np.pad(item_quan, (0, BAG_SIZE - len(item_quan)), constant_values=0)
        return np.array(padded_quan, dtype=np.uint8)
    
    def get_pc_item_count(self):
        return self.env.ram_interface.read_memory(PC_TOTAL_ITEMS)
        
    def get_pc_item_ids(self):
        return np.array(self._get_items_in_range(STORAGE_SIZE, PC_ITEMS_INDEX, ITEMS_OFFSET))
    
    def get_pc_item_quantities(self):
        item_quan = [self.env.ram_interface.read_memory(PC_ITEM_QUANTITY_INDEX + i * ITEMS_OFFSET) for i in range(self.get_pc_item_count())]
        padded_quan = np.pad(item_quan, (0, STORAGE_SIZE - len(item_quan)), constant_values=0)
        return np.array(padded_quan, dtype=np.uint8)
    
    def get_pc_pokemon_count(self):
        return self.env.ram_interface.read_memory(BOX_POKEMON_COUNT)
    
    def get_pc_pokemon_stored(self):
        return np.array([(self.env.ram_interface.read_memory(BOX_POKEMON_1 + i * BOX_OFFSET), self.env.ram_interface.read_memory(BOX_POKEMON_1_LEVEL + i * BOX_OFFSET)) for i in range(BOX_SIZE)], dtype=np.uint8)

    def get_item_quantity(self):
        # TODO: need to map sub menu state for buy/sell count
        if self.env.game_state != RedRamMenuValues.ITEM_QUANTITY:
            return np.array([0], dtype=np.float32)
        
        return np.array([self.env.ram_interface.read_memory(ITEM_SELECTION_QUANTITY)], dtype=np.float32)
                
class Map:
    def __init__(self, env):
        self.env = env
        self.x_pos_org, self.y_pos_org, self.n_map_org = None, None, None
        self.visited_pos = {}
        self.visited_pos_order = deque()
        self.new_map = 0  # TODO: Inc/dec to 6
        self.discovered_map = False
        self.moved_location = False  # indicates if the player moved 1 or more spot
        self.discovered_location = False # indicates if the player is in previously unvisited location
        self.location_history = deque()
        self.steps_discovered = 0
        self.collisions = 0
        self.collisions_lookup = {}
        self.visited_maps = set()

        self.visited = np.zeros((1, SCREEN_VIEW_SIZE, SCREEN_VIEW_SIZE), dtype=np.uint8)
        self.simple_screen = np.zeros((SCREEN_VIEW_SIZE, SCREEN_VIEW_SIZE), dtype=np.uint8)
        self.simple_screen_channels = np.zeros((11, SCREEN_VIEW_SIZE, SCREEN_VIEW_SIZE), dtype=np.uint8)
        self.coordinates = np.zeros((3, BITS_PER_BYTE), dtype=np.uint8)  # x,y,map stacked
        #self.tester = RedGymObsTester(self)
        
        

    def _clear_map_obs(self):
        self.visited = np.zeros((1, SCREEN_VIEW_SIZE, SCREEN_VIEW_SIZE), dtype=np.uint8)
        self.simple_screen = np.zeros((SCREEN_VIEW_SIZE, SCREEN_VIEW_SIZE), dtype=np.uint8)
        self.simple_screen_channels = np.zeros((11, SCREEN_VIEW_SIZE, SCREEN_VIEW_SIZE), dtype=np.uint8)
        self.coordinates = np.zeros((3, BITS_PER_BYTE), dtype=np.uint8)


    def _update_collision_lookup(self, collision_ptr):
        if collision_ptr in self.collisions_lookup:
            return
        
        collection_tiles = self.env.game.map.get_collision_tiles()
        self.collisions_lookup[collision_ptr] = collection_tiles


    def _update_simple_screen_obs(self, x_pos_new, y_pos_new, n_map_new):
        collision_ptr = self.env.game.map.get_collision_pointer()
        self._update_collision_lookup(collision_ptr)

        # Extract the 7x7 matrix from the center of the bottom_left_screen_tiles
        top_left_tiles, bottom_left_tiles = self.env.game.map.get_screen_tilemaps()
        bottom_left_tiles_7x7 = bottom_left_tiles[1:1+SCREEN_VIEW_SIZE, 1:1+SCREEN_VIEW_SIZE]
        top_left_tiles_7x7 = top_left_tiles[1:1+SCREEN_VIEW_SIZE, 1:1+SCREEN_VIEW_SIZE]

        tileset_index = self.env.game.map.get_tileset_index()
        sprites = self.env.game.map.get_npc_location_dict()
        warps = self.env.game.map.get_warp_tile_positions()

        callback = lambda x, y, pos: self._walk_simple_screen(x, y, pos, collision_ptr, tileset_index, sprites, warps, bottom_left_tiles_7x7, top_left_tiles_7x7)
        self._walk_screen(x_pos_new, y_pos_new, n_map_new, callback)


    def _update_visited_obs(self, x_pos_new, y_pos_new, n_map_new):
        callback = lambda x, y, pos: self._walk_visited_screen(x, y, pos)
        self._walk_screen(x_pos_new, y_pos_new, n_map_new, callback)

        # DO NOT set cur pos as visited on the obs until the next turn, it REALLY helps the AI
        # ie.. self.visited[3][3] = 0 (this is intentional)


    def _update_pos_obs(self, x_pos_new, y_pos_new, n_map_new):
        try:
            x_pos_binary = format(x_pos_new, f'0{BITS_PER_BYTE}b')
            y_pos_binary = format(y_pos_new, f'0{BITS_PER_BYTE}b')
            m_pos_binary = format(n_map_new, f'0{BITS_PER_BYTE}b')
        
            # appends the x,y, pos binary form to the bottom of the screen and visited matrix's
            for i, bit in enumerate(x_pos_binary):
                self.coordinates[0][i] = bit

            for i, bit in enumerate(y_pos_binary):
                self.coordinates[1][i] = bit

            for i, bit in enumerate(m_pos_binary):
                self.coordinates[2][i] = bit

        except Exception as e:
            print(f"An error occurred: {e}")
            self.env.support.save_and_print_info(False, True, True)
            self.env.support.save_debug_string("An error occurred: {e}")
            assert(True)


    def _walk_screen(self, x_pos_new, y_pos_new, n_map_new, callback):
        center_x = center_y = SCREEN_VIEW_SIZE // 2

        for y in range(SCREEN_VIEW_SIZE):
            for x in range(SCREEN_VIEW_SIZE):
                center_x = center_y = SCREEN_VIEW_SIZE // 2
                x_offset = x - center_x
                y_offset = y - center_y
                current_pos = x_pos_new + x_offset, y_pos_new + y_offset, n_map_new

                callback(x, y, current_pos)


    def _walk_visited_screen(self, x, y, pos):
        if pos in self.visited_pos:
            self.visited[0][y][x] = 0
        else:
            self.visited[0][y][x] = 1


    def _update_tileset_openworld(self, bottom_left_tiles_7x7, x, y):
        if bottom_left_tiles_7x7[y][x] == 0x36 or bottom_left_tiles_7x7[y][x] == 0x37:  # Jump Down Ledge
            self.simple_screen[y][x] = 6
        elif bottom_left_tiles_7x7[y][x] == 0x27:  # Jump Left Ledge
            self.simple_screen[y][x] = 7
        elif bottom_left_tiles_7x7[y][x] == 0x1D:  # Jump Right Ledge
            self.simple_screen[y][x] = 8
        elif bottom_left_tiles_7x7[y][x] == 0x52:  # Grass
            self.simple_screen[y][x] = 2
        elif bottom_left_tiles_7x7[y][x] == 0x14:  # Water
            self.simple_screen[y][x] = 3
        elif bottom_left_tiles_7x7[y][x] == 0x3D:  # Tree
            self.simple_screen[y][x] = 10


    def _update_tileset_cave(self, x, y, bottom_left_tiles_7x7, tiles_top_left):
        if tiles_top_left[y][x] == 0x29:  # One Pixel Wall Tile (NOTE: Top Left tile contains the tile identifier)
            self.simple_screen[y][x] = 5
        elif bottom_left_tiles_7x7[y][x] == 0x14:  # Water
            self.simple_screen[y][x] = 3
        elif bottom_left_tiles_7x7[y][x] == 0x20 or bottom_left_tiles_7x7[y][x] == 0x05 or bottom_left_tiles_7x7[y][x] == 0x15:  # Cave Ledge, Floor or Stairs
            self.simple_screen[y][x] = 2


    def _update_tileset_cemetery(self, x, y, bottom_left_tiles_7x7):
        if bottom_left_tiles_7x7[y][x] == 0x01:  # Cemetery Floor
            self.simple_screen[y][x] = 2


    def _update_tileset_forest(self, x, y, bottom_left_tiles_7x7):
        if bottom_left_tiles_7x7[y][x] == 0x20:  # Grass
            self.simple_screen[y][x] = 2
    

    def _update_matrix_with_npcs(self, x, y, pos, sprites):
        if pos in sprites:
            self.simple_screen[y][x] = 9


    def _update_matrix_with_warps(self, x, y, pos, warps):
        location = (pos[0], pos[1])
        if self.simple_screen[y][x] != 0 and location in warps:
            self.simple_screen[y][x] = 4


    def _walk_simple_screen(self, x, y, pos, collision_ptr, tileset_index, sprites, warps, bottom_left_tiles_7x7, top_left_tiles_7x7):
        if bottom_left_tiles_7x7[y][x] in self.collisions_lookup[collision_ptr]:
            self.simple_screen[y][x] = 1  # Walkable
        else:
            self.simple_screen[y][x] = 0  # Wall

        if tileset_index == 0x00:
            self._update_tileset_openworld(bottom_left_tiles_7x7, x, y)
        elif tileset_index == 0x11:
            self._update_tileset_cave(x, y, bottom_left_tiles_7x7, top_left_tiles_7x7)
        elif tileset_index == 0x0F:
            self._update_tileset_cemetery(x, y, bottom_left_tiles_7x7)
        elif tileset_index == 0x03:
            self._update_tileset_forest(x, y, bottom_left_tiles_7x7)

        self._update_matrix_with_npcs(x, y, pos, sprites)
        self._update_matrix_with_warps(x, y, pos, warps)

    def _update_simple_screen_channel_obs(self):
        self.simple_screen_channels = np.zeros((11, SCREEN_VIEW_SIZE, SCREEN_VIEW_SIZE), dtype=np.uint8)
        for y in range(SCREEN_VIEW_SIZE):
            for x in range(SCREEN_VIEW_SIZE):
                self.simple_screen_channels[self.simple_screen[y][x]][y][x] = 1


    def save_post_action_pos(self):
        x_pos_new, y_pos_new, n_map_new = self.env.game.map.get_current_location()
        self.moved_location = not (self.x_pos_org == x_pos_new and
                                   self.y_pos_org == y_pos_new and
                                   self.n_map_org == n_map_new)

        if self.moved_location:
            # Bug check: AI is only allowed to move 0 or 1 spots per turn, new maps change x,y ref pos so don't count.
            # When the game goes to a new map, it changes m first, then y,x will update on the next turn, still some corner cases like fly, blackout, bike
            if self.new_map:
                self.x_pos_org, self.y_pos_org, self.n_map_org = x_pos_new, y_pos_new, n_map_new
                self.new_map -= 1
            elif n_map_new == self.n_map_org:
                if not (abs(self.x_pos_org - x_pos_new) + abs(self.y_pos_org - y_pos_new) <= 1):
                    self.update_map_stats()

                    debug_str = ""
                    #while len(self.location_history):
                    #    debug_str += self.location_history.popleft()
                    # self.env.support.save_debug_string(debug_str)
                    # assert False
            else:
                self.new_map = 6

            if (x_pos_new, y_pos_new, n_map_new) in self.visited_pos:
                self.discovered_location = True

            if n_map_new not in self.visited_maps:
                self.visited_maps.add(n_map_new)
                self.discovered_map = True

    def save_pre_action_pos(self):
        self.x_pos_org, self.y_pos_org, self.n_map_org = self.env.game.map.get_current_location()
        self.discovered_location = False
        self.discovered_map = False

        if len(self.visited_pos_order) > MAX_STEP_MEMORY:
            del_key = self.visited_pos_order.popleft()
            del self.visited_pos[del_key]

        current_pos = (self.x_pos_org, self.y_pos_org, self.n_map_org)
        if current_pos not in self.visited_pos:
            self.visited_pos[current_pos] = self.env.step_count
            self.visited_pos_order.append(current_pos)


    def update_map_stats(self):
        new_x_pos, new_y_pos, new_map_n = self.env.game.map.get_current_location()

        debug_str = f"Moved: {self.moved_location} \n"
        if self.new_map:
            debug_str = f"\nNew Map!\n"
        debug_str += f"Start location: {self.x_pos_org, self.y_pos_org, self.n_map_org} \n"
        debug_str += f"New location: {new_x_pos, new_y_pos, new_map_n} \n"
        debug_str += f"\n"
        debug_str += f"{self.simple_screen}"
        debug_str += f"\n"
        debug_str += f"{self.visited}"

        if len(self.location_history) > 10:
            self.location_history.popleft()
        self.location_history.append(debug_str)


    def get_exploration_reward(self):
        x_pos, y_pos, map_n = self.env.game.map.get_current_location()
        if not self.moved_location:
            if (not (self.env.gameboy.action_history[0] == 5 or self.env.gameboy.action_history[0] == 6) and 
                self.env.game.get_game_state() ==  self.env.game.GameState.EXPLORING and self.new_map == False):
                self.collisions += 1
        
            return 0
        elif (x_pos, y_pos, map_n) in self.visited_pos:
            return 0.01
        
        # FALL THROUGH: In new location

        self.steps_discovered += 1
        # Bonus for exploring pokecenter before talking to first nurse, ie. encourage early learning of pokecenter healing
        # Note, that pokecenter will be one on entering the first pokecenter building and 2 when talking to the first nurse
        if self.env.world.pokecenter_history <= 3 and self.env.game.world.get_playing_audio_track() == 0xBD:
            return 10
        else:
            return 1
        
    def get_map_reward(self):
        _STARTING_MAPS = {
            0x00,  # Pallet Town
            0x28,  # Oak's Lab
            0x25,  # Moms house 1st floor
            0x26,  # Moms house 2nd floor
            0x27,  # Rival's house
        }

        map_n = self.env.game.map.get_current_map()

        if map_n not in _STARTING_MAPS and self.discovered_map:
            return len(self.visited_maps) * 2
        
        return 0

    def update_map_obs(self):
        if self.env.game.battle.in_battle:
            self._clear_map_obs()  # Don't show the map while in battle b/c human can't see map when in battle
        else:
            x_pos_new, y_pos_new, n_map_new = self.env.game.map.get_current_location()

            self._update_visited_obs(x_pos_new, y_pos_new, n_map_new)
            self._update_simple_screen_obs(x_pos_new, y_pos_new, n_map_new)
            self._update_pos_obs(x_pos_new, y_pos_new, n_map_new)
            self._update_simple_screen_channel_obs()
            
        self.update_map_stats()

    
    
    def get_current_map(self):
        return self.env.ram_interface.read_memory(PLAYER_MAP)
    
    def get_current_location(self):
        return self.env.ram_interface.read_memory(PLAYER_LOCATION_X), self.env.ram_interface.read_memory(PLAYER_LOCATION_Y), self.get_current_map()
    
    def get_collision_pointer(self):
        return np.uint16((self.env.ram_interface.read_memory(TILE_COLLISION_PTR_1) << 8) + self.env.ram_interface.read_memory(TILE_COLLISION_PTR_2))
    
    def get_tileset_index(self):
        return self.env.ram_interface.read_memory(TILESET_INDEX)

    def get_collision_tiles(self):
        collision_ptr = self.get_collision_pointer()
        collection_tiles = set()
        while True:
            collision = self.env.ram_interface.read_memory(collision_ptr)
            if collision == 0xFF:
                break

            collection_tiles.add(collision)
            collision_ptr += 1

        return collection_tiles
    
    def get_screen_tilemaps(self):
        bsm = self.env.ram_interface.pyboy.botsupport_manager()
        ((scx, scy), (wx, wy)) = bsm.screen().tilemap_position()
        tilemap = np.array(bsm.tilemap_background()[:, :])
        screen_tiles = (np.roll(np.roll(tilemap, -scy // 8, axis=0), -scx // 8, axis=1)[:18, :20] - 0x100)

        top_left_tiles = screen_tiles[:screen_tiles.shape[0]: 2,::2]
        bottom_left_tiles = screen_tiles[1: 1 + screen_tiles.shape[0]: 2,::2]

        return top_left_tiles, bottom_left_tiles
    

    def get_npc_location_dict(self, skip_moving_npc=False):
        # Moderate testing show's NPC's are never on screen during map transitions
        sprites = {}
        for i, sprite_addr in enumerate(SPRITE_STARTING_ADDRESSES):
            on_screen = self.env.ram_interface.read_memory(sprite_addr + 0x0002)

            if on_screen == 0xFF:
                continue

            # Moving sprites can cause complexity, use at discretion
            if skip_moving_npc and self.env.ram_interface.read_memory(sprite_addr + 0x0106) != 0xFF:
                continue
            
            picture_id = self.env.ram_interface.read_memory(sprite_addr)
            x_pos = self.env.ram_interface.read_memory(sprite_addr + 0x0105) - 4  # topmost 2x2 tile has value 4), thus the offset
            y_pos = self.env.ram_interface.read_memory(sprite_addr + 0x0104) - 4  # topmost 2x2 tile has value 4), thus the offset
            # facing = self.env.ram_interface.read_memory(sprite_addr + 0x0009)

            sprites[(x_pos, y_pos, self.get_current_map())] = picture_id
            
        return sprites
    
    def get_warp_tile_count(self):
        return self.env.ram_interface.read_memory(WARP_TILE_COUNT)
    
    def get_warp_tile_positions(self):
        warp_tile_count = self.get_warp_tile_count()
        warp_tile_positions = set()
        for i in range(warp_tile_count):
            warp_tile_positions.add((self.env.ram_interface.read_memory(WARP_TILE_X_ENTRY + i * WARP_TILE_ENTRY_OFFSET),
                                     self.env.ram_interface.read_memory(WARP_TILE_Y_ENTRY + i * WARP_TILE_ENTRY_OFFSET)))
        
        return warp_tile_positions

class Menus:
    def __init__(self, env):
        self.env = env

    def _get_sub_menu_item_number(self):
        return self.env.ram_interface.read_memory(TEXT_MENU_CURSOR_COUNTER_1) + self.env.ram_interface.read_memory(TEXT_MENU_CURSOR_COUNTER_2) + 1

    def get_item_menu_context(self):
        cursor_location = (self.env.ram_interface.read_memory(TEXT_MENU_CURSOR_LOCATION[0]),
                    self.env.ram_interface.read_memory(TEXT_MENU_CURSOR_LOCATION[1]))
        return cursor_location, TEXT_MENU_CURSOR_LOCATIONS.get(cursor_location, RedRamMenuValues.UNKNOWN_MENU)
    
    def get_pre_battle_menu_state(self):
        text_on_screen = self.env.ram_interface.read_memory(TEXT_FONT_ON_LOADED)
        if not text_on_screen:
            return self.env.GameState.GAME_STATE_UNKNOWN
                
        cursor_location, state = self.get_item_menu_context()
        text_dst_ptr = self.env.ram_interface.read_memory(TEXT_DST_POINTER)
        id_working_reg = self.env.ram_interface.read_memory(PRE_DEF_ID)
        if (state == RedRamMenuValues.MENU_YES or state == RedRamMenuValues.MENU_NO) and id_working_reg == 0x2D:
            if text_dst_ptr == 0xF2 and state == RedRamMenuValues.MENU_YES:
                return RedRamMenuValues.OVERWRITE_MOVE_YES
            elif text_dst_ptr == 0xF2 and state == RedRamMenuValues.MENU_NO:
                return RedRamMenuValues.OVERWRITE_MOVE_NO
            elif text_dst_ptr == 0xB9 and state == RedRamMenuValues.MENU_YES:
                return RedRamMenuValues.ABANDON_MOVE_YES
            elif text_dst_ptr == 0xB9 and state == RedRamMenuValues.MENU_NO:
                return RedRamMenuValues.ABANDON_MOVE_NO
            elif text_dst_ptr == 0xEE or text_dst_ptr == 0xF0:  # would otherwise be default y/n on a text screen
                return self.env.GameState.TALKING
        elif cursor_location == RedRamMenuKeys.BATTLE_MART_PC_ITEM_N and text_dst_ptr == 0xB9 and id_working_reg == 0x2D:  # Shares submenu w/ mart 3-10 items
            return RedRamMenuValues.OVERWRITE_MOVE_1
        elif (cursor_location == RedRamMenuKeys.OVERWRITE_MOVE_2 or
               cursor_location == RedRamMenuKeys.OVERWRITE_MOVE_3 or
                 cursor_location == RedRamMenuKeys.OVERWRITE_MOVE_4) and text_dst_ptr == 0xB9:
            return state
            
        return self.env.GameState.GAME_STATE_UNKNOWN


    def get_menu_state(self):
        text_on_screen = self.env.ram_interface.read_memory(TEXT_FONT_ON_LOADED)
        if text_on_screen:
            cursor_location, state = self.get_item_menu_context()

            # when text is on screen but menu reg's are clear, we can't be in a menu
            if cursor_location == RedRamMenuKeys.MENU_CLEAR:
                return self.env.GameState.TALKING
            
            # In a sub-box that requires fetching count of menu pos, such as mart items
            sub_state = self._get_menu_item_state(cursor_location)
            if sub_state != RedRamSubMenuValues.UNKNOWN_MENU:
                return sub_state

            # check the bigger of the two submenu's, they have the same val's, to see if we are in a submenu
            sub_state = self._get_sub_menu_state(cursor_location)
            if sub_state != RedRamSubMenuValues.UNKNOWN_MENU:
                return sub_state
            
            # check HM menu overlays
            sub_state = self._get_hm_menu_state(cursor_location)
            if sub_state != RedRamSubMenuValues.UNKNOWN_MENU:
                return sub_state

            return state
        else:
            self.env.ram_interface.write_memory(TEXT_MENU_CURSOR_LOCATION[0], 0x00)
            self.env.ram_interface.write_memory(TEXT_MENU_CURSOR_LOCATION[1], 0x00)
            for i in range(POKEMART_AVAIL_SIZE):
                self.env.ram_interface.write_memory(POKEMART_ITEMS + i, 0x00)

        return self.env.GameState.GAME_STATE_UNKNOWN
    
    def _get_hm_menu_state(self, cursor_location):
        cc50 = self.env.ram_interface.read_memory(0xCC50)        
        cc52 = self.env.ram_interface.read_memory(0xCC52)        
    
        # working reg's are used and set to 41 & 14 when in pokemart healing menu
        if (cc50 == 0x41 and cc52 == 0x14) and (cursor_location == RedRamMenuKeys.POKECENTER_HEAL or cursor_location == RedRamMenuKeys.POKECENTER_CANCEL):
            return RedRamSubMenuValues.UNKNOWN_MENU  # it's known but the next stage will set it to pokecenter

        # working reg's are used and set to 58 & 20 when in HM menu
        if not (cc50 == 0x58 and cc52 == 0x20 and self.env.ram_interface.read_memory(ITEM_COUNT_SCREEN_PEAK) == 0x7C):
            return RedRamSubMenuValues.UNKNOWN_MENU
        
        # Awful hack, strength shift the menu by 1 due to it's length so do another overwrite
        if cursor_location == RedRamMenuKeys.PC_SOMEONE_DEPOSIT_WITHDRAW:
            return RedRamMenuValues.MENU_SELECT_STATS
        elif cursor_location == RedRamMenuKeys.PC_SOMEONE_STATUS:
            return RedRamMenuValues.MENU_SELECT_SWITCH
        elif cursor_location == RedRamMenuKeys.PC_SOMEONE_CANCEL:
            return RedRamMenuValues.MENU_SELECT_CANCEL
                
        cursor_menu_position = self.env.ram_interface.read_memory(TEXT_MENU_LAST_MENU_ITEM)
        max_menu_elem = self.env.ram_interface.read_memory(TEXT_MENU_MAX_MENU_ITEM)
        menu_offset = max_menu_elem - cursor_menu_position - 3  # There are 3 menu's (stats, switch, cancel) 0-indexed

        # There are no HM's after the first 3 menu's
        if menu_offset < 0:
            return RedRamSubMenuValues.UNKNOWN_MENU

        pokemon_selected = self.env.ram_interface.read_memory(0xCC2B)
        move_1, move_2, move_3, move_4 = Pokemon(self.env).get_pokemon_moves(pokemon_selected * PARTY_OFFSET)

        for move in [move_4, move_3, move_2, move_1]:
            if move in HM_MENU_LOOKUP:
                menu_offset -= 1

            if menu_offset < 0:
                return HM_MENU_LOOKUP[move]
    
        return RedRamSubMenuValues.UNKNOWN_MENU


    
    def _get_sub_menu_state(self, cursor_location):
        if PC_POKE_MENU_CURSOR_LOCATIONS.get(cursor_location, RedRamSubMenuValues.UNKNOWN_MENU) == RedRamSubMenuValues.UNKNOWN_MENU:
            return RedRamSubMenuValues.UNKNOWN_MENU

        # Peek at screen memory to detect submenu's which have hard coded menu renderings w/ diff's between them. Reverse engineered.
        pc_menu_screen_peek = self.env.ram_interface.read_memory(PC_SUB_MENU_SCREEN_PEEK)

        # pokemon pc sub menu
        if pc_menu_screen_peek == 0x91:
            if cursor_location != RedRamSubMenuKeys.SUB_MENU_6:  # menu 6 is the same for deposit and withdraw so we have to normalize it
                return PC_POKE_MENU_CURSOR_LOCATIONS.get(cursor_location, RedRamSubMenuValues.UNKNOWN_MENU)
            else:
                pc_menu_screen_peek = self.env.ram_interface.read_memory(PC_SUB_MENU_DEPO_WITH_SCREEN_PEEK)
                return RedRamSubMenuValues.PC_SOMEONE_CONFIRM_WITHDRAW if pc_menu_screen_peek == 0x91 else RedRamSubMenuValues.PC_SOMEONE_CONFIRM_DEPOSIT
            
        # item pc sub menu
        elif pc_menu_screen_peek == 0x93:
            return PC_ITEM_MENU_CURSOR_LOCATIONS.get(cursor_location, RedRamSubMenuValues.UNKNOWN_MENU)
        
        return RedRamSubMenuValues.UNKNOWN_MENU

    def _get_menu_item_state(self, cursor_location):
        if cursor_location == RedRamMenuKeys.BATTLE_MART_PC_ITEM_1 or cursor_location == RedRamMenuKeys.BATTLE_MART_PC_ITEM_2 or cursor_location == RedRamMenuKeys.BATTLE_MART_PC_ITEM_N:
            if self.env.ram_interface.read_memory(ITEM_COUNT_SCREEN_PEAK) == 0x7E:  # 0x7E is the middle pokeball icon on screen, unique to the 3 sub menu pop out
                return RedRamMenuValues.ITEM_QUANTITY
            
            item_number = self._get_sub_menu_item_number()
            return TEXT_MENU_ITEM_LOCATIONS.get(item_number, RedRamMenuValues.ITEM_RANGE_ERROR)
        
        return RedRamSubMenuValues.UNKNOWN_MENU
    

class Pokemon:
    def __init__(self, env):
        self.env = env
    
    def get_pokemon(self, offset):
        return self.env.ram_interface.read_memory(POKEMON_1 + offset)
    
    def get_pokemon_level(self, offset):
        return self.env.ram_interface.read_memory(POKEMON_1_LEVEL_ACTUAL + offset)
    
    def get_pokemon_type(self, offset):
        type_1 = self.env.ram_interface.read_memory(POKEMON_1_TYPES[0] + offset)
        type_2 = self.env.ram_interface.read_memory(POKEMON_1_TYPES[1] + offset)

        return type_1, type_2
    
    def get_pokemon_health(self, offset):
        hp_total = (self.env.ram_interface.read_memory(POKEMON_1_MAX_HP[0] + offset) << 8) + self.env.ram_interface.read_memory(POKEMON_1_MAX_HP[1] + offset)
        hp_avail = (self.env.ram_interface.read_memory(POKEMON_1_CURRENT_HP[0] + offset) << 8) + self.env.ram_interface.read_memory(POKEMON_1_CURRENT_HP[1] + offset)

        return hp_total, hp_avail
    
    def get_pokemon_xp(self, offset):
        xp = ((self.env.ram_interface.read_memory(POKEMON_1_EXPERIENCE[0] + offset) << 16) +
              (self.env.ram_interface.read_memory(POKEMON_1_EXPERIENCE[1] + offset) << 8) +
               self.env.ram_interface.read_memory(POKEMON_1_EXPERIENCE[2] + offset))

        return xp
    
    def get_pokemon_moves(self, offset):
        move_1 = self.env.ram_interface.read_memory(POKEMON_1_MOVES[0]+ offset)
        move_2 = self.env.ram_interface.read_memory(POKEMON_1_MOVES[1]+ offset)
        move_3 = self.env.ram_interface.read_memory(POKEMON_1_MOVES[2]+ offset)
        move_4 = self.env.ram_interface.read_memory(POKEMON_1_MOVES[3]+ offset)

        return move_1, move_2, move_3, move_4
    
    def get_pokemon_pp_avail(self, offset):
        pp_1 = self.env.ram_interface.read_memory(POKEMON_1_PP_MOVES[0]+ offset)
        pp_2 = self.env.ram_interface.read_memory(POKEMON_1_PP_MOVES[1]+ offset)
        pp_3 = self.env.ram_interface.read_memory(POKEMON_1_PP_MOVES[2]+ offset)
        pp_4 = self.env.ram_interface.read_memory(POKEMON_1_PP_MOVES[3]+ offset)

        return pp_1, pp_2, pp_3, pp_4
    
    def get_pokemon_stats(self, offset):
        attack = (self.env.ram_interface.read_memory(POKEMON_1_ATTACK[0] + offset) << 8) + self.env.ram_interface.read_memory(POKEMON_1_ATTACK[1] + offset)
        defense = (self.env.ram_interface.read_memory(POKEMON_1_DEFENSE[0] + offset) << 8) + self.env.ram_interface.read_memory(POKEMON_1_DEFENSE[1] + offset)
        speed = (self.env.ram_interface.read_memory(POKEMON_1_SPEED[0] + offset) << 8) + self.env.ram_interface.read_memory(POKEMON_1_SPEED[1] + offset)
        special = (self.env.ram_interface.read_memory(POKEMON_1_SPECIAL[0] + offset) << 8) + self.env.ram_interface.read_memory(POKEMON_1_SPECIAL[1] + offset)

        return attack, defense, speed, special
    
    def get_pokemon_status(self, offset):
        return self.env.ram_interface.read_memory(POKEMON_1_STATUS + offset)

    def get_pokemon_data_dict(self, party_index=0):
        offset = party_index * PARTY_OFFSET
        pokemon = self.get_pokemon(offset)
        level = self.get_pokemon_level(offset)
        type_1, type_2 = self.get_pokemon_type(offset)
        hp_total, hp_avail = self.get_pokemon_health(offset)
        xp = self.get_pokemon_xp(offset)
        move_1, move_2, move_3, move_4 = self.get_pokemon_moves(offset)
        pp_1, pp_2, pp_3, pp_4 = self.get_pokemon_pp_avail(offset)
        attack, defense, speed, special = self.get_pokemon_stats(offset)
        health_status = self.get_pokemon_status(offset)

        # http://www.psypokes.com/rby/maxstats.php
        return {
            'pokemon': pokemon,
            'level': level,
            'type_1': type_1,
            'type_2': type_2,
            'hp_total': hp_total,  # HP Max is 703
            'hp_avail': hp_avail,
            'xp': xp,
            'move_1': move_1,
            'move_2': move_2,
            'move_3': move_3,
            'move_4': move_4,
            'pp_1': pp_1,
            'pp_2': pp_2,
            'pp_3': pp_3,
            'pp_4': pp_4,
            'attack': attack,  # Max is 366
            'defense': defense,  # Max is 458
            'speed': speed,  # Max is 378
            'special': special,  # Max is 406
            'health_status': health_status
        }


class Player:
    def __init__(self, env):
        self.env = env
    
    def _pokedex_bit_count(self, pokedex_address):
        bit_count = 0
        for i in range(POKEDEX_ADDR_LENGTH):
            binary_value = bin(self.env.ram_interface.read_memory(pokedex_address + i))
            bit_count += binary_value.count('1')

        return bit_count
    
    def _get_lineup_size(self):
        return self.env.ram_interface.read_memory(POKEMON_PARTY_COUNT)

    def get_player_lineup_dict(self):
        return [Pokemon(self.env).get_pokemon_data_dict(i) for i in range(self._get_lineup_size())]

    def get_player_lineup_pokemon(self):
        return [Pokemon(self.env).get_pokemon(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_levels(self):
        return [Pokemon(self.env).get_pokemon_level(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_health(self):
        return [Pokemon(self.env).get_pokemon_health(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_xp(self):
        return [Pokemon(self.env).get_pokemon_xp(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_moves(self):
        return [Pokemon(self.env).get_pokemon_moves(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_pp(self):
        return [Pokemon(self.env).get_pokemon_pp_avail(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_stats(self):
        return [Pokemon(self.env).get_pokemon_stats(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_types(self):
        return [Pokemon(self.env).get_pokemon_type(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def get_player_lineup_status(self):
        return [Pokemon(self.env).get_pokemon_status(i * PARTY_OFFSET) for i in range(self._get_lineup_size())]
    
    def is_following_npc(self):
        if self.env.ram_interface.read_memory(FOLLOWING_NPC_FLAG) != 0x00:
            return self.env.GameState.FOLLOWING_NPC
        
        return self.env.GameState.GAME_STATE_UNKNOWN
    
    def get_badges(self):
        return self.env.ram_interface.read_memory(OBTAINED_BADGES)
    
    def get_pokedex_seen(self):
        return self._pokedex_bit_count(POKEDEX_SEEN)
    
    def get_pokedex_owned(self):
        return self._pokedex_bit_count(POKEDEX_OWNED)
    
    def get_player_money(self):
        # Trigger warning, money is a base16 literal as base 10 numbers, max money 999,999
        money_bytes = [self.env.ram_interface.read_memory(addr) for addr in PLAYER_MONEY]
        money_hex = ''.join([f'{byte:02x}' for byte in money_bytes])
        money_int = int(money_hex, 10)
        return money_int

    def is_player_dead(self):
        return self.env.ram_interface.read_memory(PLAYER_DEAD) == 0xFF


class Battle:
    def __init__(self, env):
        self.env = env
        self.in_battle = False
        self.turns_in_current_battle = 1
        self.new_turn = False
        self.last_turn_count = 0
        self.battle_done = False

    def _in_battle_state(self):
        if self.env.game_state in BATTLE_MENU_STATES or self.env.game_state == self.env.GameState.BATTLE_TEXT:
            return True
        return False
    
    def _loaded_pokemon_address(self):
        party_index = self.env.ram_interface.read_memory(PLAYER_LOADED_POKEMON)
        return party_index * PARTY_OFFSET
    
    def _get_battle_menu_overwrites(self, game_state):
        # These are nasty's in the game where the reg's don't follow the same pattern as the other menu's, so we have to override them.
        # All these overwrites are based off the face we KNOW we're in battle, thus what menu's are/aren't possible.
        if game_state == RedRamMenuValues.PC_LOGOFF:
            game_state = RedRamMenuValues.MENU_YES
        elif game_state == RedRamMenuValues.MENU_SELECT_STATS:  # Corner-case, during battle the sub-menu's for switch/stats are reversed
            game_state = RedRamMenuValues.BATTLE_SELECT_SWITCH
        elif game_state == RedRamMenuValues.MENU_SELECT_SWITCH:
            game_state = RedRamMenuValues.BATTLE_SELECT_STATS

        if (game_state == RedRamMenuValues.MENU_YES or game_state == RedRamMenuValues.MENU_NO):
            text_dst_pointer = self.env.ram_interface.read_memory(TEXT_DST_POINTER)
            if text_dst_pointer == 0xF0 and game_state == RedRamMenuValues.MENU_YES:
                return RedRamMenuValues.NAME_POKEMON_YES
            elif text_dst_pointer == 0xF0 and game_state == RedRamMenuValues.MENU_NO:
                return RedRamMenuValues.NAME_POKEMON_NO
            elif text_dst_pointer == 0xED and game_state == RedRamMenuValues.MENU_YES:
                return RedRamMenuValues.SWITCH_POKEMON_YES
            elif text_dst_pointer == 0xED and game_state == RedRamMenuValues.MENU_NO:
                return RedRamMenuValues.SWITCH_POKEMON_NO

        if (game_state == RedRamMenuValues.MENU_YES or game_state == RedRamMenuValues.MENU_NO or
            game_state == RedRamMenuValues.BATTLE_SELECT_SWITCH or game_state == RedRamMenuValues.BATTLE_SELECT_STATS):
            return game_state
        
        return self.env.GameState.GAME_STATE_UNKNOWN
    
    def _get_battle_menu_state(self, battle_type):
        cursor_location, state = self.env.menus.get_item_menu_context()
        game_state = TEXT_MENU_CURSOR_LOCATIONS.get(cursor_location, RedRamMenuValues.UNKNOWN_MENU)

        game_state = self._get_battle_menu_overwrites(game_state)
        if game_state != self.env.GameState.GAME_STATE_UNKNOWN:
            return game_state
    
        if cursor_location == RedRamMenuKeys.MENU_CLEAR or not battle_type:
            return self.env.GameState.BATTLE_ANIMATION
        
        # Very tricky to figure this one out, there is no clear ID for battle text but we can infer it from a combo of other reg's. Battle text pause get's it 50% of the time
        # but there is a delay sometimes which give false positive on ID'ing menu's. Text box id work's the rest of the time but it shares a common value with pokemon menu so
        # it alone also can't be used but the UNKNOWN_D730 reg in battle is always 0x40 when in the pokemon menu, letting us rule out pokemon menu in battle.
        if ((self.env.ram_interface.read_memory(TEXT_BOX_ID) == 0x01 and self.env.ram_interface.read_memory(UNKNOWN_D730) != 0x40) or
             self.env.ram_interface.read_memory(BATTLE_TEXT_PAUSE_FLAG) == 0x00):
            return self.env.GameState.BATTLE_TEXT

        if state != RedRamMenuValues.UNKNOWN_MENU:
            if self.env.menus._get_menu_item_state(cursor_location) != RedRamSubMenuValues.UNKNOWN_MENU:
                item_number = self.env.ram_interface.read_memory(TEXT_MENU_CURSOR_COUNTER_1) + self.env.ram_interface.read_memory(TEXT_MENU_CURSOR_COUNTER_2) + 1
                state = TEXT_MENU_ITEM_LOCATIONS.get(item_number, RedRamMenuValues.ITEM_RANGE_ERROR)

            return state

        return self.env.GameState.GAME_STATE_UNKNOWN

    def get_battle_state(self):
        battle_type = self.get_battle_type()
        in_pre_battle = self.is_in_pre_battle()

        if not (battle_type or in_pre_battle):
            self.turns_in_current_battle = 1
            self.last_turn_count = 0
            self.in_battle = False
            self.battle_done = False
            self.new_turn = False
            return self.env.GameState.GAME_STATE_UNKNOWN
        
        self.in_battle = True

        turns_in_current_battle = self.env.ram_interface.read_memory(TURNS_IN_CURRENT_BATTLE)
        if turns_in_current_battle != self.last_turn_count:
            self.turns_in_current_battle += 1 
            self.last_turn_count = turns_in_current_battle
            self.new_turn = True
        else:
            self.new_turn = False

        return self._get_battle_menu_state(battle_type)
    
    
    def win_battle(self):
        # You can only win once per battle, so don't call w/o being ready to process a win otherwise you'll lose capturing it for the battle cycle
        if (self.in_battle == False or self.battle_done == True or self.get_battle_type() == 0 or
            self.get_battles_pokemon_left() != 0 or self.env.ram_interface.read_memory(TURNS_IN_CURRENT_BATTLE) == 0):
            return False
        
        self.battle_done = True
        return True
    
    def get_battle_type(self):
        battle_type = self.env.ram_interface.read_memory(BATTLE_TYPE)
        if battle_type == 255:
            battle_type = BattleTypes.DIED  # Died in battle, reassigned to 4 to save bits as 4-255 unused
        return battle_type
    
    def is_in_pre_battle(self):
        return self.env.ram_interface.read_memory(CURRENT_OPPONENT)
    
    def get_special_battle_type(self):
        return self.env.ram_interface.read_memory(SPECIAL_BATTLE_TYPE)
    
    def get_player_head_index(self):
        return self.env.ram_interface.read_memory(PLAYER_LOADED_POKEMON)
    
    def get_player_head_pokemon(self):
        offset = self._loaded_pokemon_address()
        return Pokemon(self.env).get_pokemon(offset)

    def get_player_party_head_hp(self):
        offset = self._loaded_pokemon_address()
        return Pokemon(self.env).get_pokemon_health(offset)
    
    def get_player_party_head_status(self):
        offset = self._loaded_pokemon_address()
        return Pokemon(self.env).get_pokemon_status(offset)
    
    def get_player_party_head_pp(self):
        offset = self._loaded_pokemon_address()
        return Pokemon(self.env).get_pokemon_pp_avail(offset)
    
    def get_player_party_head_modifiers(self):
        if not self.get_battle_type():
            return 0, 0, 0, 0, 0, 0

        attack_mod = self.env.ram_interface.read_memory(PLAYERS_POKEMON_ATTACK_MODIFIER)
        defense_mod = self.env.ram_interface.read_memory(PLAYERS_POKEMON_DEFENSE_MODIFIER)
        speed_mod =  self.env.ram_interface.read_memory(PLAYERS_POKEMON_SPEED_MODIFIER)
        accuracy_mod =  self.env.ram_interface.read_memory(PLAYERS_POKEMON_ACCURACY_MODIFIER)
        special_mod =  self.env.ram_interface.read_memory(PLAYERS_POKEMON_SPECIAL_MODIFIER)
        evasion_mod = self.env.ram_interface.read_memory(PLAYERS_POKEMON_SPECIAL_MODIFIER)

        return attack_mod, defense_mod, speed_mod, accuracy_mod, special_mod, evasion_mod
    
    def get_player_head_modifiers_dict(self):
        attack_mod, defense_mod, speed_mod, accuracy_mod, special_mod, evasion_mod = self.get_player_party_head_modifiers()

        return {
            'attack_mod': attack_mod,
            'defense_mod': defense_mod,
            'speed_mod': speed_mod,
            'accuracy_mod': accuracy_mod,
            'special_mod': special_mod,
            'evasion_mod': evasion_mod
        }
    
    def get_enemy_party_count(self):
        return self.env.ram_interface.read_memory(ENEMY_PARTY_COUNT)
    
    def get_enemy_party_head_pokemon(self):
        return self.env.ram_interface.read_memory(ENEMYS_POKEMON)
    
    def get_enemy_party_head_types(self):
        return self.env.ram_interface.read_memory(ENEMYS_POKEMON_TYPES[0]), self.env.ram_interface.read_memory(ENEMYS_POKEMON_TYPES[1])
    
    def get_enemy_party_head_hp(self):
        hp_total = (self.env.ram_interface.read_memory(ENEMYS_POKEMON_MAX_HP[0]) << 8) + self.env.ram_interface.read_memory(ENEMYS_POKEMON_MAX_HP[1])
        hp_avail = (self.env.ram_interface.read_memory(ENEMYS_POKEMON_HP[0]) << 8) + self.env.ram_interface.read_memory(ENEMYS_POKEMON_HP[1])

        return hp_total, hp_avail
    
    def get_enemy_party_head_level(self):
        return self.env.ram_interface.read_memory(ENEMYS_POKEMON_LEVEL)
    
    def get_enemy_party_head_status(self):
        return self.env.ram_interface.read_memory(ENEMYS_POKEMON_STATUS)
    
    def get_enemy_party_head_modifiers(self):
        attack_mod = self.env.ram_interface.read_memory(ENEMYS_POKEMON_ATTACK_MODIFIER)
        defense_mod = self.env.ram_interface.read_memory(ENEMYS_POKEMON_DEFENSE_MODIFIER)
        speed_mod =  self.env.ram_interface.read_memory(ENEMYS_POKEMON_SPEED_MODIFIER)
        accuracy_mod = self.env.ram_interface.read_memory(ENEMYS_POKEMON_ACCURACY_MODIFIER)
        special_mod =  self.env.ram_interface.read_memory(ENEMYS_POKEMON_SPECIAL_MODIFIER)
        evasion_mod = self.env.ram_interface.read_memory(ENEMYS_POKEMON_SPECIAL_MODIFIER)

        return attack_mod, defense_mod, speed_mod, accuracy_mod, special_mod, evasion_mod

    def get_enemy_fighting_pokemon_dict(self):
        hp_total, hp_avail = self.get_enemy_party_head_hp()
        attack_mod, defense_mod, speed_mod, accuracy_mod, special_mod, evasion_mod = self.get_enemy_party_head_modifiers()
        type_1, type_2 = self.get_enemy_party_head_types()

        return {
            'party_count': self.get_enemy_party_count(),
            'pokemon': self.get_enemy_party_head_pokemon(),
            'level': self.get_enemy_party_head_level(),
            'hp_total': hp_total,
            'hp_avail': hp_avail,
            'type_1': type_1,
            'type_2': type_2,
            'status': self.get_enemy_party_head_status(),
            'attack_mod':  attack_mod,
            'defense_mod': defense_mod,
            'speed_mod': speed_mod,
            'accuracy_mod': accuracy_mod,
            'special_mod': special_mod,
            'evasion_mod': evasion_mod
        }

    def get_battle_turn_moves(self):
        player_selected_move = self.env.ram_interface.read_memory(PLAYER_SELECTED_MOVE)
        enemy_selected_move = self.env.ram_interface.read_memory(ENEMY_SELECTED_MOVE)

        return player_selected_move, enemy_selected_move                                                         
    
    def get_battles_pokemon_left(self):
        alive_pokemon = 0

        if not self.in_battle:
            return 0 

        # Wild mons only have 1 pokemon alive and their status is in diff reg's
        if self.get_battle_type() == BattleTypes.WILD_BATTLE:
            return int(self.env.ram_interface.read_memory(ENEMYS_POKEMON_HP[0]) != 0 or self.env.ram_interface.read_memory(ENEMYS_POKEMON_HP[1]) != 0)
        
        for i in range(POKEMON_MAX_COUNT):
            if (self.env.ram_interface.read_memory(ENEMY_TRAINER_POKEMON_HP[0] + ENEMY_TRAINER_POKEMON_HP_OFFSET * i) != 0 or
                self.env.ram_interface.read_memory(ENEMY_TRAINER_POKEMON_HP[1] + ENEMY_TRAINER_POKEMON_HP_OFFSET * i) != 0):
                alive_pokemon += 1 

        return alive_pokemon
    
    def get_battle_type_hint(self): 
        if not self.get_battle_type():
            return 0

        pokemon = self.env.ram_interface.read_memory(PLAYER_LOADED_POKEMON)
        player_type_1 = self.env.ram_interface.read_memory(POKEMON_1_TYPES[0] + pokemon * PARTY_OFFSET)
        player_type_2 = self.env.ram_interface.read_memory(POKEMON_1_TYPES[1] + pokemon * PARTY_OFFSET)
        enemy_type_1 = self.env.ram_interface.read_memory(ENEMYS_POKEMON_TYPES[0])
        enemy_type_2 = self.env.ram_interface.read_memory(ENEMYS_POKEMON_TYPES[1])

        return (max(POKEMON_MATCH_TYPES.get((player_type_1, enemy_type_1), 1), POKEMON_MATCH_TYPES.get((player_type_1, enemy_type_2), 1)) *
                max(POKEMON_MATCH_TYPES.get((player_type_2, enemy_type_1), 1), POKEMON_MATCH_TYPES.get((player_type_2, enemy_type_2), 1)))
    
    def get_enemy_lineup_levels(self):
        # Wild Pokemon, only ever one
        if self.get_battle_type() == BattleTypes.WILD_BATTLE:
            return [self.env.ram_interface.read_memory(ENEMYS_POKEMON_LEVEL)]

        lineup_levels = []
        for party_index in range(POKEMON_PARTY_SIZE):
            offset = party_index * ENEMYS_POKEMON_OFFSET
            level = self.env.ram_interface.read_memory(ENEMYS_POKEMON_INDEX_LEVEL + offset)
            if level:
                lineup_levels.append(level)
            else:
                break

        return lineup_levels

    def calculate_effective_power(self, move_type_value: int, target_type: PokemonType) -> float:
        """Calculate the effective power of a move type against a target type"""
        move_type_enum = PokemonType(move_type_value)
        base_power = self.memory[PLAYERS_MOVE_POWER]
        multiplier = self.get_type_effectiveness(move_type_enum, target_type)
        return base_power * multiplier
