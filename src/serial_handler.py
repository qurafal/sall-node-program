import threading
import time

import serial

from protocol import HEADER_BYTES, build_frame, parse_stream

# ESP32 reset (DTR/RTS) saat port dibuka -> beri waktu boot sebelum I/O pertama.
OPEN_SETTLE_SEC = 2.0


class SerialHandler:
    def __init__(self, config_mgr):
        self.config = config_mgr
        self._buffer = bytearray()
        self._write_lock = threading.Lock()
        self.serial = None
        self._open()

    def _open(self):
        self.serial = serial.Serial(
            port=self.config.serial_port,
            baudrate=self.config.baud_rate,
            timeout=0,
        )
        # Membuka port men-toggle DTR/RTS -> ESP32 reboot. Tunggu boot selesai
        # (jangan kirim CMD_INIT ke bootloader), lalu buang boot log & sisa byte.
        time.sleep(OPEN_SETTLE_SEC)
        try:
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
        except Exception:
            pass

    def clear_rx_buffer(self):
        """Buang byte serial yang belum terbaca + buffer frame internal.

        Dipanggil tepat sebelum command baru dikirim supaya ACK basi
        (mis. ACK_TRANSITION / ACK_REJECTED dari siklus sebelumnya) tidak
        pernah dipenuhi oleh _wait_ack() untuk command berikutnya.
        """
        try:
            if self.serial is not None and self.serial.is_open:
                self.serial.read_all()
        except Exception:
            pass
        self._buffer.clear()

    def is_open(self) -> bool:
        return self.serial is not None and self.serial.is_open

    def read_frames(self):
        data = self.serial.read_all()
        if data:
            self._buffer.extend(data)
        frames = parse_stream(self._buffer)
        self._compact_buffer()
        return frames

    def _compact_buffer(self):
        buf = self._buffer
        n = len(buf)
        i = 0
        while i <= n - 5:
            if buf[i:i+3] == HEADER_BYTES:
                length = buf[i+3]
                if length == 0:
                    # LEN == 0 adalah sampah; resync seperti parse_stream
                    i += 1
                    continue
                if n >= i + 4 + length:
                    i += 4 + length
                    continue
                del buf[:i]
                return
            i += 1
        del buf[:i]

    def send_frame(self, cmd: int, payload: bytes = b''):
        frame = build_frame(cmd, payload)
        with self._write_lock:
            self.serial.write(frame)
            self.serial.flush()

    def close(self):
        if self.serial is not None and self.serial.is_open:
            self.serial.close()
        self.serial = None
        self._buffer.clear()
