import random
import threading
import time

from config import ConfigManager
from protocol import LANE_DIRECTIONS


prepared_cycle_payload = bytearray()
payload_lock = threading.Lock()


class YoLoWorker:
    def __init__(self, config_mgr: ConfigManager):
        self.config = config_mgr
        self._stop_event = threading.Event()
        self._thread = None
        self._last_invalid_warn = 0.0
        self._tick()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="yolo_worker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.config.yolo_poll_interval_sec + 1)

    def _run(self):
        while not self._stop_event.wait(self.config.yolo_poll_interval_sec):
            self._tick()

    def _tick(self):
        self.config.reload_if_changed()
        if not self.config.is_valid:
            now = time.time()
            if now - self._last_invalid_warn >= 10:
                self._last_invalid_warn = now
                print(f"[YOLO][WARN] Konfigurasi tidak valid, mempertahankan "
                      f"siklus terakhir: {self.config.errors}")
            return
        data = self.get_stream_counts()
        with payload_lock:
            prepared_cycle_payload[:] = b"YOLO:"  # marker for new format if needed
        # Store in a way accessible; for now attach to instance pattern via module? 
        # But master needs to call get_stream_counts() directly
        self._last_stream_counts = data

    def get_stream_counts(self) -> dict:
        """Return per-stream raw counts. Master maps lanes->streams using config/cloud.
        Format: {"S0": {"car": 5, "motorcycle": 12, "truck": 1}} or just per-stream by key.
        """
        stream_counts = {}
        # Use local config.lanes as per plan decision 9/23? Plan says "keeps using local `config.lanes`; master maps lanes→streams"
        lanes = getattr(self.config, "lanes", {}) or {}
        # Build dummy data per direction/lane group
        # Assign to streams in order of lanes entries
        stream_idx = 0
        # Iterate over lanes in order they appear conceptually
        try:
            # lanes is dict lane_id->count; map by direction groups similar to before
            direction_to_vehicles = {}
            for lane_id, count in lanes.items():
                direction = LANE_DIRECTIONS.get(lane_id)
                if direction not in direction_to_vehicles:
                    if random.random() > 0.8:
                        direction_to_vehicles[direction] = {"car": 0, "motorcycle": 0, "truck": 0}
                    else:
                        direction_to_vehicles[direction] = {
                            "car": random.randint(0, 15),
                            "motorcycle": random.randint(0, 30),
                            "truck": random.randint(0, 3)
                        }
            # Map directions to streams - simple assignment
            # Get unique directions in order
            seen_dirs = []
            for lane_id in lanes.keys():
                d = LANE_DIRECTIONS.get(lane_id)
                if d and d not in seen_dirs:
                    seen_dirs.append(d)
            for i, d in enumerate(seen_dirs):
                v = direction_to_vehicles.get(d, {"car": 0, "motorcycle": 0, "truck": 0})
                stream_counts[f"S{i}"] = {
                    "car": v.get("car", 0),
                    "motorcycle": v.get("motorcycle", 0),
                    "truck": v.get("truck", 0),
                }
                stream_idx += 1
        except Exception:
            pass
        return stream_counts

    def get_dummy_vehicles(self) -> dict:
        # Kept for compatibility
        return self.get_stream_counts()