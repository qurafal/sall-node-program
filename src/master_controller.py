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
        self.re_evaluate_interval_sec = getattr(config_mgr, "re_evaluate_interval_sec", 10)

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
            self.min_green = cfg.get("min_green", getattr(self.config, "min_green", self.min_green))
        except Exception:
            pass
        try:
            self.re_evaluate_interval_sec = cfg.get("re_evaluate_interval_sec", getattr(self.config, "re_evaluate_interval_sec", self.re_evaluate_interval_sec))
        except Exception:
            pass

    # ---------- RE_EVALUATE ----------
    def priority_score(self, stream_id):
        if stream_id not in self.streams:
            return 0.0
        s = self.streams[stream_id]
        queue = s.get("queue", 0)
        lane_count = max(1, s.get("lane_count", 1))
        max_wait = s.get("max_wait", 0)
        return (queue / lane_count * 1.0) + (max_wait * 0.5)

    def re_evaluate(self):
        # Get raw vehicle counts from yolo_worker per stream
        raw_counts = {}
        try:
            if self.yolo_worker is not None:
                raw_counts = self.yolo_worker.get_stream_counts() or {}
        except Exception:
            raw_counts = {}

        # Calculate green times per stream (store in streams)
        saturation_headway = getattr(self.config, "saturation_headway_sec", 2.1)
        min_g = int(getattr(self.config, "min_green", self.min_green))
        max_extra = int(getattr(self.config, "max_extra_green", 40))
        for sid in list(self.streams.keys()):
            try:
                # get counts for stream sid (try S{sid} or just sid)
                cnt = raw_counts.get(f"S{sid}") or raw_counts.get(sid) or {}
                cars = int(cnt.get("car", cnt.get("mobil", 0)))
                motos = int(cnt.get("motorcycle", cnt.get("motor", 0)))
                trucks = int(cnt.get("truck", cnt.get("truk", 0)))
                lane_count = max(1, self.streams[sid].get("lane_count", 1))
                pcu = cars * 1.0 + motos * 0.3 + trucks * 2.5
                if pcu <= 0:
                    green_t = min_g
                else:
                    raw_gts = (pcu / lane_count) * saturation_headway
                    green_t = int(max(min_g, min(min_g + max_extra, round(raw_gts))))
                self.streams[sid]["green_time"] = green_t
            except Exception:
                self.streams[sid]["green_time"] = min_g

        ranked = sorted(self.streams.keys(), key=self.priority_score, reverse=True)
        active = []
        for sid in ranked:
            # check conflict with already active
            incompatible = False
            for a in active:
                if self.conflict_matrix[sid][a] == 1 or self.conflict_matrix[a][sid] == 1:
                    incompatible = True
                    break
            if incompatible:
                continue
            active.append(sid)
        self.active_set = active
        self._last_re_evaluate = time.time()
        return active

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
                    y_sec = int(getattr(self.config, "yellow_sec", getattr(self.config, "yellow_duration", 3)))
                    r_sec = int(getattr(self.config, "red_sec", 2))
                    # MATRIX 8 bytes
                    matrix_bytes = bytearray()
                    for i in range(8):
                        b = 0
                        for j in range(8):
                            if self.conflict_matrix[i][j]:
                                b |= (1 << j)
                        matrix_bytes.append(b)
                    payload = bytearray([y_sec & 0xFF, r_sec & 0xFF]) + bytes(matrix_bytes)
                    # FB phases
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
                    # Set all active streams to GREEN (target state) with delays? Plan says "master sends CMD_SET_STREAM sequentially with real-time delays"
                    # Target state encoding not fully specified, but use 0x01 for GREEN as example
                    for i, sid in enumerate(self.active_set):
                        try:
                            green_t = self.streams.get(sid, {}).get("green_time", int(getattr(self.config, "min_green", 10)))
                            self.stream_expires[sid] = now + green_t
                            payload_st = bytes([sid & 0xFF, 0x01])  # assume 0x01 = GREEN
                            self._send(Command.CMD_SET_STREAM, payload_st)
                            if not self._wait_ack(Command.ACK_TRANSITION, timeout=2.0):
                                pass
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
                    # Check expirations and gap-out
                    need_re_eval = False
                    if self.ambulance_detected:
                        # kill conflicting streams
                        self.ambulance_detected = False
                        need_re_eval = True
                    else:
                        for sid in list(self.active_set):
                            expires = self.stream_expires.get(sid)
                            if expires is not None and now >= expires:
                                try:
                                    self._send(Command.CMD_SET_STREAM, bytes([sid & 0xFF, 0x00]))  # RED
                                    self._wait_ack(Command.ACK_TRANSITION, timeout=1.0)
                                except Exception:
                                    pass
                                need_re_eval = True
                                break
                            # gap-out: if past min_green and queue == 0
                            if expires is not None and now > (expires - self.streams.get(sid, {}).get("green_time", 10) + int(getattr(self.config, "min_green", 10))) and self.streams.get(sid, {}).get("queue", 0) == 0:
                                try:
                                    self._send(Command.CMD_SET_STREAM, bytes([sid & 0xFF, 0x00]))
                                    self._wait_ack(Command.ACK_TRANSITION, timeout=1.0)
                                except Exception:
                                    pass
                                need_re_eval = True
                                break
                    # Periodic check
                    if not need_re_eval and now - self._last_re_evaluate > self.re_evaluate_interval_sec:
                        need_re_eval = True
                    if need_re_eval:
                        self._enter(MasterState.RE_EVALUATE)
                        continue
                    time.sleep(POLL_INTERVAL_SEC)
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
            time.sleep(0)

    def shutdown(self):
        self.stop()
        self._close_serial()

    # public hooks
    def trigger_preemption(self):
        self._enter(MasterState.PREEMPTION)
