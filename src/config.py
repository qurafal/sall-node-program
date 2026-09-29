import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(__file__), '../data/config.json')

class ConfigManager:
    def __init__(self):
        self.config = self.load_config()
        self._validate_config()
        # Mengonversi string "lanes" menjadi integer saat booting
        self._parsed_lanes = self._parse_lanes()

    def load_config(self) -> dict:
        if not os.path.exists(CONFIG_FILE):
            raise FileNotFoundError("CRITICAL ERROR: File config.json tidak ditemukan!")
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)

    def _validate_config(self):
        required_keys = [
            "identity", "role", "assigned_master", "assigned_slaves", 
            "min_green", "max_extra_green", "yellow_duration", 
            "conflict_zone_meter", "vehicle_speed_ms", "lanes"
        ]
        for key in required_keys:
            if key not in self.config:
                raise KeyError(f"CRITICAL ERROR: Kunci '{key}' hilang dari konfigurasi! Hentikan sistem.")

    def _parse_lanes(self) -> dict:
        return {int(k): v for k, v in self.config["lanes"].items()}

    def update_config(self, new_data: dict):
        self.config.update(new_data)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)
        # Memperbarui cache lanes jika ada perubahan konfigurasi
        self._parsed_lanes = self._parse_lanes()
        print(f"[NODE {self.identity}] Konfigurasi diperbarui & disimpan.")

    def print_all_config(self):
        print("\n=== STATUS KONFIGURASI NODE ===")
        print(f"Identity          : {self.identity}")
        print(f"Role              : {self.role.upper()}")
        print(f"Assigned Master   : {self.assigned_master}")
        print(f"Assigned Slaves   : {self.assigned_slaves}")
        print(f"Min Green         : {self.min_green} detik")
        print(f"Max Extra Green   : {self.max_extra_green} detik")
        print(f"Yellow Duration   : {self.yellow_duration} detik")
        print(f"Conflict Zone     : {self.conflict_zone_meter} meter")
        print(f"Vehicle Speed     : {self.vehicle_speed_ms} m/s")
        print(f"Lanes Mapping     : {self.lanes}")
        print("===============================\n")

      
    @property
    def identity(self) -> int: return self.config["identity"]

    @property
    def role(self) -> str: return self.config["role"]

    @property
    def assigned_master(self) -> int: return self.config["assigned_master"]

    @property
    def assigned_slaves(self) -> list: return self.config["assigned_slaves"]

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
        # Mengembalikan dictionary yang kuncinya sudah dijamin integer
        return self._parsed_lanes