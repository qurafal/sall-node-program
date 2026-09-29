import time
import random
from config import ConfigManager

class FSMEngine:
    def __init__(self, config_mgr: ConfigManager):
        self.config = config_mgr
        
        # Menggabungkan Node Master dan daftar Slave untuk total lajur
        self.nodes = [self.config.identity] + self.config.assigned_slave
        self.nodes.sort()
        self.num_nodes = len(self.nodes)
        
        # Konstanta jeda waktu
        self.YELLOW_DURATION = 2.0
        self.ALL_RED_DURATION = 1.5

    #DUMMY data density
    def get_dummy_densities(self) -> dict:
       
        return {node: round(random.uniform(0.0, 1.0), 2) for node in self.nodes}

    def calculate_green_time(self, density: float) -> float:
        
        extra = density * self.config.max_extra_green
        return float(self.config.min_green + extra)

    def print_simulation_matrix(self, duration: float, active_node: int, current_state: str):
        """Mencetak format bersih simulasi lampu tanpa teks gangguan."""
        status_list = []
        for node in self.nodes:
            if current_state == "ALL_RED":
                node_state = "RED"
            elif node == active_node:
                node_state = current_state
            else:
                node_state = "RED"
                
            status_list.append(f"Node {node}: {node_state}")
            
        print(f"[{duration:4.1f}s] -> {' | '.join(status_list)}")

    def run(self):
        print("\nStarting execution on Master terminal...")
        cycle_count = 1
        
        while True:
            # ========================================================
            # FASE 1: GENERATE FSM (1x Per Siklus)
            # ========================================================
            print(f"\n{'='*45}")
            print(f"⚙️  GENERATE FSM SIKLUS {cycle_count}")
            print(f"{'='*45}")
            
            current_densities = self.get_dummy_densities()
            cycle_schedule = []
            
            for node in self.nodes:
                density = current_densities[node]
                green_time = self.calculate_green_time(density)
                
                # Simpan hasil kalkulasi ke dalam jadwal siklus
                cycle_schedule.append({
                    "node": node,
                    "green_time": green_time
                })
                print(f"-> Kalkulasi Node {node} : Kepadatan {density:.2f} = {green_time:4.1f}s Hijau")
                
            # TODO: Di titik ini, Master akan melakukan Broadcast JSON cycle_schedule ke semua Slave via LoRa
            
            # ========================================================
            # FASE 2: EKSEKUSI SIMULASI (Clean Output)
            # ========================================================
            print("\n▶ SIMULASI BERJALAN:")
            for step in cycle_schedule:
                current_node = step["node"]
                green_time = step["green_time"]
                
                # 1. State Hijau
                self.print_simulation_matrix(green_time, current_node, "GREEN")
                time.sleep(0.5) # Dipercepat untuk pengujian terminal
                
                # 2. State Kuning
                self.print_simulation_matrix(self.YELLOW_DURATION, current_node, "YELLOW")
                time.sleep(0.5)
                
                # 3. State Merah Semua (Jeda Aman)
                self.print_simulation_matrix(self.ALL_RED_DURATION, None, "ALL_RED")
                time.sleep(0.5)
                
            cycle_count += 1