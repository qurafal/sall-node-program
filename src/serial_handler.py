import threading

import serial

from protocol import HEADER_BYTES, build_frame, parse_stream

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
