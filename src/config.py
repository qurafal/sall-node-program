import json
import os
import math

CONFIG_FILE = os.path.join(os.path.dirname(__file__), '../data/config.json')
EXAMPLE_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '../data/config_example.json')

REQUIRED_KEYS = [
    "min_green", "max_extra_green",
    "conflict_zone_meter", "vehicle_speed_kmh", "lanes",
    "serial_port",
    "saturation_headway_sec", "re_evaluate_interval_sec", "yolo_poll_interval_sec"
]

MAX_CYCLE_STEPS = 8

# EFSM/cloud config defaults
DEFAULT_INACTIVE_QUEUE_THRESHOLD = 5
DEFAULT_STARVATION_TOLERANCE = 30
DEFAULT_FALLBACK_SEQUENCE = []
DEFAULT_SATURATION_HEADWAY_SEC = 2.1
DEFAULT_FALLBACK_SEQUENCE = []
DEFAULT_RE_EVALUATE_INTERVAL_SEC = 10


def _parse_lanes(lanes, errors) -> tuple:
    """
    Parse lanes format: list of [count, stream_id1, stream_id2, ...]
    Stream IDs are 0-indexed to match conflict_matrix.
    Returns (lane_id_to_count: dict[int, int], lanes_info: list)
    """
    if lanes is None:
        return {}, []
    if not isinstance(lanes, list):
        errors.append("CRITICAL ERROR: 'lanes' harus berupa daftar [[count, stream_id...], ...]!")
        return {}, []
    if not lanes:
        errors.append("CRITICAL ERROR: 'lanes' tidak boleh kosong!")
        return {}, []

    lane_id_to_count = {}
    lanes_info = []
    seen_streams = set()

    for idx, entry in enumerate(lanes):
        if isinstance(entry, bool):
            errors.append(
                f"CRITICAL ERROR: Entri #{idx + 1} di 'lanes' tidak valid: {entry!r}")
            continue
        if not isinstance(entry, list):
            errors.append(
                f"CRITICAL ERROR: Entri #{idx + 1} di 'lanes' harus berupa daftar [count, stream_id...]!")
            continue
        if len(entry) < 1:
            errors.append(
                f"CRITICAL ERROR: Entri #{idx + 1} di 'lanes' tidak boleh kosong.")
            continue

        count = 1
        stream_ids = entry

        valid_stream_ids = []
        for sid in stream_ids:
            if isinstance(sid, bool) or not isinstance(sid, int) or not (0 <= sid <= 7):
                errors.append(
                    f"CRITICAL ERROR: Stream ID '{sid}' tidak valid di entri #{idx + 1}! Hanya 0-7 yang diperbolehkan (0-indexed).")
                continue
            if sid in seen_streams:
                errors.append(
                    f"CRITICAL ERROR: Duplikat stream ID {sid} di 'lanes'!")
                continue
            seen_streams.add(sid)
            lane_id_to_count[sid] = count
            valid_stream_ids.append(sid)

        if len(stream_ids) != len(valid_stream_ids):
            continue

        lanes_info.append({
            "stream_ids": list(valid_stream_ids)
        })

    return lane_id_to_count, lanes_info


def _validate_and_parse(raw: dict) -> tuple:
    """Validasi + parsing tanpa melempar exception.

    Mengembalikan (errors, lane_id_to_count, lanes_info).
    """
    errors = []
    if not isinstance(raw, dict):
        errors.append("CRITICAL ERROR: Konfigurasi harus berupa objek JSON!")
        return errors, {}, []

    for key in REQUIRED_KEYS:
        if key not in raw:
            errors.append(
                f"CRITICAL ERROR: Kunci '{key}' hilang dari konfigurasi! Hentikan sistem.")

    # Validate numeric/integer fields
    int_fields = ["min_green", "max_extra_green", "conflict_zone_meter", "vehicle_speed_kmh", "baud_rate"]
    for key in int_fields:
        if key in raw:
            try:
                val = int(raw[key])
                if key == "baud_rate" and val <= 0:
                    errors.append(f"CRITICAL ERROR: '{key}' harus > 0!")
                elif key != "baud_rate" and val < 0:
                    errors.append(f"CRITICAL ERROR: '{key}' tidak boleh negatif!")
            except Exception:
                errors.append(f"CRITICAL ERROR: '{key}' harus berupa angka bulat!")

    # Validate yellow forms exist (only y_sec/yellow_sec)
    has_yellow = False
    for k in ["y_sec", "yellow_sec"]:
        if k in raw:
            try:
                v = int(raw[k])
                if v < 0:
                    errors.append(f"CRITICAL ERROR: '{k}' tidak boleh negatif!")
                has_yellow = True
                break
            except Exception:
                errors.append(f"CRITICAL ERROR: '{k}' harus berupa angka bulat!")
                has_yellow = True
                break
    if not has_yellow:
        errors.append("CRITICAL ERROR: Konfigurasi harus menyertakan durasi kuning (y_sec/yellow_sec)!")

    # Derive r_sec from conflict_zone_meter and vehicle_speed_kmh (fault intolerant, no default)
    try:
        czm = raw.get("conflict_zone_meter")
        vsm = raw.get("vehicle_speed_kmh")
        if czm is None or vsm is None:
            errors.append("CRITICAL ERROR: 'conflict_zone_meter' dan 'vehicle_speed_kmh' diperlukan untuk menghitung r_sec!")
        else:
            czm_i = int(czm)
            vsm_i = int(vsm)
            if czm_i < 0 or vsm_i <= 0:
                errors.append("CRITICAL ERROR: 'conflict_zone_meter' tidak boleh negatif dan 'vehicle_speed_kmh' harus > 0!")
            else:
                r_sec_calc = math.ceil(czm_i / (vsm_i/3.6)) if vsm_i > 0 else 0
                if r_sec_calc < 0:
                    r_sec_calc = 0
                # Store computed r_sec in raw for later use
                raw["_r_sec_computed"] = r_sec_calc
    except Exception as e:
        errors.append(f"CRITICAL ERROR: Gagal menghitung r_sec dari conflict_zone_meter/vehicle_speed_kmh: {e}")

    has_red = False
    for k in ["r_sec", "red_sec"]:
        if k in raw:
            try:
                v = int(raw[k])
                if v < 0:
                    errors.append(f"CRITICAL ERROR: '{k}' tidak boleh negatif!")
                has_red = True
                break
            except Exception:
                errors.append(f"CRITICAL ERROR: '{k}' harus berupa angka bulat!")
                has_red = True
                break
    if not has_red and "_r_sec_computed" not in raw:
        errors.append("CRITICAL ERROR: Konfigurasi harus menyertakan durasi merah (r_sec/red_sec) atau memungkinkan perhitungan dari conflict_zone_meter/vehicle_speed_kmh!")

    # Validate serial_port
    if "serial_port" in raw:
        sp = raw["serial_port"]
        if not isinstance(sp, str) or sp.strip() == "":
            errors.append("CRITICAL ERROR: 'serial_port' harus berupa string tidak kosong!")

    # Validate required numeric params
    for key in ["saturation_headway_sec", "re_evaluate_interval_sec", "yolo_poll_interval_sec"]:
        if key in raw:
            try:
                v = float(raw[key])
                if v <= 0:
                    errors.append(f"CRITICAL ERROR: '{key}' harus > 0!")
            except Exception:
                errors.append(f"CRITICAL ERROR: '{key}' harus berupa angka!")

    lane_id_to_count, lanes_info = _parse_lanes(raw.get("lanes"), errors)

    # Validate fallback sequence against conflict matrix if present
    try:
        cm = raw.get("conflict_matrix") or raw.get("conflicts")
        fb = raw.get("fallback_sequence") or []
        if isinstance(cm, (list, tuple)) and len(cm) == 8 and isinstance(fb, list):
            for idx, phase in enumerate(fb):
                try:
                    if isinstance(phase, dict):
                        active = phase.get("active_streams", phase.get("streams", []))
                    elif isinstance(phase, (list, tuple)) and len(phase) >= 1:
                        active = phase[0]
                    else:
                        continue
                    if isinstance(active, (list, tuple, set)):
                        act_list = list(active)
                        for i in range(len(act_list)):
                            for j in range(i+1, len(act_list)):
                                s1 = int(act_list[i]); s2 = int(act_list[j])
                                if 0 <= s1 < 8 and 0 <= s2 < 8:
                                    if cm[s1][s2] == 1 or cm[s2][s1] == 1:
                                        errors.append(f"CRITICAL ERROR: Fallback sequence phase {idx} has conflicting active streams {s1} and {s2}!")
                except Exception:
                    continue
    except Exception:
        pass

    return errors, lane_id_to_count, lanes_info


def _validate_lanes_vs_conflict_matrix(lanes_info, conflict_matrix, errors):
    """Cross-check lanes array against conflict_matrix to catch deadlocks."""
    if not lanes_info or not conflict_matrix:
        return
    try:
        # Streams in the same lane group share a single physical lane - must not conflict
        for entry in lanes_info:
            stream_ids = entry.get("stream_ids", [])
            if len(stream_ids) >= 2:
                # These streams share a single physical lane - must not conflict
                for i in range(len(stream_ids)):
                    for j in range(i + 1, len(stream_ids)):
                        s1 = stream_ids[i]
                        s2 = stream_ids[j]
                        if 0 <= s1 < 8 and 0 <= s2 < 8:
                            if conflict_matrix[s1][s2] == 1 or conflict_matrix[s2][s1] == 1:
                                errors.append(
                                    f"CRITICAL ERROR: Deadlock - stream {s1} and {s2} share a single physical lane (N==1) but are marked as conflicting in conflict_matrix!")
                                conflict_matrix[s1][s2] = 0
                                conflict_matrix[s2][s1] = 0
            pass
    except Exception:
        pass


class ConfigManager:
    def __init__(self):
        self.config = {}
        self._valid = False
        self._errors = []
        self._lane_id_to_count = {}
        self._lanes_info = []
        self._last_mtime = None

        try:
            with open(CONFIG_FILE, 'r') as f:
                raw = json.load(f)
            self._last_mtime = os.path.getmtime(CONFIG_FILE)
        except FileNotFoundError:
            self._errors = [
                "CRITICAL ERROR: File config.json tidak ditemukan! "
                "Gunakan 'config init' pada CLI atau salin data/config_example.json."]
            return
        except (OSError, json.JSONDecodeError) as e:
            self._errors = [f"CRITICAL ERROR: Gagal membaca konfigurasi: {e}"]
            return

        errors, lane_id_to_count, lanes_info = _validate_and_parse(raw)
        self._errors = errors
        if errors:
            return
        self.config = raw
        self._lane_id_to_count = lane_id_to_count
        self._lanes_info = lanes_info
        self._valid = True

    @property
    def is_valid(self) -> bool:
        return self._valid

    @property
    def errors(self) -> list:
        return list(self._errors)

    def fetch_cloud(self):
        """Fetch configuration from cloud (pluggable). Falls back to local config with EFSM fields."""
        cfg = {}
        try:
            # Prefer cloud-like keys from config if present
            if isinstance(self.config, dict):
                # streams may be under 'streams' or 'cloud_streams'
                streams = self.config.get("streams") or self.config.get("cloud_streams")
                conflict_matrix = self.config.get("conflict_matrix") or self.config.get("conflicts")
                if streams is not None:
                    cfg["streams"] = streams
                if conflict_matrix is not None:
                    cfg["conflict_matrix"] = conflict_matrix
                for k in ["min_green", "max_extra_green", "inactive_queue_threshold", "starvation_tolerance",
                          "yellow_sec", "red_sec", "y_sec", "r_sec",
                          "fallback_sequence", "saturation_headway_sec", "re_evaluate_interval_sec"]:
                    if k in self.config:
                        cfg[k] = self.config[k]
                # derive yellow_sec from y_sec if missing
                if "yellow_sec" not in cfg and "y_sec" in self.config:
                    try:
                        cfg["yellow_sec"] = int(self.config["y_sec"])
                    except Exception:
                        pass
        except Exception:
            pass
        return cfg

    def reload_if_changed(self) -> bool:
        """Muat ulang config.json jika berubah. Mengembalikan True jika valid."""
        try:
            mtime = os.path.getmtime(CONFIG_FILE)
        except OSError:
            return False
        if self._last_mtime is not None and mtime <= self._last_mtime:
            return False
        self._last_mtime = mtime

        try:
            with open(CONFIG_FILE, 'r') as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._valid = False
            self._errors = [f"CRITICAL ERROR: Gagal membaca konfigurasi: {e}"]
            return False

        errors, lane_id_to_count, lanes_info = _validate_and_parse(raw)
        self._errors = errors
        if errors:
            self._valid = False
            return False

        self.config = raw
        self._lane_id_to_count = lane_id_to_count
        self._lanes_info = lanes_info
        self._valid = True
        return True

    def update_config(self, new_data: dict) -> tuple:
        """Gabungkan perubahan, validasi, baru tulis ke file.

        Mengembalikan (ok: bool, errors: list[str]). Jika tidak valid,
        tidak ada yang ditulis dan konfigurasi di memori tetap valid.
        """
        try:
            with open(CONFIG_FILE, 'r') as f:
                base = json.load(f)
        except (OSError, json.JSONDecodeError):
            base = {}
        merged = dict(base)
        merged.update(new_data)

        errors, lane_id_to_count, lanes_info = _validate_and_parse(merged)
        if errors:
            return False, errors

        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(merged, f, indent=4)
        except OSError as e:
            return False, [f"CRITICAL ERROR: Gagal menulis konfigurasi: {e}"]

        self.config = merged
        self._lane_id_to_count = lane_id_to_count
        self._lanes_info = lanes_info
        self._valid = True
        self._errors = []
        try:
            self._last_mtime = os.path.getmtime(CONFIG_FILE)
        except OSError:
            pass
        return True, []

    def print_all_config(self):
        print("\n=== STATUS KONFIGURASI NODE ===")
        if not self._valid:
            print("Status            : TIDAK VALID — sistem menunggu perbaikan")
            for err in self._errors:
                print(f"  - {err}")
            print("===============================\n")
            return
        print("Status            : valid")
        print(f"Min Green         : {self.min_green} detik")
        print(f"Max Extra Green   : {self.max_extra_green} detik")
        print(f"Yellow Sec        : {self.yellow_sec} detik")
        print(f"Red Sec           : {self.red_sec} detik")
        print(f"Conflict Zone     : {self.conflict_zone_meter} meter")
        print(f"Vehicle Speed     : {self.vehicle_speed_kmh} m/s")
        print(f"Lanes (ID->count) : {self.lanes}")
        print(f"Serial Port       : {self.serial_port}")
        print(f"Baud Rate         : {self.baud_rate}")
        print(f"YOLO Poll Interval: {self.yolo_poll_interval_sec} detik")
        print("===============================\n")

    @property
    def min_green(self) -> int: return self.config["min_green"]

    @property
    def max_extra_green(self) -> int: return self.config["max_extra_green"]

    @property
    def conflict_zone_meter(self) -> int: return int(self.config["conflict_zone_meter"])

    @property
    def vehicle_speed_kmh(self) -> int: return int(self.config["vehicle_speed_kmh"])

    @property
    def lanes_info(self) -> list:
        return list(self._lanes_info)

    @property
    def lanes(self) -> dict:
        return self._lane_id_to_count

    def validate_lanes_conflicts(self, conflict_matrix):
        errors = []
        _validate_lanes_vs_conflict_matrix(self._lanes_info, conflict_matrix, errors)
        if errors:
            for err in errors:
                print(f"[CONFIG][WARN] {err}")
        return len(errors) == 0

    @property
    def starvation_tolerance(self) -> int:
        return int(self.config["starvation_tolerance"])

    @property
    def yellow_sec(self) -> int:
        if "y_sec" in self.config:
            return int(self.config["y_sec"])
        if "yellow_sec" in self.config:
            return int(self.config["yellow_sec"])
        raise KeyError("yellow duration not found")

    @property
    def red_sec(self) -> int:
        if "r_sec" in self.config:
            return int(self.config["r_sec"])
        if "red_sec" in self.config:
            return int(self.config["red_sec"])
        # Try computed
        if "_r_sec_computed" in self.config:
            return int(self.config["_r_sec_computed"])
        # Compute on-the-fly if possible
        try:
            czm = int(self.config["conflict_zone_meter"])
            vsm = int(self.config["vehicle_speed_kmh"])
            if vsm <= 0:
                raise KeyError("vehicle_speed_kmh must be > 0")
            r_sec_calc = math.ceil(czm / (vsm/3.6))
            return int(r_sec_calc)
        except Exception:
            raise KeyError("red duration not found")

    @property
    def saturation_headway_sec(self) -> float:
        return float(self.config["saturation_headway_sec"])

    @property
    def re_evaluate_interval_sec(self) -> float:
        return float(self.config["re_evaluate_interval_sec"])

    @property
    def serial_port(self) -> str:
        return str(self.config["serial_port"])

    @property
    def baud_rate(self) -> int:
        if "baud_rate" in self.config:
            return int(self.config["baud_rate"])
        return 115200

    @property
    def yolo_poll_interval_sec(self) -> float:
        return float(self.config["yolo_poll_interval_sec"])
