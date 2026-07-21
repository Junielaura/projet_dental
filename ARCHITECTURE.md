# Dental AI - Architecture

## Vue d'ensemble

```
┌──────────────────────────────────────────────────────────────────┐
│                        Frontend (Next.js 15)                      │
│  localhost:3000                                                   │
│  App Router / React 19 / Shadcn UI / Zustand / Recharts          │
│  WebSocket client (socket.io-client)                              │
└──────────────────────┬───────────────────────────────────────────┘
                       │ HTTP/WS
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│              Nginx (reverse proxy + TLS + rate limit)              │
│  HTTP:80 → HTTPS:443                                              │
│  /api/* → backend:5000  /socket.io/* → backend:5000              │
│  /flower/* → flower:5555  /* → frontend:3000                     │
└──────┬──────────────────────────────────┬────────────────────────┘
       │                                  │
       ▼                                  ▼
┌─────────────────┐          ┌──────────────────────────┐
│ Backend (Flask)   │          │ RPi 5 Service             │
│ port 5000         │          │ dental_service.py         │
│ Blueprints:       │          │ Threads:                  │
│  auth / patients  │◄──Redis──│  watchdog (core 0)        │
│  diagnostics      │ pub/sub  │  cmds+publisher (core 1)  │
│  scan / quality   │ 4 canaux │  AI inference (core 2)    │
│  hardware / sys   │          │  camera+sensors (core 3)  │
│  monitoring / ... │          │                           │
│                   │          │ TFLite (YOLOv8+Efficient) │
│ WebSocket 3 ns:   │          │ GradCAM heatmaps          │
│  /camera          │          │ Circuit breaker           │
│  /sensors          │          │ Burst capture (3-5 img)  │
│  /inference       │          │                           │
│                   │          │ Arduino Uno R4 (Serial)    │
│ Celery workers    │          │ Humidity/Distance/Temp    │
└──────┬────────────┘          └──────────────────────────┘
       │
       ▼
┌────────────────────┐
│ PostgreSQL 16      │
│ Tables:            │
│  scan_sessions     │
│  scan_captures     │
│  scan_results      │
│  system_health_log │
│  patients / users  │
│  diagnostics /... │
└────────────────────┘
```

## Pipeline de scan complet (16 etapes)

```
[1] Init session     → POST /api/scan/session (jwt)
[2] Positionnement   → Wizard UI guide distance/angle
[3] Auto-capture     → 2s stabilite (qualite≥80, humidite≤85%, distance 10-40mm)
    [3a] Stabilisation → 10 fps, analyse qualite 10 criteres
    [3b] Burst (5 img) → select_best → encode JPEG
[4] Analyse qualite   → CLAHE + denoising + 10 criteres (score ≥ 80)
[5] Notification qualite → WebSocket /inference emit "quality_check"
[6] Preprocessing    → Resize (640×640) + normalize + TFLite input tensor
[7] Inference IA     → TFLite interpreter → predict()
                      [7a] YOLOv8 segmentation (dents/caries/plaque)
                      [7b] EfficientNetV2 (classification etat)
[8] GradCAM          → heatmap + overlay + contours + suspect_regions
[9] Publication progression → 6 etapes → WebSocket /inference
[10] Sauvegarde resultat → scan_results (BDD)
[11] Fusion capteurs → → humidite+distance+temperature + IA
[12] Recommandations → regles metier (IA + capteurs + historique patient)
[13] Finalisation    → scan status → "completed"
[14] Notification    → WebSocket /inference "scan_complete"
[15] Visualisation   → Viewer 3 colonnes (IA + capteurs + rapport)
[16] Export          → PDF/CSV

## Structure des repertoires

projet_dental/           # Meta-repo (sous-modules)
  ├── DOCS.md
  ├── ARCHITECTURE.md
  ├── backend_robot/     # Flask API + RPi service
  │   ├── app/
  │   ├── rpi_service/
  │   │   ├── dental_service.py   # Orchestrateur RPi
  │   │   ├── camera_manager.py   # V4L2 MJPG capture
  │   │   ├── sensor_manager.py   # Serial Arduino
  │   │   ├── tf_inference.py     # TFLite + GradCAM
  │   │   ├── systemd/            # Services systemd
  │   │   └── deploy.sh
  │   ├── migrations/
  │   ├── scripts/
  │   │   ├── ssl-setup.sh
  │   │   ├── update.sh
  │   │   └── backup.sh
  │   ├── contrib/
  │   │   └── fail2ban/
  │   ├── docker-compose.yml
  │   ├── nginx.conf
  │   └── requirements.txt
  └── frontend_robot/    # Next.js 15
      ├── src/
      │   ├── app/       # App Router
      │   ├── components/ # Shadcn UI
      │   ├── hooks/     # useWebSocket, useAutomaticCapture
      │   ├── lib/       # api clients, types
      │   └── store/     # Zustand
      ├── Dockerfile
      └── package.json

## Modeles BDD

scan_sessions
  id, patient_id (FK), status, device_id,
  sensor_snapshot (JSONB), notes, started_at, created_at

scan_captures
  id, session_id (FK CASCADE), image_path,
  quality_score, quality_details (JSONB), file_size_bytes,
  width, height, format, camera_settings (JSONB), captured_at

scan_results
  id, capture_id (FK CASCADE), session_id (FK CASCADE),
  inference_result (JSONB), gradcam_path, segmentation_mask_path,
  classification, confidence, processing_time_ms,
  recommendations (JSONB), model_version, processed_at

system_health_log
  id, component, status, cpu_percent, temp_celsius,
  mem_percent, disk_free_bytes, fps_current,
  inference_time_ms, error_message, checked_at

## Docker Compose (8 services)

postgres:     PostgreSQL 16
redis:        Redis 7 (broker + pub/sub)
backend:      Flask API (gunicorn 2 workers)
celery_worker: Celery worker (2 workers, 1000 taches max)
celery_beat:  Celery beat scheduler
flower:       Celery monitoring (auth basic)
frontend:     Next.js 15 standalone
nginx:        Reverse proxy + TLS + rate limiting