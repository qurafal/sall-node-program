import json
import os
import shutil
import struct
import threading

from config import CONFIG_FILE, EXAMPLE_CONFIG_FILE
from protocol import GREEN_STATES, LANES_STATE, STATE_LANES, TrafficState, UtilsCmd
from yolo_worker import payload_lock, prepared_cycle_payload

SETTABLE_KEYS = {
    "min_green", "max_extra_green", "yellow_duration",
    "conflict_zone_meter", "vehicle_speed_ms", "lanes",
    "serial_port", "baud_rate", "yolo_poll_interval_sec",
}

HELP_TEXT = """Perintah:
  help                 daftar perintah ini
  states               tabel state hijau (nama, hex, lajur hijau)
  set <target>         override manual: nama state (OPPOSING_1_5_GREEN),
                        hex (0x1C / 1C), atau lajur (3 / 1,5 / 1,2)
                        catatan: override bersifat sesaat — fase siklus
                        menyusul kembali di slave
  emergency            mode darurat (semua merah, timer dibekukan)
  resume               lanjut dari mode darurat
  force-next           lompat ke fase berikutnya (GREEN->YELLOW->RED->NEXT)
  simulate on|off      aktifkan / nonaktifkan mode simulasi di slave
  cycle                tampilkan siklus yang disiapkan (fase + durasi)
  config               tampilkan konfigurasi (sama dengan 'config get')
  config set <key> <nilai-json>
                        ubah kunci konfigurasi tingkat atas
                        (mis. config set min_green 15 / config set serial_port COM3 /
                        config set lanes '[[1,1,2],[2,3,4],[1,5,6],[1,7,8]]')
  config init          buat data/config.json dari data/config_example.json
  quit                 hentikan program"""

class CliInterface:
    def __init__(self, config_mgr):
        self.config = config_mgr
        self.controller = None
        self._running = True
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running

    def attach(self, controller):
        self.controller = controller

    def start(self):
        self._thread = threading.Thread(target=self._run, name="cli", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        print("[CLI] Antarmuka CLI aktif. Ketik 'help' untuk daftar perintah.")
        while self._running:
            try:
                line = input("sall> ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                break
            if not line:
                continue
            try:
                self._dispatch(line)
            except Exception as e:
                print(f"[CLI][ERROR] {type(e).__name__}: {e}")
        print("[CLI] CLI ditutup.")

    def _dispatch(self, line):
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]
        handlers = {
            "help": self._cmd_help,
            "states": self._cmd_states,
            "set": self._cmd_set,
            "emergency": self._cmd_emergency,
            "resume": self._cmd_resume,
            "force-next": self._cmd_force_next,
            "simulate": self._cmd_simulate,
            "cycle": self._cmd_cycle,
            "config": self._cmd_config,
            "quit": self._cmd_quit,
        }
        handler = handlers.get(cmd)
        if handler is None:
            print(f"[CLI][WARN] Perintah tidak dikenal: '{cmd}'. Ketik 'help'.")
            return
        handler(args)

    def _require_controller(self):
        if self.controller is None:
            print("[CLI][WARN] controller belum aktif (konfigurasi belum valid).")
            return None
        if not self.controller.serial_connected:
            print("[CLI][WARN] serial tidak terhubung.")
            return None
        return self.controller

    # --- perintah ---

    def _cmd_help(self, args):
        print(HELP_TEXT)

    def _cmd_states(self, args):
        print(f"{'State':<22} {'Hex':<5} {'Lajur Hijau'}")
        print("-" * 40)
        for state in GREEN_STATES:
            lanes = ", ".join(str(l) for l in STATE_LANES[state])
            print(f"{state.name:<22} 0x{state.value:02X}   {lanes}")
        print(f"({len(GREEN_STATES)} state hijau; state transisi: "
              f"YELLOW_TRANSITION 0x20, ALL_RED_CLEARANCE 0x21, "
              f"FLASHING_YELLOW 0x22, EMERGENCY 0x23)")

    def _resolve_state(self, target: str):
        t = target.strip()
        if not t:
            return None
        # 1. nama state (dengan atau tanpa awalan STATE_)
        for candidate in (t.upper(), "STATE_" + t.upper()):
            try:
                return TrafficState[candidate]
            except KeyError:
                continue
        # 2. daftar lajur: "1,5" / "1, 5"
        if "," in t:
            pieces = [p.strip() for p in t.split(",")]
            if not all(p.isdigit() for p in pieces):
                return None
            lanes = tuple(sorted(int(p) for p in pieces))
            return LANES_STATE.get(lanes)
        # 3. heksadesimal eksplisit
        if t[:2].lower() == "0x":
            try:
                return TrafficState(int(t, 16))
            except ValueError:
                return None
        # 4. token tunggal
        if all(c in "0123456789abcdefABCDEF" for c in t):
            if any(c in "abcdefABCDEF" for c in t):
                # heksadesimal tanpa awalan, mis. 1C
                try:
                    return TrafficState(int(t, 16))
                except ValueError:
                    return None
            # desimal -> satu lajur, mis. 3
            return LANES_STATE.get((int(t),))
        return None

    def _cmd_set(self, args):
        if not args:
            print("[CLI][WARN] usage: set <target>  (mis. set OPPOSING_1_5_GREEN / set 0x1C / set 1,5)")
            return
        controller = self._require_controller()
        if controller is None:
            return
        state = self._resolve_state(" ".join(args))
        if state is None:
            print(f"[CLI][WARN] Target '{' '.join(args)}' tidak dikenal. "
                  f"Gunakan nama state, hex (0x..), atau lajur (1-8, kombinasi koma).")
            return
        controller.send_manual_state(state)

    def _cmd_emergency(self, args):
        controller = self._require_controller()
        if controller is not None:
            controller.send_emergency()

    def _cmd_resume(self, args):
        controller = self._require_controller()
        if controller is not None:
            controller.send_resume()

    def _cmd_force_next(self, args):
        controller = self._require_controller()
        if controller is not None:
            controller.send_force_next()

    def _cmd_simulate(self, args):
        if not args or args[0].lower() not in ("on", "off"):
            print("[CLI][WARN] usage: simulate on|off")
            return
        controller = self._require_controller()
        if controller is None:
            return
        util = UtilsCmd.UTILS_SIMULATE_ON if args[0].lower() == "on" \
            else UtilsCmd.UTILS_SIMULATE_OFF
        controller.send_utils(util)

    def _cmd_cycle(self, args):
        with payload_lock:
            data = bytes(prepared_cycle_payload)
        if len(data) < 1:
            print("[CLI] Belum ada siklus yang disiapkan.")
            return
        count = data[0]
        print(f"[CLI] Siklus: {count} fase")
        offset = 1
        for i in range(count):
            if offset + 3 > len(data):
                print("[CLI][WARN] Payload siklus terpotong.")
                return
            phase_id = data[offset]
            duration = struct.unpack(">H", data[offset+1:offset+3])[0]
            try:
                name = TrafficState(phase_id).name
            except ValueError:
                name = f"UNKNOWN_0x{phase_id:02X}"
            print(f"  {i+1}. {name} — {duration} detik")
            offset += 3

    def _cmd_config(self, args):
        if args and args[0].lower() == "get":
            args = args[1:]
        if not args:
            self.config.print_all_config()
            return
        if args[0].lower() == "set":
            self._cmd_config_set(args[1:])
            return
        if args[0].lower() == "init":
            self._cmd_config_init(args[1:])
            return
        print("[CLI][WARN] usage: config [get|set <key> <nilai>|init]")

    def _cmd_config_set(self, args):
        if len(args) < 2:
            print("[CLI][WARN] usage: config set <key> <nilai-json>")
            print(f"       kunci yang dapat diubah: {sorted(SETTABLE_KEYS)}")
            return
        key = args[0]
        if key not in SETTABLE_KEYS:
            print(f"[CLI][WARN] Kunci '{key}' tidak dapat diubah. "
                  f"Kunci valid: {sorted(SETTABLE_KEYS)}")
            return
        raw_value = " ".join(args[1:])
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value
        ok, errors = self.config.update_config({key: value})
        if ok:
            print(f"[CLI] Konfigurasi diperbarui: {key} = {value}")
        else:
            print(f"[CLI][WARN] Perubahan ditolak (konfigurasi tidak valid):")
            for err in errors:
                print(f"  - {err}")

    def _cmd_config_init(self, args):
        if os.path.exists(CONFIG_FILE):
            print("[CLI][WARN] data/config.json sudah ada — inisialisasi dibatalkan.")
            return
        if not os.path.exists(EXAMPLE_CONFIG_FILE):
            print(f"[CLI][ERROR] File contoh tidak ditemukan: {EXAMPLE_CONFIG_FILE}")
            return
        shutil.copyfile(EXAMPLE_CONFIG_FILE, CONFIG_FILE)
        print(f"[CLI] data/config.json dibuat dari data/config_example.json.")
        if self.config.reload_if_changed():
            print("[CLI] Konfigurasi baru sudah valid dan aktif.")
        else:
            print("[CLI][WARN] Konfigurasi baru masih tidak valid:")
            for err in self.config.errors:
                print(f"  - {err}")

    def _cmd_quit(self, args):
        print("[CLI] Perintah quit diterima. Program berhenti...")
        self._running = False
        if self.controller is not None:
            self.controller.stop()
