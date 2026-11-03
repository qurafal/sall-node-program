import time
from config import ConfigManager
from master_controller import MasterController


def main():
    print("Sistem Traffic Light Memulai Booting...")

    config_mgr = ConfigManager()

    while not config_mgr.is_valid:
        print("[CONFIG] Konfigurasi tidak valid, menunggu perbaikan (edit file)...")
        for err in config_mgr.errors:
            print(f"  - {err}")
        time.sleep(2)
        config_mgr.reload_if_changed()

    config_mgr.print_all_config()

    controller = MasterController(config_mgr)
    try:
        controller.run()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Sistem dihentikan oleh pengguna.")
    finally:
        controller.shutdown()

if __name__ == "__main__":
    main()
