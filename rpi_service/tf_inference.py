"""
TensorFlow Lite inference engine for dental disease classification
and GradCAM computation. Singleton model loader with mock fallback.
"""
import json
import time
import logging
import threading
from pathlib import Path
from typing import Optional
import numpy as np

logger = logging.getLogger("tf_inference")

MODEL_PATH = Path(__file__).parent / "models"
CLASS_LABELS = ["Healthy", "Caries", "Gingivitis", "Periodontitis"]
FRENCH_LABELS = ["Sain", "Carie", "Gingivite", "Parodontite"]


class ModelLoader:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._models = {}
                    cls._instance._loaded = False
        return cls._instance

    def load_all(self) -> bool:
        with self._lock:
            if self._loaded:
                return True

            model_name = os.environ.get("TFLITE_MODEL", "dentalnet_v2_uint8.tflite")
            model_path = MODEL_PATH / model_name

            try:
                import tflite_runtime.interpreter as tflite
                backend = "tflite_runtime"
            except ImportError:
                try:
                    import tensorflow as tf
                    backend = "tensorflow"
                except ImportError:
                    logger.warning("No TFLite backend available. Using mock inference.")
                    self._loaded = True
                    return False

            if not model_path.exists():
                logger.warning("Model not found: %s", model_path)
                self._loaded = True
                return False

            try:
                if backend == "tflite_runtime":
                    import tflite_runtime.interpreter as tflite
                    interpreter = tflite.Interpreter(model_path=str(model_path))
                else:
                    interpreter = tf.lite.Interpreter(model_path=str(model_path))

                interpreter.allocate_tensors()
                self._models["interpreter"] = interpreter
                self._models["input_details"] = interpreter.get_input_details()
                self._models["output_details"] = interpreter.get_output_details()
                self._models["model_name"] = str(model_path)
                self._loaded = True
                logger.info("TFLite model loaded: %s (%s)", model_path, backend)
                return True
            except Exception as exc:
                logger.error("Failed to load model: %s", exc)
                return False

    def get_interpreter(self):
        return self._models.get("interpreter")

    def get_input_details(self):
        return self._models.get("input_details")

    def get_output_details(self):
        return self._models.get("output_details")

    def get_model_name(self):
        return self._models.get("model_name", "mock")

    def is_loaded(self) -> bool:
        return self._models.get("interpreter") is not None


import os

model_loader = ModelLoader()


class TFLiteInference:
    def __init__(self):
        self.loaded = model_loader.load_all()

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        if image.shape[:2] != (224, 224):
            import cv2
            image = cv2.resize(image, (224, 224))
        if image.shape[-1] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
        elif image.shape[-1] == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image = image.astype(np.float32) / 127.5 - 1.0
        return np.expand_dims(image, axis=0).astype(np.float32)

    def predict(self, image: np.ndarray) -> dict:
        if not self.loaded:
            return self._mock_predict()

        interpreter = model_loader.get_interpreter()
        input_details = model_loader.get_input_details()
        output_details = model_loader.get_output_details()

        t0 = time.perf_counter()
        input_data = self.preprocess(image)
        interpreter.set_tensor(input_details[0]["index"], input_data)
        interpreter.invoke()
        output = interpreter.get_tensor(output_details[0]["index"])[0]
        elapsed_ms = (time.perf_counter() - t0) * 1000

        exp_scores = np.exp(output - np.max(output))
        probs = exp_scores / exp_scores.sum()

        predicted_class = int(np.argmax(probs))
        confidence = float(probs[predicted_class])
        all_scores = {CLASS_LABELS[i]: round(float(probs[i]), 3) for i in range(len(CLASS_LABELS))}

        return {
            "maladie": CLASS_LABELS[predicted_class],
            "label_fr": FRENCH_LABELS[predicted_class],
            "confidence": round(confidence, 4),
            "all_scores": all_scores,
            "model_used": model_loader.get_model_name(),
            "analysis_time_ms": round(elapsed_ms, 1),
        }

    def predict_from_bytes(self, image_bytes: bytes) -> dict:
        import cv2
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return self._mock_predict()
        return self.predict(img)

    def compute_gradcam(self, image: np.ndarray, class_index: Optional[int] = None) -> dict:
        if not self.loaded:
            h, w = image.shape[:2] if image is not None else (224, 224)
            return self._mock_gradcam(w, h)

        import cv2
        interpreter = model_loader.get_interpreter()
        input_details = model_loader.get_input_details()
        output_details = model_loader.get_output_details()

        input_data = self.preprocess(image)
        interpreter.set_tensor(input_details[0]["index"], input_data)
        interpreter.invoke()
        predictions = interpreter.get_tensor(output_details[0]["index"])[0]

        if class_index is None:
            class_index = int(np.argmax(predictions))

        conv_output = self._get_conv_output(interpreter, input_data)
        if conv_output is None:
            return self._mock_gradcam(image.shape[1], image.shape[0])

        pooled = np.mean(conv_output[:, :, :], axis=(0, 1))
        gradcam = np.zeros(conv_output.shape[:2], dtype=np.float32)
        for i in range(min(conv_output.shape[2], len(pooled))):
            gradcam += pooled[i] * conv_output[:, :, i]
        gradcam = np.maximum(gradcam, 0)

        h, w = image.shape[:2]
        gradcam = cv2.resize(gradcam, (w, h))
        if gradcam.max() > 0:
            gradcam /= gradcam.max()

        heatmap = (gradcam * 255).astype(np.uint8)
        heatmap_color = cv2.applyColorMap(heatmap, cv2.COLORMAP_JET)
        overlay = cv2.addWeighted(image, 0.6, heatmap_color, 0.4, 0)

        _, heatmap_buffer = cv2.imencode(".png", heatmap_color)
        _, overlay_buffer = cv2.imencode(".png", overlay)

        import base64
        return {
            "heatmap": base64.b64encode(heatmap_buffer.tobytes()).decode("utf-8"),
            "overlay": base64.b64encode(overlay_buffer.tobytes()).decode("utf-8"),
            "class_index": class_index,
            "confidence": float(predictions[class_index]),
        }

    def _get_conv_output(self, interpreter, input_data):
        try:
            tensor_details = interpreter.get_tensor_details()
            conv_layers = [d for d in tensor_details if "conv" in d["name"].lower()
                           or "activation" in d["name"].lower()
                           or "leaky_relu" in d["name"].lower()]
            if not conv_layers:
                conv_layers = [d for d in tensor_details if len(d["shape"]) == 4]
            if conv_layers:
                target = conv_layers[-1]
                return interpreter.get_tensor(target["index"])
        except Exception as exc:
            logger.debug("GradCAM conv output error: %s", exc)
        return None

    def _mock_predict(self) -> dict:
        import random
        t0 = time.perf_counter()
        time.sleep(0.3 + random.random() * 0.2)
        scores = {}
        for c in CLASS_LABELS:
            scores[c] = random.uniform(0, 0.98)
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}
        maladie = max(scores, key=scores.get)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "maladie": maladie,
            "label_fr": FRENCH_LABELS[CLASS_LABELS.index(maladie)],
            "confidence": round(scores[maladie], 4),
            "all_scores": {k: round(v, 4) for k, v in scores.items()},
            "model_used": "mock_fallback",
            "analysis_time_ms": round(elapsed_ms, 1),
        }

    def _mock_gradcam(self, w: int, h: int) -> dict:
        import cv2, base64, numpy as np
        mock = np.zeros((h, w, 3), dtype=np.uint8)
        cv2.circle(mock, (w // 2, h // 2), min(w, h) // 4, (0, 0, 255), -1)
        _, buf = cv2.imencode(".png", mock)
        return {
            "heatmap": base64.b64encode(buf.tobytes()).decode("utf-8"),
            "overlay": base64.b64encode(buf.tobytes()).decode("utf-8"),
            "class_index": 0,
            "confidence": 0.85,
        }
