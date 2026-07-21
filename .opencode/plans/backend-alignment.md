# Plan d'alignement Backend ↔ Frontend

## 1. Fix `ScanResult.to_dict()` dans `backend_robot/app/models/scan_session.py`

**Remplacer** la méthode `to_dict()` de `ScanResult` (lignes 91-106) pour coller au type frontend `ScanResult` :

```python
def to_dict(self):
    diseases = []
    if self.all_scores:
        for maladie, score in sorted(self.all_scores.items(), key=lambda x: -x[1]):
            if maladie == "Healthy":
                continue
            severity = "severe" if score > 0.7 else "modere" if score > 0.4 else "faible"
            diseases.append({
                "maladie": maladie,
                "label": maladie.replace("_", " ").title(),
                "confidence": score,
                "severity": severity,
                "tooth_numbers": [],
                "surface_percent": round(score * 100, 1),
                "lesion_count": max(1, round(score * 5)),
            })
    return {
        "id": self.id,
        "capture_id": self.capture_id,
        "session_id": self.capture.session_id if self.capture else None,
        "fusion": {
            "maladie": self.maladie,
            "confidence": self.confidence,
            "all_scores": self.all_scores or {},
            "diseases": diseases,
            "risk_score": (self.risk_score or {}).get("overall", 0),
            "model_version": self.model_version,
            "analysis_time_ms": self.analysis_time_ms,
        },
        "segmentation_mask_path": None,
        "gradcam_path": None,
        "sensor_snapshot": self.sensor_snapshot or {},
        "xai": {
            "gradcam_heatmap": None,
            "overlay_image": None,
            "feature_importance": {},
            "generated_text": (self.xai_data or {}).get("summary", ""),
            "regions": [],
        },
        "recommendations": self.recommendations_data or [],
        "patient_history": self.history_data or None,
        "created_at": self.created_at.isoformat() if self.created_at else None,
    }
```

## 2. Fix `ScanCapture.to_dict()` dans le même fichier

**Remplacer** les clés `original_url`/`enhanced_url`/`thumbnail_url` par `original_path`/`enhanced_path`/`thumbnail_path` (lignes 52-71) :

```python
def to_dict(self):
    return {
        "id": self.id,
        "session_id": self.session_id,
        "original_path": self.original_path,
        "enhanced_path": self.enhanced_path,
        "thumbnail_path": self.thumbnail_path,
        "quality_score": self.quality_score,
        "quality_details": {
            "blur": self.quality_blur,
            "brightness": self.quality_brightness,
            "contrast": self.quality_contrast,
            "noise": self.quality_noise,
            "motion": self.quality_motion,
            "glare": self.quality_glare,
            "obstruction": 100.0,
            "tooth_visibility": 100.0,
            "gum_visibility": 100.0,
            "overall": self.quality_score,
        },
        "width": self.width,
        "height": self.height,
        "captured_at": self.captured_at.isoformat() if self.captured_at else None,
    }
```

## 3. Fix `check-conditions` route dans `backend_robot/app/blueprints/scan/routes.py`

**Remplacer** les lignes 29-75 (retour objet → retour tableau) :

```python
CHECK_LABELS = {
    "tip_present": "Embout stérile détecté",
    "camera_available": "Caméra connectée",
    "brightness_sufficient": "Luminosité suffisante",
    "resolution_ok": "Résolution correcte",
    "focus_ok": "Netteté acceptable",
    "disk_space": "Espace disque suffisant",
    "db_connected": "Base de données connectée",
    "backend_healthy": "Backend opérationnel",
    "model_loaded": "Modèle IA chargé",
    "opencv_ready": "OpenCV prêt",
    "websocket_ready": "WebSocket connecté",
}

@scan_bp.route("/check-conditions", methods=["GET"])
@jwt_required()
def check_conditions():
    checks = {k: True for k in CHECK_LABELS}
    disk = os.statvfs("/")
    if disk.f_frsize * disk.f_bavail < 500 * 1024 * 1024:
        checks["disk_space"] = False
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:
        checks["db_connected"] = False
    try:
        import cv2
    except ImportError:
        checks["opencv_ready"] = False
    from app.websocket import socketio
    checks["websocket_ready"] = socketio is not None and socketio.server is not None
    results = [
        {"name": k, "label": CHECK_LABELS[k], "passed": v}
        for k, v in checks.items()
    ]
    for key, passed in checks.items():
        if not passed:
            logger.warning("Pre-scan check failed: %s", key)
    logger.info("Pre-scan checks: all_passed=%s", all(checks.values()))
    return jsonify({"checks": results, "all_passed": all(checks.values()), "timestamp": time.time()})
```

## 4. Fix `start_session` route

**Remplacer** les lignes 110-113 pour retourner le session complet :

```python
return jsonify(session.to_dict()), 201
```

## 5. Ajouter les routes manquantes dans `scan/routes.py`

**Ajouter** à la fin du fichier, avant la dernière ligne :

```python
@scan_bp.route("/result/<int:capture_id>", methods=["GET"])
@jwt_required()
def get_scan_result(capture_id):
    result = ScanResult.query.filter_by(capture_id=capture_id).first()
    if not result:
        return jsonify({"error": "Résultat introuvable"}), 404
    return jsonify(result.to_dict())

@scan_bp.route("/session/<int:session_id>", methods=["GET"])
@jwt_required()
def get_session(session_id):
    session = db.session.get(ScanSession, session_id)
    if not session:
        return jsonify({"error": "Session introuvable"}), 404
    return jsonify(session.to_dict())

@scan_bp.route("/session/<int:session_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_session(session_id):
    session = db.session.get(ScanSession, session_id)
    if not session:
        return jsonify({"error": "Session introuvable"}), 404
    session.status = "cancelled"
    session.ended_at = db.func.now()
    db.session.commit()
    return jsonify({"message": "Session annulée", "session_id": session.id})

@scan_bp.route("/progress/capture/<int:capture_id>", methods=["GET"])
@jwt_required()
def get_capture_progress(capture_id):
    capture = db.session.get(ScanCapture, capture_id)
    if not capture:
        return jsonify({"error": "Capture introuvable"}), 404
    result = ScanResult.query.filter_by(capture_id=capture.id).first()
    progress = 100 if result else 50
    return jsonify({"progress": progress, "has_result": result is not None})
```

## 6. Fix `hardware/status` route dans `backend_robot/app/blueprints/hardware/routes.py`

**Remplacer** le bloc final du handler (lignes 192-196) :

```python
COMPONENT_META = {
    "raspberry_pi": {"name": "raspberry_pi", "label": "Raspberry Pi"},
    "camera": {"name": "camera", "label": "Caméra"},
    "arduino": {"name": "arduino", "label": "Arduino"},
    "opencv": {"name": "opencv", "label": "OpenCV"},
    "tflite": {"name": "tflite", "label": "TensorFlow Lite"},
    "yolo": {"name": "yolo", "label": "YOLOv8"},
    "websocket": {"name": "websocket", "label": "WebSocket"},
}

components_list = []
for key, data in all_components.items():
    meta = COMPONENT_META.get(key, {"name": key, "label": key})
    components_list.append({
        "name": meta["name"],
        "label": meta["label"],
        "status": data["status"],
        "message": data.get("message", ""),
    })
for sensor_key, sensor_data in sensors.items():
    components_list.append({
        "name": sensor_key,
        "label": f"Capteur {sensor_key}",
        "status": sensor_data["status"],
        "message": sensor_data.get("message", ""),
    })

return jsonify({"components": components_list, "allReady": all_ready, "overall_status": worst_status})
```

## 7. Ajouter `humidity` à SensorData

**Ajouter** une colonne après `pressure` dans `backend_robot/app/models/sensor_data.py` :

```python
humidity = db.Column(db.Float, nullable=True)
```

Et dans `to_dict()` :

```python
'humidity': self.humidity,
```

## 8. Mettre à jour les API clients frontend

Dans `frontend_robot/src/lib/api/scan.api.ts` :

- `checkConditions`: retourne `data.checks` au lieu de `data`
- `startSession`: retourne `data` (le session complète)
- `analyze`: retourne `data.result`
- `getResult`: nouvelle méthode utilisant `GET /scan/result/:id`
- `getSessionById`: nouvelle méthode
- `cancelSession`: nouvelle méthode
- `getProgress`: utiliser `/scan/progress/capture/:id`

Dans `frontend_robot/src/lib/api/hardware.api.ts` :

- `getStatus`: retourner `data.components`

## 9. Simplifier `CheckCondition` dans les types frontend

Dans `frontend_robot/src/types/diagnostic-session.ts` :

```typescript
export interface CheckCondition {
  name: string;
  label: string;
  passed: boolean;
}
```

## Ordre d'exécution recommandé

1. Models (to_dict)
2. Routes scan (check-conditions, start_session, +3 nouvelles)
3. Routes hardware (components array)
4. SensorData (humidity)
5. Frontend API clients
6. Frontend types
7. Build backend
8. Build frontend
