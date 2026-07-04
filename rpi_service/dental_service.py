#!/usr/bin/env python3
"""
Dental AI - Raspberry Pi 5 Service Orchestrator
Architecture temps réel avec thread pinning, watchdog, circuit breaker.
"""
import os
import sys
import json
import time
import base64
import signal
import logging
import threading
import platform
from pathlib import Path
from typing import Optional
from queue import Queue, Empty, Full

try:
    import redis
except ImportError:
    redis = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("dental_service")

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
API_URL = os.environ.get("API_URL", "http://localhost:5000/api")
CAPTURE_DIR = Path(os.environ.get("CAPTURE_DIR", "/captures"))


def pin_thread(core_id: int):
    try:
        import os as _os
        _os.sched_setaffinity(0, {core_id})
    except (AttributeError, PermissionError):
        pass


def set_thread_priority(priority: int):
    try:
        import os as _os
        param = _os.SCHED_FIFO if priority > 50 else _os.SCHED_OTHER
        _os.sched_setscheduler(0, param, _os.sched_param(priority))
    except (AttributeError, PermissionError, OSError):
        pass


class CircuitBreaker:
    def __init__(self, name: str, threshold: int = 5, recovery: float = 30.0):
        self.name = name
        self.threshold = threshold
        self.recovery = recovery
        self.failures = 0
        self.last_failure = 0.0
        self.state = "closed"
        self._lock = threading.Lock()

    def call(self, func, *args, **kwargs):
        with self._lock:
            if self.state == "open":
                if time.time() - self.last_failure > self.recovery:
                    self.state = "half-open"
                    logger.info("Circuit %s → half-open", self.name)
                else:
                    raise RuntimeError(f"Circuit {self.name} open")
        try:
            result = func(*args, **kwargs)
            with self._lock:
                if self.state == "half-open":
                    self.state = "closed"
                    self.failures = 0
                    logger.info("Circuit %s → closed", self.name)
            return result
        except Exception as exc:
            with self._lock:
                self.failures += 1
                self.last_failure = time.time()
                if self.failures >= self.threshold:
                    self.state = "open"
                    logger.warning("Circuit %s → open (%d failures)", self.name, self.failures)
            raise


class DentalService:
    def __init__(self):
        self.redis_client = None
        self.pubsub = None
        if redis:
            try:
                self.redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT,
                                                decode_responses=True, socket_timeout=2)
                self.redis_client.ping()
                logger.info("Redis connected: %s:%s", REDIS_HOST, REDIS_PORT)
            except Exception as exc:
                logger.warning("Redis unavailable: %s. Running without pub/sub.", exc)
                self.redis_client = None
        else:
            logger.warning("redis-py not installed. Running without pub/sub.")
            self.redis_client = None

        if self.redis_client:
            self.pubsub = self.redis_client.pubsub()

        self.running = True
        self.frame_queue: Queue = Queue(maxsize=5)
        self.sensor_buffer: list = []
        self.sensor_buffer_lock = threading.Lock()
        self.last_heartbeat = 0.0
        self.fail_count = 0
        self.camera_breaker = CircuitBreaker("camera", threshold=3, recovery=10)
        self.arduino_breaker = CircuitBreaker("arduino", threshold=5, recovery=30)

    # ── Lifecycle ──

    def start(self):
        logger.info("Starting Dental AI Service | RPi 5")
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        signal.signal(signal.SIGTERM, lambda *_: self.shutdown())
        signal.signal(signal.SIGINT, lambda *_: self.shutdown())

        from camera_manager import CameraManager
        from sensor_manager import SensorManager

        self.cam = CameraManager()
        self.sensor = SensorManager()

        threads = [
            ("sensors",       self._sensor_loop,   3),
            ("commands",      self._command_loop,  1),
            ("publisher",     self._publisher,     1),
            ("watchdog",      self._watchdog,      0),
            ("camera_stream", self._camera_stream, 3),
        ]
        for name, target, core in threads:
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            logger.info("Thread %s started on core %d", name, core)

        logger.info("Dental AI Service ready")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self):
        logger.info("Shutting down Dental AI Service")
        self.running = False
        if hasattr(self, "cam") and self.cam:
            self.cam.release()
        if hasattr(self, "sensor") and self.sensor:
            self.sensor.stop()
        self._publish("dental_events", {"type": "service_stopped", "timestamp": time.time()})
        logger.info("Shutdown complete")

    # ── Redis publish helper ──

    def _publish(self, channel: str, data: dict):
        if self.redis_client:
            try:
                self.redis_client.publish(channel, json.dumps(data, default=str))
            except Exception as exc:
                logger.debug("Redis publish error: %s", exc)

    # ── Camera stream thread (core 3, SCHED_FIFO) ──

    def _camera_stream(self):
        pin_thread(3)
        set_thread_priority(50)
        logger.info("Camera stream thread started (core 3, FIFO)")

        adaptive_resolutions = [(1920, 1080), (1280, 720), (640, 480)]
        res_index = 0
        error_count = 0

        while self.running:
            try:
                self.camera_breaker.call(self.cam.open)
                error_count = 0
                logger.info("Camera opened at %dx%d", *adaptive_resolutions[res_index])
            except RuntimeError:
                logger.error("Camera circuit open. Retrying in 10s.")
                time.sleep(10)
                continue

            while self.running:
                frame = self.cam.read_frame()
                if frame is None:
                    error_count += 1
                    if error_count > 10:
                        logger.warning("Camera lost. Reconnecting...")
                        self.cam.release()
                        time.sleep(2)
                        break
                    time.sleep(0.05)
                    continue
                error_count = 0

                try:
                    self.frame_queue.put_nowait(frame)
                except Full:
                    try:
                        self.frame_queue.get_nowait()
                        self.frame_queue.put_nowait(frame)
                    except (Empty, Full):
                        pass

                quality = self.cam.analyze_quality(frame)
                fps = getattr(self.cam, "_fps_counter", 30)
                self._publish("dental_events", {
                    "type": "frame",
                    "quality_score": quality["overall_score"],
                    "fps": fps,
                    "resolution": f"{adaptive_resolutions[res_index][0]}x{adaptive_resolutions[res_index][1]}",
                    "timestamp": time.time(),
                })

                cpu_load = self._get_cpu_load()
                if cpu_load > 80 and res_index < len(adaptive_resolutions) - 1:
                    res_index += 1
                    self.cam.resolution = adaptive_resolutions[res_index]
                    logger.info("CPU high (%d%%), reducing resolution to %dx%d",
                                cpu_load, *adaptive_resolutions[res_index])
                elif cpu_load < 50 and res_index > 0:
                    res_index -= 1
                    self.cam.resolution = adaptive_resolutions[res_index]
                    logger.info("CPU normal (%d%%), restoring resolution to %dx%d",
                                cpu_load, *adaptive_resolutions[res_index])

                time.sleep(0.033)

            self.cam.release()
            time.sleep(2)

    # ── Sensor loop thread (core 3) ──

    def _sensor_loop(self):
        pin_thread(3)
        set_thread_priority(40)
        logger.info("Sensor loop thread started (core 3)")

        while self.running:
            if not self.sensor.connect():
                time.sleep(5)
                continue

            self.sensor.start()
            self.sensor.on_data(self._on_sensor_data)

            while self.running:
                time.sleep(0.5)
                # Vérifier heartbeat
                if time.time() - self.last_heartbeat > 5:
                    self.fail_count += 1
                    if self.fail_count > 3:
                        logger.error("Arduino heartbeat lost. Reconnecting...")
                        self.sensor.stop()
                        self._publish("dental_events", {
                            "type": "arduino_disconnected",
                            "timestamp": time.time(),
                        })
                        break
                else:
                    self.fail_count = 0
                time.sleep(2)

            self.sensor.stop()
            time.sleep(5)

    def _on_sensor_data(self, data: dict):
        self.last_heartbeat = time.time()

        if not data or not self._validate_checksum(data):
            logger.debug("Sensor data invalid (checksum)")
            return

        with self.sensor_buffer_lock:
            self.sensor_buffer.append({**data, "_received_at": time.time()})
            if len(self.sensor_buffer) > 1000:
                self.sensor_buffer = self.sensor_buffer[-500:]
            cutoff = time.time() - 30
            self.sensor_buffer = [d for d in self.sensor_buffer if d["_received_at"] > cutoff]

        self._publish("sensor_data", {
            "ph": data.get("ph"),
            "temperature": data.get("temp"),
            "distance": data.get("dist") or data.get("distance"),
            "pressure": data.get("press") or data.get("pressure"),
            "humidity": data.get("hum") or data.get("humidity"),
            "tip_detected": bool(data.get("tip", 1)),
            "recorded_at": time.time(),
        })

    def _validate_checksum(self, data: dict) -> bool:
        fields = [data.get(k) for k in ("ph", "temp", "dist", "press", "hum", "tip", "seq", "cs")]
        if any(f is None for f in fields[:7]):
            return True
        calculated = 0
        for i in range(7):
            v = fields[i]
            if isinstance(v, float):
                calculated ^= int(v * 100) & 0xFF
            else:
                calculated ^= int(v) & 0xFF
        return calculated == data.get("cs", calculated)

    # ── Command loop thread (core 1) ──

    def _command_loop(self):
        pin_thread(1)
        logger.info("Command loop thread started (core 1)")
        if not self.pubsub:
            logger.warning("No Redis pub/sub; command loop disabled")
            return

        try:
            self.pubsub.subscribe("dental_commands")
            for message in self.pubsub.listen():
                if not self.running:
                    break
                if message["type"] != "message":
                    continue
                try:
                    cmd = json.loads(message["data"])
                    self._handle_command(cmd)
                except json.JSONDecodeError:
                    pass
        except Exception as exc:
            logger.error("Command loop error: %s", exc)

    def _handle_command(self, cmd: dict):
        action = cmd.get("action")
        logger.info("Command received: %s", action)
        if action == "capture":
            self._do_capture(cmd.get("diagnostic_id"))
        elif action == "burst_capture":
            self._do_burst_capture(cmd.get("diagnostic_id"), cmd.get("count", 5))
        elif action == "status":
            self._publish_status()
        elif action == "set_resolution":
            self.cam.resolution = (cmd.get("width", 1920), cmd.get("height", 1080))
        elif action == "emergency_stop":
            self.shutdown()
        else:
            logger.warning("Unknown command: %s", action)

    def _do_capture(self, diagnostic_id=None):
        try:
            frame = self.frame_queue.get(timeout=2)
        except Empty:
            logger.error("No frame available for capture")
            self._publish("dental_events", {"type": "error", "code": "no_frame"})
            return

        enhanced = self.cam.enhance(frame)
        ts = int(time.time() * 1000)
        filename = f"diag_{diagnostic_id or 'manual'}_{ts}.jpg"
        filepath = str(CAPTURE_DIR / filename)

        import cv2
        cv2.imwrite(filepath, enhanced, [cv2.IMWRITE_JPEG_QUALITY, 95])

        with open(filepath, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        self._publish("dental_events", {
            "type": "capture_result",
            "image": encoded,
            "filename": filename,
            "diagnostic_id": diagnostic_id,
            "width": enhanced.shape[1],
            "height": enhanced.shape[0],
            "timestamp": time.time(),
        })
        logger.info("Captured: %s", filename)

    def _do_burst_capture(self, diagnostic_id=None, count=5):
        frames = self.cam.capture_burst(count)
        if not frames:
            logger.error("Burst capture returned no frames")
            return

        best = self.cam.select_best(frames)
        if best is not None:
            self._do_capture(diagnostic_id)

    # ── Publisher thread (core 1) ──

    def _publisher(self):
        pin_thread(1)
        logger.info("Publisher thread started (core 1)")
        while self.running:
            self._publish_status()
            time.sleep(5)

    # ── Watchdog thread (core 0) ──

    def _watchdog(self):
        pin_thread(0)
        logger.info("Watchdog thread started (core 0)")
        while self.running:
            cpu = self._get_cpu_load()
            temp = self._get_cpu_temp()
            mem = self._get_mem_usage()
            if cpu is not None:
                self._publish("dental_events", {
                    "type": "system_health",
                    "cpu_percent": cpu,
                    "temp_celsius": temp,
                    "mem_percent": mem,
                    "timestamp": time.time(),
                })
                if cpu > 90:
                    logger.warning("CPU overload: %d%%", cpu)
                if temp and temp > 80:
                    logger.warning("CPU temperature high: %.1f°C", temp)
            time.sleep(5)

    def _publish_status(self):
        import subprocess, glob
        rpi_model = "Raspberry Pi 5"
        try:
            with open("/proc/device-tree/model", "r") as f:
                rpi_model = f.read().strip("\x00")
        except FileNotFoundError:
            pass

        camera_status = "connected"
        try:
            result = subprocess.run(["v4l2-ctl", "--list-devices"],
                                    capture_output=True, text=True, timeout=3)
            if "video" not in result.stdout.lower():
                camera_status = "disconnected"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            camera_status = "checking"

        arduino_ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
        arduino_status = "connected" if arduino_ports else "disconnected"

        status = {
            "type": "hardware_status",
            "raspberry_pi": {
                "status": "connected",
                "message": rpi_model,
                "details": {
                    "hostname": os.uname().nodename,
                    "kernel": os.uname().release,
                    "arch": platform.machine(),
                    "temp": self._get_cpu_temp(),
                    "cpu_load": self._get_cpu_load(),
                },
            },
            "camera": {"status": camera_status, "message": "Caméra AZDENT" if camera_status == "connected" else "Non détectée"},
            "arduino": {"status": arduino_status, "message": f"Port: {arduino_ports[0]}" if arduino_ports else "Non détecté"},
            "sensors": {
                "ph": {"status": "connected" if arduino_status == "connected" else "disconnected", "message": "pH salivaire"},
                "temperature": {"status": "connected" if arduino_status == "connected" else "disconnected", "message": "Température buccale"},
                "distance": {"status": "connected" if arduino_status == "connected" else "disconnected", "message": "Distance caméra-dent"},
                "pressure": {"status": "connected" if arduino_status == "connected" else "disconnected", "message": "Pression"},
                "humidity": {"status": "connected" if arduino_status == "connected" else "disconnected", "message": "Humidité salivaire"},
            },
            "allReady": camera_status == "connected" and arduino_status == "connected",
            "overall_status": "connected" if (camera_status == "connected" and arduino_status == "connected") else "disconnected",
            "timestamp": time.time(),
        }
        if self.redis_client:
            try:
                self.redis_client.set("hardware_status", json.dumps(status))
            except Exception:
                pass

    # ── System metrics ──

    def _get_cpu_temp(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return round(int(f.read().strip()) / 1000, 1)
        except FileNotFoundError:
            return None

    def _get_cpu_load(self):
        try:
            import psutil
            return int(psutil.cpu_percent(interval=0.5))
        except ImportError:
            try:
                with open("/proc/loadavg") as f:
                    parts = f.read().strip().split()
                    return round(float(parts[0]) / os.cpu_count() * 100, 1)
            except (FileNotFoundError, ZeroDivisionError):
                return None

    def _get_mem_usage(self):
        try:
            import psutil
            return int(psutil.virtual_memory().percent)
        except ImportError:
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if "MemAvailable" in line:
                            avail = int(line.split()[1])
                        elif "MemTotal" in line:
                            total = int(line.split()[1])
                    return round((1 - avail / total) * 100, 1)
            except (FileNotFoundError, NameError):
                return None


if __name__ == "__main__":
    service = DentalService()
    service.start()
