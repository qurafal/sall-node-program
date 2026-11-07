import time
from config import ConfigManager
from master_controller import MasterController
from yolo_worker import YoLoWorker


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

    yolo = YoLoWorker(config_mgr)
    yolo.start()

    controller = MasterController(config_mgr, yolo_worker=yolo)
    try:
        controller.run()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Sistem dihentikan oleh pengguna.")
    finally:
        yolo.stop()
        controller.shutdown()

if __name__ == "__main__":
    main()
