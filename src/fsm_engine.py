import time
import random
from config import ConfigManager

class FSMEngine:
    def __init__(self, config_mgr: ConfigManager):
            self.config = config_mgr
            
            # 1. Menggabungkan Node Master dan daftar Slave untuk total lajur
            self.nodes = [self.config.identity] + self.config.assigned_slaves
            self.nodes.sort()
            self.num_nodes = len(self.nodes)
            
            # 2. Parameter Fisika & Simulasi
            self.SIMULATION_SPEED = 0.1  # Pengali waktu (0.5 = 2x lebih cepat di terminal)
            self.max_green = self.config.min_green + self.config.max_extra_green
            self.all_red_duration = self.config.conflict_zone_meter / self.config.vehicle_speed_ms
            
            # 3. Kamus pembobotan PCU (Passenger Car Unit)
            self.PCU_WEIGHTS = {
                "motor": 0.3,
                "mobil": 1.0,
                "truk": 2.0
            }

            print(f"[FSM ENGINE] Diinisialisasi untuk {self.num_nodes} Node: {self.nodes}")
            print(f"[PHYSICS] All-Red Clearing Time: {self.all_red_duration:.1f} detik")

   
    def get_dummy_vehicles(self) -> dict:
        # Dummy data
        
        yolo_data = {}
        for node in self.nodes:
            # Randomisasi ringan untuk demonstrasi Phase Skipping (kadang 0)
            if random.random() > 0.8:
                yolo_data[node] = {"motor": 0, "mobil": 0, "truk": 0}
            else:
                #Pembobotan PCU 
                yolo_data[node] = {
                    "motor": random.randint(0, 30),
                    "mobil": random.randint(0, 15),
                    "truk": random.randint(0, 3)
                }
        return yolo_data

    def generate(self) -> tuple:
        
        yolo_data = self.get_dummy_vehicles()
        total_pcu_load = 0
        cycle_schedule = []
        node_pcu_loads = {}
        
        
        STARTUP_LOST_TIME = 2.0   # Detik (Waktu reaksi pengemudi di barisan pertama)
        SATURATION_HEADWAY = 2.1  # Detik (Waktu rata-rata 1 PCU melintasi garis stop)
        
        print(f"\n[YOLO DATA RAW] {yolo_data}")
        
        # Tahap A: Hitung Total Beban PCU dengan pembobotan
        for node, vehicles in yolo_data.items():
            pcu = (vehicles["motor"] * self.PCU_WEIGHTS["motor"]) + \
                  (vehicles["mobil"] * self.PCU_WEIGHTS["mobil"]) + \
                  (vehicles["truk"] * self.PCU_WEIGHTS["truk"])
            
            node_pcu_loads[node] = pcu
            total_pcu_load += pcu

        # Tahap B: Kalkulasi Durasi Hijau Aktual (Queue-Clearing)
        if total_pcu_load == 0:
            print("⚠️ Semua arah KOSONG. Failsafe: Berpindah ke Mode Malam (Flashing Yellow).")
        else:
            for node, pcu in node_pcu_loads.items():
                if pcu == 0:
                    print(f"⏩ Node {node} terdeteksi KOSONG : PHASE SKIPPED")
                    continue
                
                
                jml_lajur = self.config.lanes.get(node)
                
                
                # Waktu Reaksi Awal + (Beban per lajur * Waktu stabil per PCU)
                raw_gts = STARTUP_LOST_TIME + ((pcu / jml_lajur) * SATURATION_HEADWAY)
                
                # Batasi dengan min_green dan max_green
                final_green = max(self.config.min_green, min(self.max_green, round(raw_gts)))
                
                cycle_schedule.append({"node": node, "green_time": final_green})
                
                # Log Pembuktian Kalkulasi di terminal Master
                raw_count = sum(yolo_data[node].values())
                print(f"✅ Kalkulasi Node {node} ({raw_count} Kend, Beban {pcu:.1f} PCU, {jml_lajur} Lajur)")
                print(f"   -> {STARTUP_LOST_TIME}s (Reaksi) + {((pcu/jml_lajur)*SATURATION_HEADWAY):.1f}s (Jalan) = Butuh {raw_gts:.1f}s | Diputuskan: {final_green}s")
                
        return cycle_schedule, total_pcu_load
    def broadcast_state(self, active_node, current_state, duration):
       #nanti buat ngirim ke semua node
        status_list = []
        for node in self.nodes:
            if current_state == "FLASHING_YELLOW":
                node_state = "FLASH_YLW"
            elif current_state == "ALL_RED":
                node_state = "RED"
            elif node == active_node:
                node_state = current_state
            else:
                node_state = "RED"
            status_list.append(f"N{node}:{node_state}")
            
        # Menambahkan durasi di bagian depan matriks
        print(f"[{duration:4.1f}s] 📡 [TX] -> {' | '.join(status_list)}")

    def run(self):
        print("\nStarting execution on Master terminal...")
        cycle_count = 1
        
        while True:
            # 1. LOAD CONFIGURATION
            self.config = ConfigManager() 
            
            if self.config.role != "master":
                print(f"Tiang {self.config.identity} adalah Slave. Menunggu broadcast dari Master...")
                time.sleep(2)
                continue

            print(f"\n{'='*55}")
            print(f"⚙️  GENERATE FSM SIKLUS {cycle_count}")
            print(f"{'='*55}")
            
            # 2. KALKULASI MATEMATIS
            cycle_schedule, total_pcu_load = self.generate()

            # 3. PENYIARAN METRONOM (Eksekusi State Aktual)
            print("\n📡 BROADCAST REAL-TIME BERJALAN:")
            
            if total_pcu_load == 0:
                # Eksekusi Mode Malam (Flashing Yellow)
                duration_flash = 5.0
                self.broadcast_state(None, "FLASHING_YELLOW", duration_flash)
                time.sleep(duration_flash * self.SIMULATION_SPEED) 
            else:
                for step in cycle_schedule:
                    current_node = step["node"]
                    g_time = step["green_time"]
                    
                    print(f"\n--- Mengaktifkan Tiang {current_node} ---")
                    
                    # State: HIJAU
                    self.broadcast_state(current_node, "GREEN", float(g_time))
                    time.sleep(g_time * self.SIMULATION_SPEED)
                    
                    # State: KUNING
                    self.broadcast_state(current_node, "YELLOW", float(self.config.yellow_duration))
                    time.sleep(self.config.yellow_duration * self.SIMULATION_SPEED)
                    
                    # State: ALL-RED
                    self.broadcast_state(None, "ALL_RED", float(self.all_red_duration))
                    time.sleep(self.all_red_duration * self.SIMULATION_SPEED)
                    
            cycle_count += 1