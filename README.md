# Smart Traffic Light — Stream-Based Staggered Controller

Raspberry Pi (master) + ESP32-S3 (slave) traffic light controller using a stream-based staggered architecture. The master owns all green timers in RAM and sends state-change commands to the slave over a custom binary UART protocol.

## Architecture

```
┌────────────────────────────┐   Serial (115200)   ┌──────────────────────────┐
│  Raspberry Pi (master)     │   AB CD FF LEN CMD   │  ESP32-S3 (slave)        │
│  - yolo_worker            │ ── CMD_INIT ───────► │  - stream executor       │
│  - master_controller      │ ── CMD_SET_STREAM ─► │  - conflict enforcement  │
│  - RE_EVALUATE logic      │ ── CMD_HEARTBEAT ───► │  - watchdog              │
│  - CLI (daemon thread)    │ ◄── ACK_... ──────── │                          │
└────────────────────────────┘                      └──────────────────────────┘
```

## Stream Model

- **8 streams** (Stream 0-7), renumbered from cloud config order
- Streams are non-conflicting groups that can run in parallel when allowed by the conflict matrix
- **Staggered start**: all selected non-conflicting streams start at T=0 simultaneously; stagger appears in end times only
- Conflict matrix is 8x8 bytes where bit N of byte K means Stream K conflicts with Stream N

## Serial Protocol

Frame: `0xAB 0xCD 0xFF <LEN> <CMD> <PAYLOAD...>`

**LEN counts the CMD byte plus the payload** (`LEN = 1 + len(payload)`). The slave rejects `LEN == 0`.

### Pi → ESP32

| Hex | Command        | Payload | Description |
|-----|----------------|---------|-------------|
| 0x10| CMD_INIT       | `[Y_SEC, R_SEC, MATRIX(8), FB phases...]` | Initialize with yellow/red clearance and conflict matrix |
| 0x30| CMD_SET_STREAM | `[Stream ID, Target State]` | Set stream state (Green/Red) |
| 0x40| CMD_HEARTBEAT  | — | Keep-alive (resets slave watchdog) |
| 0x50| CMD_RESYNC     | — | Recovery/resync |

**CMD_INIT payload:**
- Byte 0: Y_SEC (Yellow clearance duration in seconds)
- Byte 1: R_SEC (All-Red clearance duration in seconds)
- Byte 2-9: MATRIX (8 bytes, Byte 2 = Stream 0 conflicts ... Byte 9 = Stream 7 conflicts)
- Byte 10+: FB phases — repeating `[MASK, SEC]` pairs for fallback sequence

**Fallback sequence format (config):**
```json
"fallback_sequence": [
  {"active_streams": [0, 1], "duration_s": 30},
  {"active_streams": [2, 3], "duration_s": 20}
]
```

### ESP32 → Pi

| Hex | Command       | Description |
|-----|---------------|-------------|
| 0x11| ACK_INIT_OK   | Init acknowledged |
| 0x31| ACK_TRANSITION| Transition completed |
| 0xFF| ACK_REJECTED  | Transition rejected |

## Master States

```
INIT_CLOUD → INIT_HARDWARE → RE_EVALUATE → STREAM_TRANSITION → ACTIVE_FLOW
                                                              ↖FAULT ↗
                                                              PREEMPTION
```

## RE_EVALUATE Algorithm

1. Get raw vehicle counts from yolo_worker per stream (`{"S0": {"car": 5, "motorcycle": 12, "truck": 1}}`)
2. Calculate PCU per stream: `(cars * 1.0) + (motorcycles * 0.3) + (trucks * 2.5)`
3. Calculate green time: `(PCU / lane_count) * saturation_headway_sec`
4. Clamp to `[min_green, min_green + max_extra_green]`
5. Rank streams by priority score: `(queue / lane_count * 1.0) + (max_wait * 0.5)`
6. Select non-conflicting active set (greedy by priority)
7. All selected streams start at T=0 simultaneously

## ACTIVE_FLOW Logic

Every 1 second:
1. Send CMD_HEARTBEAT
2. For each active stream:
   - **Expiry check**: if now >= stream.expires_at → send CMD_SET_STREAM(Red), trigger RE_EVALUATE
   - **Gap-out check**: if now > stream.min_green AND stream.queue == 0 → send CMD_SET_STREAM(Red), trigger RE_EVALUATE
3. **Periodic check**: if now - last_re_evaluate > re_evaluate_interval_sec → trigger RE_EVALUATE
4. **Emergency check**: if ambulance_detected → kill conflicting streams, trigger RE_EVALUATE

## Configuration (`data/config.json`)

```json
{
    "min_green": 10,
    "max_extra_green": 40,
    "yellow_duration": 3,
    "y_sec": 3,
    "r_sec": 2,
    "conflict_zone_meter": 150,
    "vehicle_speed_ms": 10,
    "saturation_headway_sec": 2.1,
    "re_evaluate_interval_sec": 10,
    "lanes": [
        [1, 1, 2],
        [2, 3, 4],
        [1, 5, 6],
        [1, 7, 8]
    ],
    "streams": [
        {"lane_count": 2, "queue": 0},
        {"lane_count": 2, "queue": 0},
        {"lane_count": 2, "queue": 0},
        {"lane_count": 2, "queue": 0}
    ],
    "conflict_matrix": [
        [0,0,0,0,1,1,1,1],
        [0,0,0,0,1,1,1,1],
        [0,0,0,0,1,1,1,1],
        [0,0,0,0,1,1,1,1],
        [1,1,1,1,0,0,0,0],
        [1,1,1,1,0,0,0,0],
        [1,1,1,1,0,0,0,0],
        [1,1,1,1,0,0,0,0]
    ],
    "fallback_sequence": [
        {"active_streams": [0,1,2,3], "duration_s": 30}
    ],
    "serial_port": "/dev/ttyUSB0",
    "baud_rate": 115200,
    "yolo_poll_interval_sec": 2
}
```

Key fields:
- `streams` - Stream definitions from cloud config (renumbered to 0-7 by master)
- `conflict_matrix` - 8x8 conflict matrix
- `y_sec`/`r_sec` or `yellow_duration`/`red_sec` - Clearance times
- `saturation_headway_sec` - Used for green time calculation
- `re_evaluate_interval_sec` - Periodic re-evaluation interval
- `fallback_sequence` - Dynamic fallback phases
- `lanes` - Local lane config used by yolo_worker to map counts

## Running

```bash
pip install pyserial
python src/main.py
```

## Project Structure

```
src/
├── main.py              # entrypoint
├── cli.py               # interactive CLI
├── master_controller.py # stream-based master controller
├── serial_handler.py    # serial wrapper + frame parser
├── yolo_worker.py       # per-stream vehicle count provider
├── protocol.py          # command/state enums, frame build/parse
└── config.py            # JSON config manager
data/
├── config.json          # live config
└── config_example.json  # config template
ref/
└── architecture-blueprint.md
```
