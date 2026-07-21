# Déploiement Edge RPi — Caméra, Modèles IA & Capteurs

## Topologie Réseau

```
┌──────────────────────────────────────────────────────────────┐
│                    Tailscale Mesh VPN                         │
├─────────────────────┬────────────────────────────────────────┤
│  Ubuntu Serveur     │  Raspberry Pi 4/5 (Edge Device)        │
│  100.x.x.2          │  100.x.x.3                             │
│                     │                                         │
│  Backend Flask+WS   │  dental_service.py  :8080              │
│  :5000              │    Orchestrateur threads edge          │
│                     │    HTTP commands, analyse IA           │
│  Frontend React     │                                         │
│  :3000              │  mjpeg_server.py    :8081              │
│                     │    Stream MJPEG caméra + snapshots     │
│  PostgreSQL :5432   │                                         │
│  Nginx :80/443      │  api_client.py      (importé)          │
│                     │    POST capteurs/analyses au Backend   │
│                     │                                         │
│                     │  ┌─── Caméra USB (/dev/video0)         │
│                     │  ├─── Arduino (/dev/ttyACM0)           │
│                     │  └─── Modèles TFLite (./models/)       │
└─────────────────────┴────────────────────────────────────────┘
```

Seuls les 3 services ci-dessus tournent sur le RPi (pas de Redis, pas de PostgreSQL, pas de Flask Backend local).

---

## Composants RPi

| Fichier | Rôle | Port |
|---------|------|------|
| `dental_service.py` | Orchestrateur principal : threads caméra, capteurs, heartbeat, watchdog, serveur HTTP | `:8080` |
| `mjpeg_server.py` | Serveur MJPEG indépendant : stream continu + snapshots | `:8081` |
| `api_client.py` | Client HTTP avec pool de connexions (4 persistantes) | — |
| `camera_manager.py` | Driver V4L2 : résolution adaptative, burst, analyse qualité | — |
| `sensor_manager.py` | Driver série Arduino : pH, température, distance, pression, humidité | — |
| `tf_inference.py` | Pipeline IA : U-Net → masquage → Multimodal → GradCAM | — |

---

## 1. Prérequis Matériel

### 1.1 Caméra (AZDENT USB intra-orale)

```bash
# Vérifier la détection
ls -la /dev/video*
v4l2-ctl --list-devices
# Doit afficher : "AZDENT" ou "USB Camera" sur /dev/video0

# Tester le flux
sudo apt install fswebcam
fswebcam -d /dev/video0 -r 1920x1080 test.jpg

# Activer le module si absent
sudo modprobe uvcvideo
```

**Résolutions supportées** (adaptatives selon charge CPU) :
- `1920×1080` (full HD, défaut)
- `1280×720` (HD, si CPU > 80%)
- `640×480` (SD, si CPU toujours > 80%)

### 1.2 Capteurs (Arduino Uno R4)

```bash
# Vérifier la détection
ls /dev/ttyACM* /dev/ttyUSB*
# Doit montrer /dev/ttyACM0

# Tester la communication
sudo apt install minicom
minicom -D /dev/ttyACM0 -b 115200
# Les données JSON doivent défiler : {"ph":7.0,"temp":36.5,...}
```

**Format attendu** : trames JSON avec checksum :
```json
{"ph":7.2,"temp":36.8,"dist":12.5,"press":758.3,"hum":45.1,"tip":0,"seq":142,"cs":187}
```

**Mapping des capteurs** pour le modèle multimodal :

| Champ | Unité | Défaut | Description |
|-------|-------|--------|-------------|
| `ph` | — | 7.0 | Acidité buccale |
| `temp` / `temperature` | °C | 36.5 | Température |
| `dist` / `distance` | mm | 10.0 | Distance de mesure |
| `press` / `pressure` | hPa | 760.0 | Pression atmosphérique |
| `hum` / `humidity` | % | 50.0 | Humidité |

### 1.3 Modèles TFLite

Deux fichiers requis dans `rpi_service/models/` :

```
/home/pi/dental/rpi_service/models/
├── multimodal_model.tflite   (15 MB)  Classification 4 classes
└── unet_model.tflite         (30 MB)  Segmentation U-Net
```

| Modèle | Variable d'env | Entrée | Sortie |
|--------|---------------|--------|--------|
| **Multimodal** (classifieur) | `TFLITE_MODEL` | `image_input` 224×224 RGB [-1,1] + `physio_input` vecteur [1, N] features | 4 classes : Healthy, Caries, Gingivitis, Periodontitis |
| **U-Net** (segmentation) | `UNET_MODEL` | Image 256×256 RGB | Masque binaire 256×256 |

**Pipeline d'inférence** :

```
Image brute → redim 256×256 → U-Net → masque binaire
                                          ↓
                                  redim taille originale
                                  multiplication pixel (masquage)
                                          ↓
                                  Image masquée (fond supprimé)
                                          ↓
                                  redim 224×224
                                  concaténer données capteurs (physio_input)
                                          ↓
                                  Multimodal Model
                                          ↓
                                  { maladie, confiance, all_scores,
                                    segmentation_mask (PNG base64),
                                    gradcam, analysis_time_ms }
```

**Fallback** : si la TFLite runtime ou les modèles sont absents, le service utilise un mock prédictif aléatoire (simule 300-500ms).

---

## 2. Déploiement sur le RPi

### 2.1 Créer les dossiers

```bash
# Sur le RPi
sudo mkdir -p /home/pi/dental/rpi_service/models /home/pi/captures
sudo chown -R pi:pi /home/pi/dental /home/pi/captures
```

### 2.2 Copier les fichiers depuis Ubuntu

```bash
# Sur l'Ubuntu — copie edge uniquement (pas backend_robot/ ni frontend_robot/)
scp -r rpi_service/ pi@100.x.x.3:/home/pi/dental/
```

Vérifie la structure :

```bash
# Sur le RPi
ls -la /home/pi/dental/rpi_service/
# Doit contenir : dental_service.py, mjpeg_server.py, api_client.py,
#                  camera_manager.py, sensor_manager.py, tf_inference.py,
#                  models/*.tflite, requirements.txt
```

### 2.3 Installer les dépendances Python

```bash
# Sur le RPi
cd /home/pi/dental
pip3 install --upgrade pip setuptools wheel
pip3 install -r rpi_service/requirements.txt

# Si tflite-runtime échoue (ARM), forcer la version compatible :
pip3 install tflite-runtime==2.14.0
# Ou utiliser le mock fallback (le service tourne sans TFLite)
```

**Dépendances** : `flask`, `requests`, `opencv-python-headless`, `numpy`, `pyserial`, `psutil`, `tflite-runtime`

### 2.4 Configurer Tailscale

```bash
# Sur les deux machines (Ubuntu + RPi)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Vérifier les IPs
tailscale ip
# Ubuntu → 100.x.x.2
# RPi    → 100.x.x.3
```

Les services RPi (`:8080`, `:8081`) ne sont accessibles que depuis le mesh Tailscale. Aucun port n'est ouvert sur Internet.

### 2.5 Variables d'environnement

Exporte avant le premier lancement (ou mets dans le service systemd) :

```bash
export BACKEND_API_URL=http://100.x.x.2:5000/api    # URL du Backend Ubuntu
export TFLITE_MODEL=multimodal_model.tflite          # Classifieur
export UNET_MODEL=unet_model.tflite                  # U-Net segmentation
export CAPTURE_DIR=/home/pi/captures                 # Dossier captures
export ARDUINO_PORT=/dev/ttyACM0                     # Port Arduino
export ARDUINO_BAUD=115200                           # Baud rate
export HTTP_PORT=8080                                # Port HTTP commands
```

> **Attention** : `BACKEND_API_URL` doit pointer vers l'IP Tailscale de l'Ubuntu, **pas** `localhost`.

### 2.6 Démarrer en test direct

```bash
cd /home/pi/dental
export BACKEND_API_URL=http://100.x.x.2:5000/api
export TFLITE_MODEL=multimodal_model.tflite
export UNET_MODEL=unet_model.tflite

# Lancer l'orchestrateur (contient le serveur HTTP + MJPEG)
python3 rpi_service/dental_service.py &

# Vérifier
curl http://localhost:8080/health          # → {"status":"ok","camera":true,...}
curl http://localhost:8081/snapshot.jpg    # → image JPEG
curl http://localhost:8081/stream.mjpg     # → flux MJPEG
```

### 2.7 Service systemd (production)

Crée `/etc/systemd/system/dental-edge.service` :

```ini
[Unit]
Description=Dental AI - Raspberry Pi Edge Service
After=network.target tailscaled.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/dental/rpi_service

Environment=BACKEND_API_URL=http://100.x.x.2:5000/api
Environment=CAPTURE_DIR=/home/pi/captures
Environment=ARDUINO_PORT=/dev/ttyACM0
Environment=ARDUINO_BAUD=115200
Environment=TFLITE_MODEL=multimodal_model.tflite
Environment=UNET_MODEL=unet_model.tflite
Environment=PYTHONUNBUFFERED=1

ExecStart=/usr/bin/python3 /home/pi/dental/rpi_service/dental_service.py
ExecStop=/bin/kill -SIGTERM $MAINPID
ExecStopPost=/bin/sleep 2

Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5

# CPU affinity for RPi 5 (4 cores)
CPUAffinity=0-3
IOSchedulingClass=best-effort
IOSchedulingPriority=3

[Install]
WantedBy=multi-user.target
```

Puis :

```bash
sudo cp rpi_service/systemd/dental-ai.service /etc/systemd/system/dental-edge.service
sudo systemctl daemon-reload
sudo systemctl enable --now dental-edge
sudo journalctl -u dental-edge -f    # suivre les logs
```

Le service est également disponible dans `rpi_service/systemd/dental-ai.service` (déjà préconfiguré avec les bonnes variables, à adapter pour l'IP Tailscale).

---

## 3. Flux de données détaillés

### 3.1 Flux caméra (MJPEG → Frontend)

```
Caméra USB → mjpeg_server.py (:8081)
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
 /stream.mjpg           /snapshot.jpg
 (flux continu)         (image unique JPEG)
       │                     │
       ▼                     ▼
<img src="...stream.mjpg">   CameraAdapter (TS)
(lecture MJPEG native)       polling 1000ms (fallback base64)
       │                     │
       ▼                     ▼
<LivePreview />              useScanStore.setCameraFrame()
(15-30 FPS, natif)           (pour composants nécessitant base64)
```

### 3.2 Flux analyse (Frontend → RPi → Backend)

```
Frontend "Lancer analyse"
       │
       ▼
InferenceAdapter → HTTP POST /start-analysis { session_id }
       │
       ▼
dental_service.py (:8080)
  → capture frame + enhance
  → post_capture() vers Backend (/api/scan/capture)
  → ai.predict(image, sensor_data)
       │
       ├── U-Net segment → masque binaire
       ├── masquage de l'image
       ├── Multimodal classifier (image + physio_input)
       └── GradCAM
       │
       ▼
post_analysis() vers Backend (/api/scan/analyze)
  { fusion, gradcam, sensor_snapshot, segmentation_mask }
       │
       ▼
Backend Flask → stocke en DB + émet WS /inference
       │
       ▼
InferenceAdapter (TS) → useScanStore.setInferenceResult()
```

### 3.3 Flux capteurs (continu)

```
Arduino (trames JSON, 200ms)
       │
       ▼
sensor_manager.py → parsing + checksum
       │
       ▼
dental_service.py → buffer circulaire (30s)
       │
       ▼
api_client.post_sensor_data() → Backend /api/hardware/sensors
       │
       ▼
Backend → émet WS /sensors
       │
       ▼
SensorAdapter (TS) → useScanStore.setSensorData()
```

### 3.4 Heartbeat (toutes les 5s)

```
dental_service.py → api_client.post_heartbeat()
       │
       ▼
POST /api/hardware/status
  { raspberry_pi: { hostname, kernel, temp, cpu_load },
    camera: { status },
    arduino: { status },
    ai_engine: { status, model } }
       │
       ▼
Frontend (React Query polling ou WS)
```

---

## 4. Optimisations performances

| Optimisation | Fichier | Détail |
|-------------|---------|--------|
| **MJPEG direct** | `mjpeg_server.py` | Flux MJPEG natif dans `<img>`, pas de base64/FileReader |
| **Snapshot polling 1s** | `CameraAdapter` | Fallback base64 réduit à 1/s (était 200ms) |
| **Pool HTTP** | `api_client.py` | `Session` + `HTTPAdapter` pool 4 connexions persistantes |
| **JPEG quality 80** | `mjpeg_server.py` | 15% plus rapide que 85, qualité visuelle identique |
| **Résolution adaptative** | `camera_manager.py` | Passe de 1080p → 720p → 480p si CPU > 80% |
| **Thread pinning** | `dental_service.py` | Threads caméra/capteurs sur core 3 avec priorité SCHED_FIFO |
| **Cache-Control no-cache** | `mjpeg_server.py` | Évite le caching navigateur sur les snapshots |
| **U-Net bypass** | `tf_inference.py` | Si U-Net absent, classification directe sans masquage |
| **Mock fallback** | `tf_inference.py` | Si TFLite runtime absente, prédiction aléatoire simulée |

---

## 5. Vérification complète

Depuis l'Ubuntu (ou tout appareil dans le mesh Tailscale) :

```bash
# 1. Santé du service RPi
curl http://100.x.x.3:8080/health
# → {"status":"ok","camera":true,"ai_loaded":true,"sensor_connected":true}

# 2. Snapshot caméra
curl http://100.x.x.3:8081/snapshot.jpg -o test.jpg
file test.jpg   # doit être JPEG

# 3. Données capteurs
curl http://100.x.x.3:8080/sensor-latest
# → {"ph":7.2,"temp":36.8,"dist":12.5,...}

# 4. Backend reachable depuis le RPi
curl http://100.x.x.2:5000/api/health
```

Depuis le navigateur (via le Frontend) :

```
http://<IP_UBUNTU>:3000
→ Nouveau diagnostic → sélectionner patient
→ Flux caméra MJPEG en direct (via <img> + streamUrl)
→ Lancer l'analyse → RPi U-Net + Multimodal → résultat dans le panneau inférence
→ Capteurs affichés en continu (via WS /sensors)
```

---

## 6. Structure finale sur le RPi

```
/home/pi/dental/
  └── rpi_service/
      ├── dental_service.py        Orchestrateur threads + HTTP (:8080)
      ├── mjpeg_server.py          Serveur MJPEG (:8081)
      ├── api_client.py            Client HTTP Backend (pool connexions)
      ├── camera_manager.py        Driver V4L2 caméra AZDENT
      ├── sensor_manager.py        Driver série Arduino Uno R4
      ├── tf_inference.py          Pipeline U-Net + Multimodal + GradCAM
      ├── requirements.txt         Dépendances Python
      ├── systemd/
      │   └── dental-ai.service    Service systemd (edge uniquement)
      └── models/
          ├── multimodal_model.tflite   Classification (image + capteurs)
          └── unet_model.tflite         Segmentation U-Net
```

Seuls les fichiers `rpi_service/` sont nécessaires sur le RPi. Le Backend (`backend_robot/`) et le Frontend (`frontend_robot/`) restent sur l'Ubuntu.

---

## 7. Dépannage

### La caméra ne s'ouvre pas

```bash
sudo modprobe uvcvideo
v4l2-ctl --list-devices
# Vérifier le périphérique : /dev/video0
# Essayer un autre index : CameraManager(device=1)
```

### L'Arduino n'est pas détecté

```bash
ls /dev/ttyACM* /dev/ttyUSB*
# Redémarrer le service : sudo systemctl restart dental-edge
# Vérifier le câble USB (data, pas power-only)
```

### Les modèles TFLite ne se chargent pas

```bash
ls -la /home/pi/dental/rpi_service/models/
echo "TFLITE_MODEL=$TFLITE_MODEL"
echo "UNET_MODEL=$UNET_MODEL"
# Vérifier les permissions : chmod 644 *.tflite
# Vérifier la plateforme : tflite-runtime n'existe pas sur x86_64 (mock fallback)
```

### Le RPi ne POSTe pas au Backend

```bash
# Depuis le RPi
curl http://100.x.x.2:5000/api/health
# Si échec : vérifier Tailscale (tailscale status)
# Vérifier la variable BACKEND_API_URL
echo $BACKEND_API_URL
```

### Erreur port déjà utilisé

```bash
sudo ss -tlnp | grep 8080
# Un autre processus écoute sur :8080
# Changer HTTP_PORT ou tuer l'ancien processus
```

### Lire les logs

```bash
sudo journalctl -u dental-edge -f --since "5 min ago"
# Ou en direct : python3 rpi_service/dental_service.py (sans daemon)
```

---

## 8. Sécurité

- **Tailscale Mesh VPN** : les ports 8080 et 8081 du RPi ne sont accessibles que depuis le mesh (100.x.x.x/10)
- **Aucun port exposé sur Internet** : pas de UPnP, pas de forwarding
- **ProtectSystem=full** dans systemd : les binaires système sont en lecture seule pour le service
- **NoNewPrivileges=true** : le service ne peut pas escalader ses privilèges
- **ACLs** configurables dans [Tailscale Admin Console](https://login.tailscale.com)

---

## 9. Différence avec l'ancienne architecture

| Avant (Redis bridge) | Après (Edge push) |
|----------------------|-------------------|
| RPi publie dans Redis (pub/sub) | RPi POSTe directement au Backend (HTTP) |
| Backend lit Redis et émet WS | Backend reçoit POST et émet WS |
| Frontend WS → Backend → Redis → RPi | Frontend HTTP → RPi, RPi POST → Backend |
| Redis requis sur le RPi | Plus de Redis sur le RPi |
| Communication indirecte | Communication directe RPi ↔ Backend |
| Modèle : Redis bridge | Modèle : Edge push |
