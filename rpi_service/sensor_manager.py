"""
Sensor manager for Arduino Uno R4 communication via USB Serial.
Reads pH, temperature, distance, pressure, humidity at 200ms intervals.
Checksum validation, heartbeat monitoring, circular buffer.
"""
import json
import time
import logging
from threading import Thread, Lock
from typing import Optional, Callable
from collections import deque

logger = logging.getLogger("sensor_manager")


class ChecksumError(ValueError):
    pass


class SensorManager:
    def __init__(self, port: str = "/dev/ttyACM0", baud: int = 115200):
        self.port = port
        self.baud = baud
        self.ser = None
        self.running = False
        self._callbacks: list[Callable] = []
        self._thread: Optional[Thread] = None
        self._buffer = deque(maxlen=500)
        self._buffer_lock = Lock()
        self._last_heartbeat = 0.0
        self._seq_expected = 0
        self._missed_seq = 0
        self._parse_errors = 0
        self._invalid_checksums = 0

    def connect(self) -> bool:
        try:
            import serial
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
                write_timeout=0.1,
            )
            time.sleep(2)
            self.ser.reset_input_buffer()
            logger.info("Arduino connected: %s @ %d baud", self.port, self.baud)
            self._seq_expected = 0
            self._missed_seq = 0
            self._parse_errors = 0
            return True
        except ImportError:
            logger.warning("pyserial not installed")
            return False
        except Exception as exc:
            logger.warning("Arduino connection failed on %s: %s", self.port, exc)
            return False

    def disconnect(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
            logger.info("Arduino disconnected")

    def on_data(self, callback: Callable):
        self._callbacks.append(callback)

    def start(self):
        self.running = True
        self._thread = Thread(target=self._read_loop, daemon=True, name="sensor_read")
        self._thread.start()

    def stop(self):
        self.running = False
        self.disconnect()

    def _read_loop(self):
        while self.running:
            try:
                if not self.ser or not self.ser.is_open:
                    if not self.connect():
                        time.sleep(5)
                        continue

                line = self.ser.readline()
                if not line:
                    continue

                text = line.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue

                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    self._parse_errors += 1
                    if self._parse_errors % 100 == 0:
                        logger.warning("Parse errors: %d", self._parse_errors)
                    continue

                if self._validate_sensor_data(data):
                    self._last_heartbeat = time.time()
                    with self._buffer_lock:
                        self._buffer.append({**data, "_received_at": self._last_heartbeat})
                    for cb in self._callbacks:
                        try:
                            cb(data)
                        except Exception as exc:
                            logger.error("Sensor callback error: %s", exc)

                time.sleep(0.001)
            except Exception as exc:
                logger.error("Sensor read loop error: %s", exc)
                self.disconnect()
                time.sleep(5)

    def _validate_sensor_data(self, data: dict) -> bool:
        cs = data.get("cs")
        if cs is not None:
            fields = [data.get(k) for k in ("ph", "temp", "dist", "press", "hum", "tip", "seq")]
            if any(f is None for f in fields):
                return False
            calculated = 0
            for i, v in enumerate(fields):
                if isinstance(v, float):
                    calculated ^= int(v * 100) & 0xFF
                else:
                    calculated ^= int(v) & 0xFF
            if calculated != cs:
                self._invalid_checksums += 1
                return False

        seq = data.get("seq")
        if seq is not None and self._seq_expected > 0:
            gap = (seq - self._seq_expected) % 65536
            if gap > 2:
                self._missed_seq += 1
                if self._missed_seq == 1 or self._missed_seq % 50 == 0:
                    logger.warning("Missed %d sensor readings (seq gap: %d)", self._missed_seq, gap)
        if seq is not None:
            self._seq_expected = (seq + 1) % 65536

        return True

    def send_command(self, cmd: dict) -> bool:
        if not self.ser or not self.ser.is_open:
            return False
        try:
            payload = (json.dumps(cmd) + "\n").encode("utf-8")
            self.ser.write(payload)
            logger.debug("Sent command: %s", cmd)
            return True
        except Exception as exc:
            logger.error("Send command error: %s", exc)
            return False

    def calibrate_ph(self):
        return self.send_command({"cmd": "calibrate_ph"})

    def set_led(self, on: bool):
        return self.send_command({"cmd": "set_led", "value": 1 if on else 0})

    def emergency_stop(self):
        return self.send_command({"cmd": "emergency_stop"})

    def get_latest(self) -> Optional[dict]:
        with self._buffer_lock:
            return self._buffer[-1] if self._buffer else None

    def get_window(self, seconds: float = 10.0) -> list:
        cutoff = time.time() - seconds
        with self._buffer_lock:
            return [d for d in self._buffer if d.get("_received_at", 0) > cutoff]

    def get_average(self, key: str, seconds: float = 10.0) -> Optional[float]:
        window = self.get_window(seconds)
        values = [d[key] for d in window if key in d and d[key] is not None]
        if not values:
            return None
        return sum(values) / len(values)

    def get_stats(self) -> dict:
        return {
            "parse_errors": self._parse_errors,
            "invalid_checksums": self._invalid_checksums,
            "missed_sequences": self._missed_seq,
            "buffer_size": len(self._buffer),
            "last_heartbeat_age": time.time() - self._last_heartbeat if self._last_heartbeat else -1,
            "is_connected": self.ser is not None and self.ser.is_open,
        }
