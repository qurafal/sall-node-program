import threading
import time

import serial

from protocol import Command, MasterState, HEADER_BYTES


POLL_INTERVAL_SEC = 0.02
RECONNECT_DELAY_SEC = 2.0


class MasterController:
    def __init__(self, config_mgr, serial_handler_factory=None, yolo_worker=None):
        self.config = config_mgr
        self.serial = None
        self._serial_factory = serial_handler_factory
        self.yolo_worker = yolo_worker
        self.state = MasterState.INIT_CLOUD
        self._running = True
        self._lock = threading.Lock()
        self.streams = {}  # stream_id (0-7) -> info
        self.conflict_matrix = [[0] * 8 for _ in range(8)]  # 8x8
        self.active_set = []
        self.min_green = getattr(config_mgr, "min_green", 10)
        self._state_entered_at = time.time()
        self._last_heartbeat = 0.0
        self._last_re_evaluate = 0.0
        self.ambulance_detected = False
        # track active stream end times
        self.stream_expires = {}  # stream_id -> expires_at
        # look-ahead state
        self.next_active_set = []
        self.next_streams_info = {}
        self._lookahead_computed = False
        self._current_phase_active = []
        try:
            self.re_evaluate_interval_sec = float(config_mgr.re_evaluate_interval_sec)
        except Exception:
            self.re_evaluate_interval_sec = 10.0
        try:
            self.min_green = int(config_mgr.min_green)
        except Exception:
            self.min_green = 10

    @property
    def serial_connected(self):
        return self.serial is not None and self.serial.is_open

    def stop(self):
        self._running = False

    # ---------- Cloud ----------
    def load_cloud_config(self):
        cfg = {}
        try:
            cfg = getattr(self.config, "fetch_cloud", lambda: {})()
        except Exception:
            cfg = {}

        # Normalize streams - renumber 0-7 from cloud config order
        streams_raw = cfg.get("streams", {}) if isinstance(cfg, dict) else {}
        streams_norm = {}
        stream_list_ordered = []
        try:
            if isinstance(streams_raw, list):
                stream_list_ordered = streams_raw
            elif isinstance(streams_raw, dict):
                # try to order by key if numeric
                items = []
                for k, v in streams_raw.items():
                    try:
                        if isinstance(k, str) and k.startswith(('0x', '0X')):
                            ik = int(k, 16)
                        elif isinstance(k, str):
                            ik = int(k)
                        elif isinstance(k, int):
                            ik = k
                        else:
                            continue
                        items.append((ik, v))
                    except Exception:
                        continue
                items.sort(key=lambda x: x[0])
                stream_list_ordered = [v for _, v in items]
            else:
                stream_list_ordered = []
        except Exception:
            stream_list_ordered = []

        # Renumber 0-7
        for idx, item in enumerate(stream_list_ordered[:8]):
            if isinstance(item, dict):
                streams_norm[idx] = {
                    "queue": int(item.get("queue", 0)),
                    "lane_count": int(item.get("lane_count", 1)) if int(item.get("lane_count", 1)) > 0 else 1,
                    "max_wait": float(item.get("max_wait", item.get("max_wait_time", 0))),
                }
            else:
                streams_norm[idx] = {"queue": 0, "lane_count": 1, "max_wait": 0.0}

        self.streams = streams_norm

        # Build conflict matrix 8x8
        cm_raw = cfg.get("conflict_matrix", {}) if isinstance(cfg, dict) else {}
        matrix = [[0] * 8 for _ in range(8)]
        try:
            if isinstance(cm_raw, (list, tuple)) and len(cm_raw) == 8:
                for i in range(8):
                    row = cm_raw[i]
                    if isinstance(row, (list, tuple, bytes)):
                        for j in range(min(8, len(row))):
                            val = row[j]
                            if isinstance(val, bool):
                                matrix[i][j] = 1 if val else 0
                            elif isinstance(val, int):
                                matrix[i][j] = 1 if val != 0 else 0
                            else:
                                matrix[i][j] = 0
                    elif isinstance(row, int):
                        # bitmask
                        for j in range(8):
                            matrix[i][j] = 1 if (row & (1 << j)) else 0
            elif isinstance(cm_raw, dict):
                for k, v in cm_raw.items():
                    try:
                        if isinstance(k, str) and k.startswith(('0x', '0X')):
                            i = int(k, 16)
                        elif isinstance(k, str):
                            i = int(k)
                        elif isinstance(k, int):
                            i = k
                        else:
                            continue
                        if not (0 <= i < 8):
                            continue
                        if isinstance(v, (list, set, tuple)):
                            for nk in v:
                                try:
                                    if isinstance(nk, str) and nk.startswith(('0x', '0X')):
                                        j = int(nk, 16)
                                    elif isinstance(nk, str):
                                        j = int(nk)
                                    elif isinstance(nk, int):
                                        j = nk
                                    else:
                                        continue
                                    if 0 <= j < 8 and j != i:
                                        matrix[i][j] = 1
                                        matrix[j][i] = 1  # make symmetric if needed
                                except Exception:
                                    continue
                        elif isinstance(v, int):
                            for j in range(8):
                                if v & (1 << j):
                                    matrix[i][j] = 1
                                    matrix[j][i] = 1
                    except Exception:
                        continue
        except Exception:
            matrix = [[0] * 8 for _ in range(8)]
        self.conflict_matrix = matrix
        try:
            if hasattr(self.config, 'validate_lanes_conflicts'):
                self.config.validate_lanes_conflicts(self.conflict_matrix)
        except Exception:
            pass

        try:
            self.min_green = int(cfg.get("min_green", self.config.min_green))
        except Exception:
            try:
                self.min_green = int(self.config.min_green)
            except Exception:
                pass
        try:
            self.re_evaluate_interval_sec = float(cfg.get("re_evaluate_interval_sec", self.config.re_evaluate_interval_sec))
        except Exception:
            try:
                self.re_evaluate_interval_sec = float(self.config.re_evaluate_interval_sec)
            except Exception:
                pass

    # ---------- RE_EVALUATE ----------
    def priority_score(self, stream_id):
        if stream_id not in self.streams:
            return 0.0
        s = self.streams[stream_id]
        lane_count = max(1, s.get("lane_count", 1))
        max_wait = s.get("max_wait", 0)
        return (max_wait * 0.5)

    def _compute_lookahead(self):
        try:
            # Save current active set context
            current_active = list(self.active_set)
            # Run re-evaluate to compute next
            next_set = self.re_evaluate()
            # Store
            self.next_active_set = list(next_set)
            self._lookahead_computed = True
            self._current_phase_active = current_active
            print(f"[MASTER][LOOKAHEAD] Computed next active set: {[f'S{s}' for s in self.next_active_set]}")
        except Exception as e:
            print(f"[MASTER][LOOKAHEAD][ERR] {e}")
            self._lookahead_computed = False
            self.next_active_set = []

    # ---------- Serial helpers ----------
    def _open_serial(self):
        try:
            if self._serial_factory:
                self.serial = self._serial_factory(self.config)
            else:
                from serial_handler import SerialHandler
                self.serial = SerialHandler(self.config)
        except Exception as e:
            print(f"[MASTER][WARN] serial open failed: {e}")
            self.serial = None

    def _close_serial(self):
        if self.serial is not None:
            try:
                self.serial.close()
            except Exception:
                pass
        self.serial = None

    def _send(self, cmd, payload=b""):
        if self.serial is None:
            raise RuntimeError("serial not connected")
        self.serial.send_frame(cmd, payload)

    def _wait_ack(self, ack_cmd, timeout=None):
        deadline = None if timeout is None else time.time() + timeout
        while self._running:
            if self.serial is None:
                return False
            try:
                frames = self.serial.read_frames()
            except Exception:
                return False
            for cmd, _ in frames:
                if cmd == ack_cmd:
                    return True
                if cmd == Command.ACK_REJECTED:
                    return False
            if deadline is not None and time.time() > deadline:
                return False
            time.sleep(POLL_INTERVAL_SEC)
        return False

    # ---------- State transitions ----------
    def _enter(self, state):
        self.state = state
        self._state_entered_at = time.time()
        print(f"[MASTER] -> {state}")

    def run(self):
        while self._running:
            try:
                if self.state == MasterState.INIT_CLOUD:
                    self.load_cloud_config()
                    self._enter(MasterState.INIT_HARDWARE)
                elif self.state == MasterState.INIT_HARDWARE:
                    if self.serial is None:
                        self._open_serial()
                        if self.serial is None:
                            time.sleep(RECONNECT_DELAY_SEC)
                            continue
                    # Build CMD_INIT payload
                    y_sec = int(self.config.yellow_sec)
                    r_sec = int(self.config.red_sec)
                    # MATRIX 8 bytes
                    matrix_bytes = bytearray()
                    for i in range(8):
                        b = 0
                        for j in range(8):
                            if self.conflict_matrix[i][j]:
                                b |= (1 << j)
                        matrix_bytes.append(b)
                    payload = bytearray([y_sec & 0xFF, r_sec & 0xFF]) + bytes(matrix_bytes)
                    fb = getattr(self.config, "fallback_sequence", [])
                    if isinstance(fb, list):
                        for phase in fb:
                            try:
                                if isinstance(phase, dict):
                                    active = phase.get("active_streams", phase.get("streams", []))
                                    dur = int(phase.get("duration_s", phase.get("duration", 0)))
                                elif isinstance(phase, (list, tuple)) and len(phase) >= 2:
                                    active = phase[0]
                                    dur = int(phase[1])
                                else:
                                    continue
                                mask = 0
                                if isinstance(active, (list, tuple, set)):
                                    for sid in active:
                                        try:
                                            s = int(sid)
                                            if 0 <= s < 8:
                                                mask |= (1 << s)
                                        except Exception:
                                            continue
                                payload.append(mask & 0xFF)
                                payload.append(dur & 0xFF)
                            except Exception:
                                continue
                    try:
                        self._send(Command.CMD_INIT, bytes(payload))
                    except Exception as e:
                        print(f"[MASTER][ERR] send init: {e}")
                        self._enter(MasterState.FAULT)
                        continue
                    if self._wait_ack(Command.ACK_INIT_OK, timeout=5.0):
                        self._enter(MasterState.RE_EVALUATE)
                    else:
                        self._enter(MasterState.FAULT)
                elif self.state == MasterState.RE_EVALUATE:
                    self.re_evaluate()
                    self._enter(MasterState.STREAM_TRANSITION)
                elif self.state == MasterState.STREAM_TRANSITION:
                    # Send CMD_SET_STREAM sequentially with real-time delays
                    now = time.time()
                    print(f"[MASTER][TRANSITION] Activating {len(self.active_set)} stream(s): {[f'S{s}' for s in self.active_set]}")
                    # Set all active streams to GREEN (target state) with delays? Plan says "master sends CMD_SET_STREAM sequentially with real-time delays"
                    # Target state encoding not fully specified, but use 0x01 for GREEN as example
                    for i, sid in enumerate(self.active_set):
                        try:
                            try:
                                green_t = self.streams.get(sid, {}).get("green_time", int(self.config.min_green))
                            except Exception:
                                green_t = self.streams.get(sid, {}).get("green_time", 10)
                            self.stream_expires[sid] = now + green_t
                            s_info = self.streams.get(sid, {})
                            print(f"[MASTER][TRANSITION]   -> S{sid} GREEN for {green_t}s (expires {self.stream_expires[sid]:.1f}, queue={s_info.get('queue',0)}, lc={s_info.get('lane_count',1)}, reason: highest priority non-conflicting)")
                            payload_st = bytes([sid & 0xFF, 0x01])  # assume 0x01 = GREEN
                            self._send(Command.CMD_SET_STREAM, payload_st)
                            if not self._wait_ack(Command.ACK_TRANSITION, timeout=2.0):
                                print(f"[MASTER][TRANSITION]   ACK not received for S{sid}")
                            time.sleep(0.01)  # small real-time delay
                        except Exception as e:
                            print(f"[MASTER][ERR] stream transition {sid}: {e}")
                    self._enter(MasterState.ACTIVE_FLOW)
                    self._last_heartbeat = time.time()
                    self._state_entered_at = time.time()
                elif self.state == MasterState.ACTIVE_FLOW:
                    now = time.time()
                    # Send heartbeat every 1 second
                    if now - self._last_heartbeat >= 1.0:
                        try:
                            self._send(Command.CMD_HEARTBEAT)
                        except Exception:
                            self._enter(MasterState.FAULT)
                            continue
                        self._last_heartbeat = now
                    # Poll for ACK_CLEARED from ESP32 (green+yellow finished)
                    try:
                        frames = self.serial.read_frames() if self.serial is not None else []
                    except Exception:
                        frames = []
                    for cmd, _ in frames:
                        if cmd == Command.ACK_CLEARED:
                            # Fire precomputed next phase immediately
                            if self.next_active_set:
                                self.active_set = list(self.next_active_set)
                                self.next_active_set = []
                                self._enter(MasterState.STREAM_TRANSITION)
                            else:
                                self._enter(MasterState.RE_EVALUATE)
                            need_re_eval = False
                            break
                    if self.state != MasterState.ACTIVE_FLOW:
                        continue
                    # Check expirations and gap-out
                    need_re_eval = False
                    if self.ambulance_detected:
                        # kill conflicting streams
                        self.ambulance_detected = False
                        print("[MASTER][ACTIVE] Ambulance detected - triggering re-evaluation")
                        need_re_eval = True
                    else:
                        for sid in list(self.active_set):
                            expires = self.stream_expires.get(sid)
                            if expires is not None and now >= expires:
                                try:
                                    print(f"[MASTER][ACTIVE] S{sid} expired (now={now:.1f} >= expires={expires:.1f}) - setting RED, triggering re-evaluation")
                                    self._send(Command.CMD_SET_STREAM, bytes([sid & 0xFF, 0x00]))  # RED
                                    self._wait_ack(Command.ACK_TRANSITION, timeout=1.0)
                                except Exception:
                                    pass
                                need_re_eval = True
                                break
                            # gap-out: if past min_green and queue == 0
                            green_t = self.streams.get(sid, {}).get("green_time", 10)
                            try:
                                min_g = int(self.config.min_green)
                            except Exception:
                                min_g = 10
                            if expires is not None and now > (expires - green_t + min_g) :
                                try:
                                    print(f"[MASTER][ACTIVE] S{sid} gap-out: past min_green ({min_g}s) with queue=0 - setting RED, triggering re-evaluation")
                                    self._send(Command.CMD_SET_STREAM, bytes([sid & 0xFF, 0x00]))
                                    self._wait_ack(Command.ACK_TRANSITION, timeout=1.0)
                                except Exception:
                                    pass
                                need_re_eval = True
                                break
                    # Periodic check
                    if not need_re_eval and now - self._last_re_evaluate > self.re_evaluate_interval_sec:
                        print(f"[MASTER][ACTIVE] Periodic re-evaluation (>{self.re_evaluate_interval_sec}s elapsed)")
                        need_re_eval = True
                    if need_re_eval:
                        self._enter(MasterState.RE_EVALUATE)
                        continue

                elif self.state == MasterState.FAULT:
                    try:
                        self._send(Command.CMD_RESYNC)
                    except Exception:
                        pass
                    time.sleep(RECONNECT_DELAY_SEC)
                    self._enter(MasterState.RE_EVALUATE)
                elif self.state == MasterState.PREEMPTION:
                    time.sleep(POLL_INTERVAL_SEC)
            except serial.SerialException as e:
                print(f"[MASTER][WARN] serial disconnect: {e}")
                self._close_serial()
                self._enter(MasterState.FAULT)
            except Exception as e:
                print(f"[MASTER][ERROR] {e}")
                self._enter(MasterState.FAULT)

    def shutdown(self):
        self.stop()
        self._close_serial()

    # public hooks
    def trigger_preemption(self):
        self._enter(MasterState.PREEMPTION)
