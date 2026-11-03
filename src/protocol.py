from enum import IntEnum, Enum


class Command(IntEnum):
    CMD_INIT = 0x10
    ACK_INIT_OK = 0x11
    CMD_SET_STREAM = 0x30
    ACK_TRANSITION = 0x31
    ACK_CLEARED = 0x32
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


HEADER_BYTES = bytes([0xAB, 0xCD, 0xFF])

DIRECTIONS = ("N", "S", "E", "W")

def build_frame(cmd: int, payload: bytes = b'') -> bytes:
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