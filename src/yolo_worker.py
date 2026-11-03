import random
import threading
import time

from config import ConfigManager


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
            prepared_cycle_payload[:] = b"YOLO:"
        self._last_stream_counts = data

    def get_stream_counts(self) -> dict:
        stream_counts = {}
        lanes_info = getattr(self.config, "lanes_info", []) if hasattr(self.config, "lanes_info") else []
        seen = []
        if lanes_info:
            for entry in lanes_info:
                for sid in entry.get("stream_ids", []):
                    if sid not in seen:
                        seen.append(sid)
        if not seen:
            lanes = getattr(self.config, "lanes", {}) or {}
            seen = list(lanes.keys()) if isinstance(lanes, dict) else []
        for sid in seen:
            if random.random() > 0.8:
                stream_counts[f"S{sid}"] = {"car": 0, "motorcycle": 0, "truck": 0}
            else:
                stream_counts[f"S{sid}"] = {
                    "car": random.randint(0, 15),
                    "motorcycle": random.randint(0, 30),
                    "truck": random.randint(0, 3),
                }
        return stream_counts

    def get_dummy_vehicles(self) -> dict:
        return self.get_stream_counts()