from enum import IntEnum, Enum


class Command(IntEnum):
    CMD_INIT = 0x10
    ACK_INIT_OK = 0x11
    CMD_SET_STREAM = 0x30
    ACK_TRANSITION = 0x31
    ACK_REJECTED = 0xFF
    CMD_HEARTBEAT = 0x40
    CMD_RESYNC = 0x50
    CMD_UTILS = 0x02


class MasterState(str, Enum):
    INIT_CLOUD = "INIT_CLOUD"
    INIT_HARDWARE = "INIT_HARDWARE"
    RE_EVALUATE = "RE_EVALUATE"
    STREAM_TRANSITION = "STREAM_TRANSITION"
    ACTIVE_FLOW = "ACTIVE_FLOW"
    PREEMPTION = "PREEMPTION"
    FAULT = "FAULT"

class UtilsCmd(IntEnum):
    UTILS_HELP           = 0x01
    UTILS_SIMULATE_ON    = 0x02
    UTILS_SIMULATE_OFF   = 0x03

class TrafficState(IntEnum):
    STATE_SYSTEM_INIT        = 0x00
    STATE_EVALUATE_QUEUE     = 0x01
    STATE_LANE_1_GREEN       = 0x10
    STATE_LANE_2_GREEN       = 0x11
    STATE_LANE_3_GREEN       = 0x12
    STATE_LANE_4_GREEN       = 0x13
    STATE_LANE_5_GREEN       = 0x14
    STATE_LANE_6_GREEN       = 0x15
    STATE_LANE_7_GREEN       = 0x16
    STATE_LANE_8_GREEN       = 0x17
    STATE_APPROACH_1_2_GREEN = 0x18
    STATE_APPROACH_3_4_GREEN = 0x19
    STATE_APPROACH_5_6_GREEN = 0x1A
    STATE_APPROACH_7_8_GREEN = 0x1B
    STATE_OPPOSING_1_5_GREEN = 0x1C
    STATE_OPPOSING_3_7_GREEN = 0x1D
    STATE_YELLOW_TRANSITION  = 0x20
    STATE_ALL_RED_CLEARANCE  = 0x21
    STATE_FLASHING_YELLOW    = 0x22
    STATE_EMERGENCY          = 0x23

HEADER_BYTES = bytes([0xAB, 0xCD, 0xFF])

# Model lajur searah jarum jam: N=(1,2), E=(3,4), S=(5,6), W=(7,8)
LANE_DIRECTIONS = {
    1: "N", 2: "N",
    3: "E", 4: "E",
    5: "S", 6: "S",
    7: "W", 8: "W",
}

# lajur ganjil pembuka -> pasangan pendekatannya
APPROACH_OF = {
    1: (1, 2),
    3: (3, 4),
    5: (5, 6),
    7: (7, 8),
}

# lajur pembuka pendekatan -> lajur pembuka pendekatan lawannya
OPPOSING_APPROACH = {
    1: 5,
    3: 7,
    5: 1,
    7: 3,
}

LANE_STATES = {
    1: TrafficState.STATE_LANE_1_GREEN,
    2: TrafficState.STATE_LANE_2_GREEN,
    3: TrafficState.STATE_LANE_3_GREEN,
    4: TrafficState.STATE_LANE_4_GREEN,
    5: TrafficState.STATE_LANE_5_GREEN,
    6: TrafficState.STATE_LANE_6_GREEN,
    7: TrafficState.STATE_LANE_7_GREEN,
    8: TrafficState.STATE_LANE_8_GREEN,
}

APPROACH_STATES = {
    (1, 2): TrafficState.STATE_APPROACH_1_2_GREEN,
    (3, 4): TrafficState.STATE_APPROACH_3_4_GREEN,
    (5, 6): TrafficState.STATE_APPROACH_5_6_GREEN,
    (7, 8): TrafficState.STATE_APPROACH_7_8_GREEN,
}

OPPOSING_STATES = {
    (1, 5): TrafficState.STATE_OPPOSING_1_5_GREEN,
    (3, 7): TrafficState.STATE_OPPOSING_3_7_GREEN,
}

# ID lajur hijau per state (satu-satunya acuan untuk CLI & derivasi fase)
STATE_LANES: dict = {
    **{state: (lane,) for lane, state in LANE_STATES.items()},
    **{state: pair for pair, state in APPROACH_STATES.items()},
    **{state: pair for pair, state in OPPOSING_STATES.items()},
}

# arah per state, diturunkan dari LANE_DIRECTIONS (dipakai kalkulasi PCU)
STATE_DIRECTIONS: dict = {
    state: tuple(dict.fromkeys(LANE_DIRECTIONS[lane] for lane in lanes))
    for state, lanes in STATE_LANES.items()
}

# kebalikan STATE_LANES: tuple lajur -> state (untuk CLI `set 1,5`)
LANES_STATE: dict = {lanes: state for state, lanes in STATE_LANES.items()}

# 14 state hijau (daftar CLI / validasi)
GREEN_STATES = list(STATE_LANES.keys())

DIRECTIONS = ("N", "S", "E", "W")

def build_frame(cmd: int, payload: bytes = b'') -> bytes:
    # LEN menghitung CMD + payload (slave menolak LEN == 0)
    length = 1 + len(payload)
    return HEADER_BYTES + bytes([length, cmd]) + payload

def parse_stream(buffer: bytearray):
    frames = []
    n = len(buffer)
    i = 0
    while i <= n - 5:
        if buffer[i:i+3] == HEADER_BYTES:
            length = buffer[i+3]
            if length == 0:
                # LEN == 0 adalah sampah pada kabel; resync 1 byte
                i += 1
                continue
            if n >= i + 4 + length:
                cmd = buffer[i+4]
                payload = bytes(buffer[i+5:i+4+length])
                frames.append((cmd, payload))
                i += 4 + length
                continue
        i += 1
    return frames
