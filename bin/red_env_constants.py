# constants.py

# Constants for RedGymEnv
BYTE_SIZE = 256
BITS_PER_BYTE = 8
VEC_DIM = 4320
MAX_STEP_MEMORY = 2000
FRAME_STACKS = 3
OUTPUT_SHAPE = (36, 40, 3)
MEM_PADDING = 2
MEMORY_HEIGHT = 8
OUTPUT_FULL = (
    OUTPUT_SHAPE[0] * FRAME_STACKS + 2 * (MEM_PADDING + MEMORY_HEIGHT),
    OUTPUT_SHAPE[1],
    OUTPUT_SHAPE[2]
)
POS_HISTORY_SIZE = 14
POS_BYTES = 9
XYM_BYTES = 3
POS_MAP_DETAIL_BYTES = 6
SCREEN_VIEW_SIZE = 7
NEXT_STEP_VISITED = 13  # Num of pos's that are within two moves from cur pos + cur pos
PYBOY_RUN_SPEED = 6
MAP_VALUE_PALLET_TOWN = 12
OBSERVATION_MEMORY_SIZE = 12

GLOBAL_SEED = 2
