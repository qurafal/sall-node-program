import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(__file__), '../data/config.json')

class ConfigManager:
    def __init__(self):
        self.config = self.load_config()
        self._validate_config()

    def load_config(self) -> dict:
        if not os.path.exists(CONFIG_FILE):
            raise FileNotFoundError("CRITICAL ERROR: File config.json tidak ditemukan!")
        
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)

    def _validate_config(self):
        """Pengecekan di awal booting. Jika ada kunci yang hilang, sistem langsung crash."""
        required_keys = ["identity", "role", "assigned_master", "assigned_slave", "min_green", "max_extra_green"]
        for key in required_keys:
            if key not in self.config:
                raise KeyError(f"CRITICAL ERROR: Kunci '{key}' hilang dari konfigurasi! Hentikan sistem.")

    def update_config(self, new_data: dict):
        self.config.update(new_data)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)
        print(f"[NODE {self.identity}] Konfigurasi diperbarui & disimpan.")

    def print_all_config(self):
        """Mencetak seluruh properti konfigurasi ke terminal untuk debugging/log."""
        print("\n=== STATUS KONFIGURASI NODE ===")
        print(f"Identity        : {self.identity}")
        print(f"Role            : {self.role.upper()}")
        print(f"Assigned Master : {self.assigned_master}")
        print(f"Assigned Slave  : {self.assigned_slave}")
        print(f"Min Green       : {self.min_green} detik")
        print(f"Max Extra Green : {self.max_extra_green} detik")
        print("===============================\n")

    # Menggunakan kurung siku []. Tidak ada nilai default/fallback!
    # Jika dipanggil dan kuncinya tidak ada, program akan langsung berhenti.

    @property
    def identity(self) -> int:
        return self.config["identity"]

    @property
    def role(self) -> str:
        return self.config["role"]

    @property
    def assigned_master(self) -> int:
        return self.config["assigned_master"]

    @property
    def assigned_slave(self) -> list:
        return self.config["assigned_slave"]

    @property
    def min_green(self) -> int:
        return self.config["min_green"]

    @property
    def max_extra_green(self) -> int:
        return self.config["max_extra_green"]