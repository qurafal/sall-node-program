import json
import os

from protocol import (
    LANES_STATE,
    OPPOSING_APPROACH,
)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), '../data/config.json')
EXAMPLE_CONFIG_FILE = os.path.join(os.path.dirname(__file__), '../data/config_example.json')

REQUIRED_KEYS = [
    "min_green", "max_extra_green", "yellow_duration",
    "conflict_zone_meter", "vehicle_speed_ms", "lanes",
    "serial_port"
]

VALID_PAIRS = [(1, 2), (3, 4), (5, 6), (7, 8)]
MAX_CYCLE_STEPS = 8

# EFSM/cloud config defaults
DEFAULT_INACTIVE_QUEUE_THRESHOLD = 5
DEFAULT_STARVATION_TOLERANCE = 30
DEFAULT_YELLOW_SEC = 3
DEFAULT_RED_SEC = 2
DEFAULT_SATURATION_HEADWAY_SEC = 2.1
DEFAULT_FALLBACK_SEQUENCE = []
DEFAULT_RE_EVALUATE_INTERVAL_SEC = 10


def _parse_lanes(lanes, errors) -> tuple:
    """
    Parse new lanes format: list of [count, lamp_id1, lamp_id2, ...]
    Returns (lane_id_to_count: dict[int, int], phases: list[TrafficState])
    """
    if lanes is None:
        return {}, []
    if not isinstance(lanes, list):
        errors.append("CRITICAL ERROR: 'lanes' harus berupa daftar [[count, lamp_id...], ...]!")
        return {}, []
    if not lanes:
        errors.append("CRITICAL ERROR: 'lanes' tidak boleh kosong!")
        return {}, []

    lane_id_to_count = {}
    normalized = []
    seen = set()
    pair_starts = set()
    has_pair = False

    for idx, entry in enumerate(lanes):
        if isinstance(entry, bool):
            errors.append(
                f"CRITICAL ERROR: Entri #{idx+1} di 'lanes' tidak valid: {entry!r}")
            continue
        if not isinstance(entry, list):
            errors.append(
                f"CRITICAL ERROR: Entri #{idx+1} di 'lanes' harus berupa daftar [count, lamp_id...]!")
            continue
        if len(entry) < 2:
            errors.append(
                f"CRITICAL ERROR: Entri #{idx+1} di 'lanes' minimal 2 elemen [count, lamp_id]!")
            continue

        count = entry[0]
        lamp_ids = entry[1:]

        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            errors.append(
                f"CRITICAL ERROR: Jumlah lajur (elemen pertama) entri #{idx+1} tidak valid: {count!r}. Harus integer positif.")
            continue

        valid_lamp_ids = []
        for lamp_id in lamp_ids:
            if isinstance(lamp_id, bool) or not isinstance(lamp_id, int) or not (1 <= lamp_id <= 8):
                errors.append(
                    f"CRITICAL ERROR: Lamp ID '{lamp_id}' tidak valid di entri #{idx+1}! Hanya 1-8 yang diperbolehkan.")
                continue
            if lamp_id in seen:
                errors.append(
                    f"CRITICAL ERROR: Duplikat lamp ID {lamp_id} di 'lanes'!")
                continue
            seen.add(lamp_id)
            lane_id_to_count[lamp_id] = count
            valid_lamp_ids.append(lamp_id)

        if len(lamp_ids) != len(valid_lamp_ids):
            continue

        if len(valid_lamp_ids) == 1:
            normalized.append(("lane", valid_lamp_ids[0]))
        elif len(valid_lamp_ids) == 2:
            pair = tuple(valid_lamp_ids)
            if pair not in VALID_PAIRS:
                errors.append(
                    f"CRITICAL ERROR: Pasangan {list(pair)} tidak valid di entri #{idx+1}! "
                    f"Hanya [1,2], [3,4], [5,6], [7,8] yang diperbolehkan.")
                continue
            if pair in seen:
                errors.append(
                    f"CRITICAL ERROR: Duplikat entri pasangan {list(pair)} di 'lanes'!")
                continue
            seen.add(pair)
            pair_starts.add(pair[0])
            has_pair = True
            normalized.append(("pair", pair))
        else:
            errors.append(
                f"CRITICAL ERROR: Entri #{idx+1} terlalu banyak lamp ID ({len(valid_lamp_ids)}). "
                f"Maksimal 2 lamp ID per entri (single atau pair).")
            continue

    for kind, value in normalized:
        if kind != "pair":
            continue
        opponent = OPPOSING_APPROACH[value[0]]
        if opponent not in pair_starts:
            errors.append(
                f"CRITICAL ERROR: Pasangan {list(value)} membutuhkan pasangan lawannya "
                f"[{opponent},{opponent+1}] di 'lanes'!")

    if has_pair:
        for kind, value in normalized:
            if kind == "lane" and value % 2 == 0:
                errors.append(
                    f"CRITICAL ERROR: Lajur genap {value} tidak boleh aktif sendiri "
                    f"selama ada pasangan di 'lanes' (gunakan [{value-1},{value}]).")

    phases = _derive_phases(normalized)
    if len(phases) > MAX_CYCLE_STEPS:
        errors.append(
            f"CRITICAL ERROR: Siklus terlalu panjang: {len(phases)} fase "
            f"(maksimum {MAX_CYCLE_STEPS}).")

    return lane_id_to_count, phases


def _derive_phases(normalized) -> list:
    """Turunkan fase siklus sesuai urutan 'lanes'.

    Pasangan [a,b] menyusulkan APPROACH langsung; ketika pasangan lawan
    dari satu set sudah keduanya muncul, OPPOSING disisipkan tepat
    setelah APPROACH pasangan kedua.
    """
    phases = []
    seen_starts = set()
    for kind, value in normalized:
        if kind == "lane":
            phases.append(LANES_STATE[(value,)])
            continue
        phases.append(LANES_STATE[tuple(value)])
        start = value[0]
        seen_starts.add(start)
        if OPPOSING_APPROACH[start] in seen_starts:
            partner = OPPOSING_APPROACH[start]
            phases.append(LANES_STATE[(min(start, partner), max(start, partner))])
    return phases


def _validate_and_parse(raw: dict) -> tuple:
    """Validasi + parsing tanpa melempar exception.

    Mengembalikan (errors, lane_id_to_count, enabled_phases).
    """
    errors = []
    for key in REQUIRED_KEYS:
        if key not in raw:
            errors.append(
                f"CRITICAL ERROR: Kunci '{key}' hilang dari konfigurasi! Hentikan sistem.")

    lane_id_to_count, phases = _parse_lanes(raw.get("lanes"), errors)
    return errors, lane_id_to_count, phases


class ConfigManager:
    def __init__(self):
        self.config = {}
        self._valid = False
        self._errors = []
        self._lane_id_to_count = {}
        self._enabled_phases = []
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

        errors, lane_id_to_count, phases = _validate_and_parse(raw)
        self._errors = errors
        if errors:
            return
        self.config = raw
        self._lane_id_to_count = lane_id_to_count
        self._enabled_phases = phases
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
                for k in ["min_green", "inactive_queue_threshold", "starvation_tolerance",
                          "yellow_sec", "red_sec", "y_sec", "r_sec",
                          "fallback_sequence", "saturation_headway_sec", "re_evaluate_interval_sec"]:
                    if k in self.config:
                        cfg[k] = self.config[k]
                # also map yellow_duration (s) to yellow_sec if missing
                if "yellow_sec" not in cfg and "y_sec" not in cfg and "yellow_duration" in self.config:
                    try:
                        cfg["yellow_sec"] = int(self.config["yellow_duration"])
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

        errors, lane_id_to_count, phases = _validate_and_parse(raw)
        self._errors = errors
        if errors:
            self._valid = False
            return False

        self.config = raw
        self._lane_id_to_count = lane_id_to_count
        self._enabled_phases = phases
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

        errors, lane_id_to_count, phases = _validate_and_parse(merged)
        if errors:
            return False, errors

        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(merged, f, indent=4)
        except OSError as e:
            return False, [f"CRITICAL ERROR: Gagal menulis konfigurasi: {e}"]

        self.config = merged
        self._lane_id_to_count = lane_id_to_count
        self._enabled_phases = phases
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
        else:
            print("Status            : valid")
        print(f"Min Green         : {self.config.get('min_green')} detik")
        print(f"Max Extra Green   : {self.config.get('max_extra_green')} detik")
        print(f"Yellow Duration   : {self.config.get('yellow_duration')} detik")
        print(f"Conflict Zone     : {self.config.get('conflict_zone_meter')} meter")
        print(f"Vehicle Speed     : {self.config.get('vehicle_speed_ms')} m/s")
        print(f"Lanes (ID->count) : {self.lanes}")
        print(f"Fase Turunan      : {[s.name for s in self._enabled_phases]}")
        print(f"Serial Port       : {self.config.get('serial_port')}")
        print(f"Baud Rate         : {self.baud_rate}")
        print(f"YOLO Poll Interval: {self.yolo_poll_interval_sec} detik")
        print("===============================\n")

    @property
    def min_green(self) -> int: return self.config["min_green"]

    @property
    def max_extra_green(self) -> int: return self.config["max_extra_green"]

    @property
    def yellow_duration(self) -> int: return self.config["yellow_duration"]

    @property
    def conflict_zone_meter(self) -> int: return self.config["conflict_zone_meter"]

    @property
    def vehicle_speed_ms(self) -> int: return self.config["vehicle_speed_ms"]

    @property
    def lanes(self) -> dict:
        return self._lane_id_to_count

    @property
    def enabled_phases(self) -> list:
        return self._enabled_phases

    @property
    def inactive_queue_threshold(self) -> int:
        try:
            return int(self.config.get("inactive_queue_threshold", DEFAULT_INACTIVE_QUEUE_THRESHOLD))
        except Exception:
            return DEFAULT_INACTIVE_QUEUE_THRESHOLD

    @property
    def starvation_tolerance(self) -> int:
        try:
            return int(self.config.get("starvation_tolerance", DEFAULT_STARVATION_TOLERANCE))
        except Exception:
            return DEFAULT_STARVATION_TOLERANCE

    @property
    def yellow_sec(self) -> int:
        try:
            if "y_sec" in self.config:
                return int(self.config["y_sec"])
            if "yellow_sec" in self.config:
                return int(self.config["yellow_sec"])
            if "yellow_duration" in self.config:
                return int(self.config["yellow_duration"])
        except Exception:
            pass
        return DEFAULT_YELLOW_SEC

    @property
    def red_sec(self) -> int:
        try:
            if "r_sec" in self.config:
                return int(self.config["r_sec"])
            if "red_sec" in self.config:
                return int(self.config["red_sec"])
        except Exception:
            pass
        return DEFAULT_RED_SEC

    @property
    def saturation_headway_sec(self) -> float:
        try:
            return float(self.config.get("saturation_headway_sec", DEFAULT_SATURATION_HEADWAY_SEC))
        except Exception:
            return DEFAULT_SATURATION_HEADWAY_SEC

    @property
    def re_evaluate_interval_sec(self) -> float:
        try:
            return float(self.config.get("re_evaluate_interval_sec", DEFAULT_RE_EVALUATE_INTERVAL_SEC))
        except Exception:
            return DEFAULT_RE_EVALUATE_INTERVAL_SEC

    @property
    def fallback_sequence(self) -> list:
        try:
            fs = self.config.get("fallback_sequence", DEFAULT_FALLBACK_SEQUENCE)
            if isinstance(fs, list):
                return fs
            if isinstance(fs, str):
                # try to parse as json string
                try:
                    import json as _json
                    val = _json.loads(fs)
                    if isinstance(val, list):
                        return val
                except Exception:
                    pass
        except Exception:
            pass
        return []