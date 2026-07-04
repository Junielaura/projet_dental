"""
Camera manager for AZDENT intra-oral USB HD camera.
OpenCV capture with adaptive resolution, burst mode, quality analysis.
"""
import cv2
import numpy as np
import time
import logging
from typing import Optional, Tuple

logger = logging.getLogger("camera_manager")


class CameraManager:
    def __init__(self, device: int = 0, resolution: Tuple[int, int] = (1920, 1080)):
        self.device = device
        self.resolution = resolution
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_open = False
        self._fps_counter = 30
        self._last_frame_time = 0.0
        self._error_count = 0

    def open(self) -> bool:
        try:
            if self.cap:
                self.cap.release()
            self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            if not self.cap.isOpened():
                self.cap = cv2.VideoCapture(self.device)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.is_open = self.cap.isOpened()
            if self.is_open:
                actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
                logger.info("Camera opened: %s %dx%d @ %.1ffps",
                            "/dev/video%d" % self.device, actual_w, actual_h, actual_fps)
                self.resolution = (actual_w or self.resolution[0], actual_h or self.resolution[1])
                self._error_count = 0
            return self.is_open
        except Exception as exc:
            logger.error("Failed to open camera: %s", exc)
            return False

    def read_frame(self) -> Optional[np.ndarray]:
        if not self.is_open or self.cap is None:
            return None
        try:
            ret, frame = self.cap.read()
            if ret:
                now = time.time()
                dt = now - self._last_frame_time
                if dt > 0:
                    self._fps_counter = int(0.9 * self._fps_counter + 0.1 / dt)
                self._last_frame_time = now
                self._error_count = 0
                return frame
            else:
                self._error_count += 1
                if self._error_count > 10:
                    logger.warning("Camera read failed %d times", self._error_count)
                return None
        except Exception as exc:
            logger.error("Camera read error: %s", exc)
            return None

    def analyze_quality(self, frame: np.ndarray) -> dict:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = frame.shape[:2]

        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        blur = min(100, max(0, (laplacian_var / 500) * 100))

        mean_brightness = np.mean(gray)
        brightness = min(100, max(0, 100 - abs(128 - mean_brightness) * 0.78))

        contrast = min(100, max(0, (np.std(gray) / 64) * 100))

        noise_val = np.std(gray[::2, ::2])
        noise = min(100, max(0, 100 - (noise_val / 20) * 100))

        overexposed = np.sum(gray > 240) / (h * w) * 100
        glare = min(100, max(0, 100 - overexposed * 5))

        overall = round(
            blur * 0.20 + brightness * 0.20 + contrast * 0.20 +
            noise * 0.15 + glare * 0.25, 1
        )

        return {
            "blur": round(blur, 1),
            "brightness": round(brightness, 1),
            "contrast": round(contrast, 1),
            "noise": round(noise, 1),
            "glare": round(glare, 1),
            "overall_score": overall,
        }

    def enhance(self, frame: np.ndarray) -> np.ndarray:
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        denoised = cv2.fastNlMeansDenoisingColored(enhanced, None, 10, 10, 7, 21)
        return denoised

    def capture_burst(self, count: int = 5) -> list:
        frames = []
        for _ in range(count):
            frame = self.read_frame()
            if frame is not None:
                frames.append(frame)
            time.sleep(0.05)
        if frames:
            frame_h, frame_w = frames[0].shape[:2]
            logger.info("Burst captured %d/%d frames at %dx%d", len(frames), count, frame_w, frame_h)
        else:
            logger.warning("Burst capture returned 0 frames")
        return frames

    def select_best(self, frames: list) -> Optional[np.ndarray]:
        if not frames:
            return None
        scored = [(self.analyze_quality(f), f) for f in frames]
        best_score, best_frame = max(scored, key=lambda x: x[0]["overall_score"])
        logger.info("Best frame quality: %.1f/100", best_score["overall_score"])
        return best_frame

    def release(self):
        if self.cap:
            self.cap.release()
            self.is_open = False
            logger.info("Camera released")
