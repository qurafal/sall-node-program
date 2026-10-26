import time
from cli import CliInterface
from config import ConfigManager
from master_controller import MasterController


def main():
    print("Sistem Traffic Light Memulai Booting...")

    config_mgr = ConfigManager()

    # CLI aktif sejak awal agar konfigurasi bisa diperbaiki
    # dari luar selama menunggu konfigurasi valid.
    cli = CliInterface(config_mgr)
    cli.start()

    while not config_mgr.is_valid:
        if not cli.is_running:
            print("[SHUTDOWN] Sistem dihentikan dari CLI.")
            return
        print("[CONFIG] Konfigurasi tidak valid, menunggu perbaikan "
              "(CLI: 'config set' / 'config init' atau edit file)...")
        for err in config_mgr.errors:
            print(f"  - {err}")
        time.sleep(2)
        config_mgr.reload_if_changed()

    config_mgr.print_all_config()

    # --- Role branching lama (direservasi untuk ekstensi multi-master di masa depan) ---
    # try:
    #     if config_mgr.role == "master":
    #         print(f"[NODE {config_mgr.identity}] Menjalankan layanan MASTER...")
    #
    #         # Inisialisasi dan jalankan FSM Engine
    #         fsm = FSMEngine(config_mgr)
    #         fsm.run() # Program akan tertahan di dalam loop run() ini
    #
    #     elif config_mgr.role == "slave":
    #         print(f"[NODE {config_mgr.identity}] Menjalankan layanan SLAVE...")
    #         # TODO: Loop untuk LoRa TX (kirim densitas) dan RX (dengar perintah Master)
    #         while True:
    #             time.sleep(1)
    #
    #     else:
    #         raise ValueError(f"Role tidak dikenali: {config_mgr.role}")
    #
    # except (FileNotFoundError, KeyError, ValueError) as e:
    #     print(f"\n[FATAL ERROR] {e}")
    #     emergency_mode()

    controller = MasterController(config_mgr)
    cli.attach(controller)
    try:
        controller.run()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Sistem dihentikan oleh pengguna.")
    finally:
        controller.shutdown()

if __name__ == "__main__":
    main()
