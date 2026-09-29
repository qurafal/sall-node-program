import time
from config import ConfigManager
from fsm_engine import FSMEngine  # Import FSM

def emergency_mode():
    print("[CRITICAL] Memasuki MODE DARURAT: Lampu Kuning Berkedip.")
    while True:
        time.sleep(1)

def main():
    print("Sistem Traffic Light Memulai Booting...")
    
    try:
        config_mgr = ConfigManager()
        config_mgr.print_all_config()
        
        if config_mgr.role == "master":
            print(f"[NODE {config_mgr.identity}] Menjalankan layanan MASTER...")
            
            # Inisialisasi dan jalankan FSM Engine
            fsm = FSMEngine(config_mgr)
            fsm.run() # Program akan tertahan di dalam loop run() ini
            
        elif config_mgr.role == "slave":
            print(f"[NODE {config_mgr.identity}] Menjalankan layanan SLAVE...")
            # TODO: Loop untuk LoRa TX (kirim densitas) dan RX (dengar perintah Master)
            while True:
                time.sleep(1)
            
        else:
            raise ValueError(f"Role tidak dikenali: {config_mgr.role}")
            
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"\n[FATAL ERROR] {e}")
        emergency_mode()
        
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Sistem dihentikan dengan aman oleh pengguna.")

if __name__ == "__main__":
    main()