# Dental AI Diagnostic — Documentation complète

> Système intelligent de diagnostic des maladies dentaires basé sur l'IA.
> Stack : Raspberry Pi 5 + Arduino Uno R4 + TensorFlow Lite + Flask + Next.js 15 + PostgreSQL 16

---

## Table des matières

1. [Architecture système](#1-architecture-système)
2. [Diagrammes de séquence](#2-diagrammes-de-séquence)
3. [Guide d'installation — Backend](#3-guide-dinstallation--backend)
4. [Guide d'installation — Frontend](#4-guide-dinstallation--frontend)
5. [Déploiement sur Raspberry Pi](#5-déploiement-sur-raspberry-pi)
6. [Docker Compose](#6-docker-compose)
7. [Référence API](#7-référence-api)
8. [Dépannage](#8-dépannage)

---

## 1. Architecture système

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND (Next.js 15)                               │
│                                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Diagnostic   │  │ Dashboard    │  │ Monitoring   │  │ Documentation    │  │
│  │ Viewer (SPA) │  │ Patients/    │  │ Temps réel   │  │ API Swagger      │  │
│  │ 6 phases     │  │ Diagnostics  │  │ WebSocket    │  │                  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────────────────┘  │
│         │                 │                 │                                │
│         └─────────────────┼─────────────────┘                                │
│                           │                                                  │
│                    ┌──────▼───────┐                                          │
│                    │   Zustand    │  Stores : auth, session, sidebar, theme  │
│                    │   TanStack   │  useQuery / useMutation                  │
│                    │   Axios      │  Intercepteur JWT + refresh              │
│                    │   Recharts   │  Graphiques (Radar, Bar, Area, Gauge)   │
│                    └──────────────┘                                          │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ HTTP REST + WebSocket (Socket.IO)
                           │
┌──────────────────────────▼───────────────────────────────────────────────────┐
│                         BACKEND (Flask 3.x)                                   │
│                                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Blueprints  │  │ Services     │  │ Repositories │  │ Middleware        │  │
│  │             │  │              │  │              │  │                  │  │
│  │ • auth      │  │ AuthService  │  │ UserRepo     │  │ JWT              │  │
│  │ • patients  │  │ PatientSvc   │  │ PatientRepo  │  │ CORS             │  │
│  │ • diag      │  │ DiagnosticSvc│  │ DiagnosticRep│  │ Rate Limiter     │  │
│  │ • hardware  │  │ SensorSvc    │  │ SensorRepo   │  │ Security Headers │  │
│  │ • scan      │  │ AuditSvc     │  │ AuditRepo    │  │ Error Handler    │  │
│  │ • sensors   │  │ Notification │  │ Notification │  │ Logging          │  │
│  │ • reports   │  │ ReportsSvc   │  │              │  │                  │  │
│  │ • dashboard │  │              │  │              │  │                  │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  └──────────────────┘  │
│         │                 │                 │                                │
│         └─────────────────┼─────────────────┘                                │
│                           │                                                  │
│                    ┌──────▼───────┐                                          │
│                    │  Gunicorn    │  WSGI server, workers: 2                 │
│                    │  Celery      │  Tâches asynchrones (notifications)      │
│                    │  Redis       │  Cache, session, pub/sub, rate limit     │
│                    │  PostgreSQL  │  Base de données principale              │
│                    └──────────────┘                                          │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ Serial (USB) / I2C / GPIO
                           │
┌──────────────────────────▼───────────────────────────────────────────────────┐
│                    RASPBERRY PI 5                                            │
│                                                                              │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────────┐  │
│  │ dental_service  │  │ camera_manager   │  │ tf_inference               │  │
│  │                 │  │                  │  │                            │  │
│  │ • Orchestrateur │  │ • OpenCV capture │  │ • TFLite Interpreter       │  │
│  │ • Redis pub/sub │  │ • Burst (5 img)  │  │ • Classification (4 cls)   │  │
│  │ • Threads       │  │ • CLAHE enhance  │  │ • GradCAM computation      │  │
│  │ • Command loop  │  │ • Quality score  │  │ • U-Net segmentation       │  │
│  └────────┬────────┘  └────────┬─────────┘  └────────────────────────────┘  │
│           │                    │                                            │
│           └────────────────────┼────────────────────────────────────────────┘
│                                │                                            │
│                    ┌───────────▼───────────┐                                │
│                    │   Redis (local)       │                                │
│                    │   Pub/Sub :           │                                │
│                    │   • sensor_data       │◄──── Arduino (Serial 115200)  │
│                    │   • dental_commands   │───► Flask API                 │
│                    │   • dental_events     │───► WebSocket clients         │
│                    └───────────────────────┘                                │
└──────────────────────────────────────────────────────────────────────────────┘
                           │ Serial USB
                           │
┌──────────────────────────▼───────────────────────────────────────────────────┐
│                    ARDUINO UNO R4                                           │
│                                                                              │
│  Capteurs :                                                                  │
│  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ pH       │  │ Température│  │ Distance │  │ Pression │  │ Humidité    │  │
│  │ salivaire│  │ buccale    │  │ (cm)     │  │ (N)      │  │ salivaire   │  │
│  └──────────┘  └────────────┘  └──────────┘  └──────────┘  └─────────────┘  │
│                                                                              │
│  Fréquence : 200ms — Format : JSON sur Serial                               │
│  Détection embout stérile : Contact sec (GPIO)                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Schéma de déploiement (3 repos liés par sous-modules)

```
projet_dental/              ← Meta-repo (sous-modules Git)
├── DOCS.md                 # Documentation centrale
├── ARCHITECTURE.md         # Architecture complète
├── backend_robot/          ← Sous-module
│   ├── app/                # Flask API (blueprints, models, middleware)
│   ├── rpi_service/        # Orchestrateur RPi (5 threads, TFLite, GradCAM)
│   ├── migrations/         # Alembic (4 versions)
│   ├── scripts/            # ssl-setup, update, backup
│   ├── contrib/fail2ban/   # Filtres de sécurité
│   ├── nginx.conf          # Reverse proxy + HTTPS/WSS
│   └── docker-compose.yml  # 8 services
├── frontend_robot/         ← Sous-module
│   ├── src/app/            # App Router (dashboard, scan, patients...)
│   ├── src/components/     # Wizard 6 phases, Viewer 3 colonnes
│   ├── src/hooks/          # useWebSocket, useAutomaticCapture...
│   └── src/store/          # Zustand (26 actions / 30 champs)
```

### Pipeline IA complet (6 étapes WebSocket)

L'inférence TFLite sur RPi publie sa progression via le namespace `/inference` :

1. `preprocessing` — Resize 640×640 + normalize + CLAHE
2. `detection` — YOLOv8 segmentation (dents, caries, plaque)
3. `classification` — EfficientNetV2 (4 classes, softmax)
4. `gradcam` — Heatmap overlay + contours + suspect_regions
5. `fusion` — Fusion IA + capteurs (pH, humidité, distance) + historique patient
6. `complete` — Résultat final avec recommandations

### Stack technique

| Couche | Technologie | Version |
|--------|-------------|---------|
| Frontend | Next.js (App Router) | 15.5.x |
| | React | 19 (RC) |
| | TypeScript | 5.5.x |
| | TailwindCSS | 3.4.x |
| | Shadcn UI | latest |
| | Recharts | 2.12.x |
| | TanStack Query | 5.x |
| | Zustand | 4.x |
| | Axios | 1.7.x |
| Backend | Python / Flask | 3.12 / 3.0.3 |
| | Gunicorn | 22.x |
| | SQLAlchemy | 2.x |
| | Celery | 5.4.x |
| | Redis | 7.x |
| BDD | PostgreSQL | 16 |
| IoT | Raspberry Pi 5 | Bookworm |
| | Arduino Uno R4 | — |
| | TensorFlow Lite | 2.16 |
| | OpenCV | 4.9.x |
| Conteneurs | Docker | latest |
| | Docker Compose | V2 |

---

## 2. Diagrammes de séquence

### 2.1 Diagnostic complet (16 étapes)

```
Patient         Dentiste            Frontend            Backend             RPi            Arduino
   │               │                   │                   │                 │               │
   │               │  Nouveau diag     │                   │                 │               │
   │               │──────────────────►│                   │                 │               │
   │               │                   │ GET /api/hardware/status             │               │
   │               │                   │──────────────────►│                 │               │
   │               │                   │                   │──Serial query──►│               │
   │               │                   │                   │                 │──I2C/Serial──►│
   │               │                   │                   │◄────────────────│◄──────────────│
   │               │◄──── Status ──────│◄──────────────────│                 │               │
   │               │                   │                   │                 │               │
   │               │  Démarrer scan    │                   │                 │               │
   │               │──────────────────►│ POST /api/scan/session/start        │               │
   │               │                   │──────────────────►│                 │               │
   │               │◄── Session ID ────│◄──────────────────│                 │               │
   │               │                   │                   │                 │               │
   │               │   Vérification    │                   │                 │               │
   │               │──────────────────►│ GET /api/scan/check-conditions      │               │
   │               │                   │──────────────────►│                 │               │
   │               │                   │                   │ ◄── 9 checks ──│               │
   │               │◄──── Résultat ────│◄──────────────────│                 │               │
   │               │                   │                   │                 │               │
   │               │   Flux caméra     │                   │                 │               │
   │               │──────────────────►│ WS /ws/camera     │                 │               │
   │               │                   │◄══════════════════│══ frame (JPEG)══│               │
   │               │                   │◄══ distance ──────│═════════════════│               │
   │               │                   │                   │                 │               │
   │               │   Capturer        │                   │                 │               │
   │               │──────────────────►│ POST /api/scan/capture              │               │
   │               │                   │──────────────────►│── command──────►│               │
   │               │                   │                   │                 │ capture()     │
   │               │                   │                   │◄─ burst 5 imgs─│               │
   │               │◄── Qualité ───────│◄─ résultat ───────│                 │               │
   │               │                   │                   │                 │               │
   │               │                   │ WS /ws/inference  │                 │               │
   │               │                   │◄════progress══════│══ TFLite ──────►│               │
   │               │                   │  (6 étapes)       │◄── résultats ──│               │
   │               │                   │◄════ result═══════│═════════════════│               │
   │               │◄── Diagnostic ────│◄══════════════════│                 │               │
   │               │                   │                   │                 │               │
   │               │                   │ POST /api/reports/generate          │               │
   │               │                   │──────────────────►│                 │               │
   │               │◄── PDF ───────────│◄──────────────────│                 │               │
   │               │                   │                   │                 │               │
   │   Résultats   │                   │                   │                 │               │
   │◄──────────────│───────────────────│───────────────────│─────────────────│──────────────│
```

### 2.2 Pipeline d'inférence IA

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐
│ Pre-     │───►│ Détection│───►│ Segmen-  │───►│ Classif. │───►│ GradCAM  │───►│ Fusion    │
│ traitement│    │ dents    │    │ tation   │    │ Efficient│    │          │    │ capteurs   │
│          │    │          │    │ U-Net    │    │ Net      │    │          │    │           │
├──────────┤    ├──────────┤    ├──────────┤    ├──────────┤    ├──────────┤    ├───────────┤
│ Resize   │    │ MobileNet│    │ Mask     │    │ 4 classes│    │ Heatmap  │    │ IA + pH   │
│ 224×224  │    │ SSD      │    │ binaire  │    │ Softmax  │    │ overlay  │    │ + temp    │
│ Normalize│    │ Boxes    │    │ 224×224  │    │ Argmax   │    │ rouge→   │    │ + histo   │
│ CLAHE    │    │ conf>0.5 │    │          │    │          │    │ jaune    │    │           │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘    └───────────┘
      │               │               │               │               │               │
      └───────────────┴───────────────┴───────────────┴───────────────┴───────────────┘
                                         │
                                   ┌─────▼─────┐
                                   │ Résultat  │
                                   │ final     │
                                   │ • maladie │
                                   │ • conf.   │
                                   │ • indices │
                                   │ • XAI     │
                                   └───────────┘
```

---

## 3. Guide d'installation — Backend

### Prérequis

- Python 3.12+
- PostgreSQL 16
- Redis 7+
- Docker (optionnel mais recommandé)

### Installation locale

```bash
# 1. Cloner le dépôt
git clone https://github.com/Junielaura/projet_dental.git
cd projet_dental/backend_robot

# 2. Créer l'environnement virtuel
python3 -m venv .venv
source .venv/bin/activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Copier et configurer .env
cp .env.example .env
# Éditer .env avec vos valeurs

# 5. Lancer PostgreSQL + Redis (ou Docker)
# Option locale :
# sudo service postgresql start && sudo service redis-server start
# Option Docker :
docker compose up -d postgres redis

# 6. Initialiser la base de données
flask db upgrade
# ou
python manage.py init_db
python manage.py seed_db
python manage.py create_admin

# 7. Lancer le serveur
flask run --host=0.0.0.0 --port=5000
# ou en production :
gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 wsgi:app
```

### Variables d'environnement (.env)

```ini
FLASK_APP=wsgi:app
FLASK_ENV=development

# Sécurité
SECRET_KEY=votre-secret-256bits
JWT_SECRET_KEY=votre-jwt-secret

# JWT
JWT_ACCESS_TOKEN_EXPIRES=900        # 15 min
JWT_REFRESH_TOKEN_EXPIRES=604800    # 7 jours

# Base de données
DATABASE_URL=postgresql://dental_user:dental_pass@localhost:5432/dental_db

# Redis
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# CORS
CORS_ORIGINS=http://localhost:3000

# Uploads
UPLOAD_FOLDER=./uploads
MAX_CONTENT_LENGTH=10485760

# Raspberry Pi
RPI_SERIAL_PORT=/dev/ttyACM0
RPI_BAUD_RATE=115200

# Logging
LOG_DIR=./logs
LOG_LEVEL=INFO

# Email (optionnel)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=noreply@dental-ai.com
```

### Structure du projet backend

```
backend_robot/
├── app/
│   ├── __init__.py              # Factory create_app()
│   ├── extensions.py            # Flask extensions (db, migrate, jwt, ma)
│   ├── celery_app.py            # Celery config
│   ├── logging_config.py        # Logging (RotatingFileHandler)
│   ├── seeds.py                 # Données initiales
│   ├── blueprints/
│   │   ├── auth/                # Authentification JWT
│   │   ├── patients/            # CRUD patients
│   │   ├── diagnostics/         # CRUD diagnostics + analyse IA
│   │   ├── monitoring/          # Capteurs temps réel
│   │   ├── hardware/            # Status Raspberry Pi, caméra, Arduino
│   │   ├── scan/                # Session de scan, capture, analyse
│   │   ├── reports/             # Génération PDF, export CSV
│   │   ├── images/              # Upload / récupération images
│   │   ├── users/               # Gestion utilisateurs (admin)
│   │   ├── audit/               # Traçabilité
│   │   ├── dashboard/           # Statistiques agrégées
│   │   └── notifications/       # Notifications utilisateur
│   ├── models/                  # SQLAlchemy models (8 tables)
│   ├── services/                # Logique métier
│   ├── repositories/            # Pattern repository
│   ├── middleware/               # Error handler, auth, security
│   └── tasks/                   # Tâches Celery (notifications, email)
├── migrations/                  # Flask-Migrate (Alembic)
├── scripts/
│   └── migrate.sh               # Helper migrations Docker
├── config.py                    # Configuration par environnement
├── requirements.txt             # Dépendances Python
├── manage.py                    # CLI : init_db, seed_db, create_admin
├── wsgi.py                      # Point d'entrée Gunicorn
├── celery_worker.py             # Point d'entrée Celery
└── Dockerfile                   # Image Docker
```

---

## 4. Guide d'installation — Frontend

### Prérequis

- Node.js 20+
- npm 10+

### Installation

```bash
# 1. Cloner le dépôt
git clone https://github.com/Junielaura/frontend_robot.git
cd frontend_robot

# 2. Installer les dépendances
npm install --legacy-peer-deps

# 3. Configurer l'environnement
# Copier .env.local.example vers .env.local ou définir :
echo "NEXT_PUBLIC_API_URL=http://localhost:5000/api" > .env.local
echo "NEXT_PUBLIC_WS_URL=ws://localhost:5000/ws" >> .env.local

# 4. Lancer le serveur de développement
npm run dev
# → http://localhost:3000

# 5. Build production
npm run build
npm start
```

### Variables d'environnement

```ini
NEXT_PUBLIC_API_URL=http://localhost:5000/api
NEXT_PUBLIC_WS_URL=ws://localhost:5000/ws
```

### Structure du projet frontend

```
frontend_robot/
├── src/
│   ├── app/
│   │   ├── layout.tsx            # Root layout (Inter font)
│   │   ├── providers.tsx         # ThemeProvider > QueryProvider > AuthProvider
│   │   ├── globals.css           # CSS variables (HSL), dark mode
│   │   ├── page.tsx              # Redirige vers /auth/login
│   │   ├── auth/
│   │   │   ├── layout.tsx
│   │   │   └── login/ + forgot-password/ + reset-password/
│   │   └── dashboard/
│   │       ├── layout.tsx        # Sidebar + Header + main
│   │       ├── page.tsx          # Dashboard stats
│   │       ├── patients/         # CRUD patients
│   │       ├── diagnostics/
│   │       │   ├── page.tsx      # Liste diagnostics
│   │       │   ├── new/page.tsx  # Diagnostic Viewer (6 phases)
│   │       │   └── [id]/page.tsx # Visualisation détaillée
│   │       ├── monitoring/       # Capteurs temps réel
│   │       ├── reports/          # Rapports PDF
│   │       ├── users/            # Gestion utilisateurs
│   │       ├── settings/         # Profil, thème
│   │       ├── notifications/    # Liste notifications (pagination, lire, supprimer)
│   │       └── docs/             # Documentation API Swagger
│   ├── components/
│   │   ├── ui/                   # Shadcn UI (18 composants)
│   │   ├── layout/               # DashboardLayout, Sidebar, Header, NotificationPanel
│   │   ├── common/               # PageHeader, StatusBadge, Skeleton, EmptyState
│   │   ├── diagnostics/          # 16 composants du Diagnostic Viewer
│   │   │   ├── wizard-stepper.tsx
│   │   │   ├── hardware-status-panel.tsx
│   │   │   ├── pre-scan-checklist.tsx
│   │   │   ├── live-preview.tsx
│   │   │   ├── sensor-panel.tsx
│   │   │   ├── capture-progress.tsx
│   │   │   ├── inference-pipeline.tsx
│   │   │   ├── layer-visualizer.tsx
│   │   │   ├── diagnostic-result.tsx
│   │   │   ├── multiclass-probabilities.tsx
│   │   │   ├── biomedical-indices.tsx
│   │   │   ├── biomedical-parameters.tsx
│   │   │   ├── risk-score-gauge.tsx
│   │   │   ├── xai-explanation.tsx
│   │   │   ├── historical-comparison.tsx
│   │   │   ├── recommendations-list.tsx
│   │   │   ├── patient-evolution.tsx
│   │   │   ├── advanced-indicators.tsx
│   │   │   ├── medical-report-actions.tsx
│   │   │   ├── audit-trail.tsx
│   │   │   ├── clinical-recommendations.tsx
│   │   │   ├── dental-image-viewer.tsx
│   │   │   ├── patient-header.tsx
│   │   │   ├── xai-visualization.tsx
│   │   │   └── error-panel.tsx
│   │   ├── forms/
│   │   ├── dialogs/
│   │   ├── charts/
│   │   ├── data-table/
│   │   └── monitoring/
│   ├── hooks/                    # 9 hooks custom
│   │   ├── use-auth.ts
│   │   ├── use-hardware-check.ts
│   │   ├── use-pre-scan-check.ts
│   │   ├── use-camera-stream.ts
│   │   ├── use-intelligent-capture.ts
│   │   ├── use-inference-pipeline.ts
│   │   ├── use-sensor-data.ts
│   │   ├── use-pdf-report.ts
│   │   ├── use-socket.ts
│   │   ├── use-notification-socket.ts  # WebSocket /notifications en temps réel
│   │   └── use-permissions.ts
│   ├── stores/                   # Zustand : auth, session, sidebar, theme, notifications
│   ├── lib/
│   │   ├── api/                  # 10 modules API (axios)
│   │   │   ├── client.ts         # Axios instance + intercepteur JWT
│   │   │   ├── auth.api.ts
│   │   │   ├── patients.api.ts
│   │   │   ├── diagnostics.api.ts
│   │   │   ├── scan.api.ts
│   │   │   ├── hardware.api.ts
│   │   │   ├── monitoring.api.ts
│   │   │   ├── reports.api.ts
│   │   │   ├── users.api.ts
│   │   │   ├── notifications.api.ts  # Liste, unread-count, markRead, delete
│   │   │   └── dashboard.api.ts
│   │   ├── utils.ts              # cn(), formatDate(), getConfidenceColor()
│   │   ├── constants.ts          # Rôles, status, maladies
│   │   └── mock-diagnostic.ts    # Données mockées
│   └── types/                    # TypeScript interfaces
│       ├── index.ts
│       └── diagnostic-session.ts
├── middleware.ts                 # Next.js middleware
├── next.config.mjs
├── tailwind.config.ts
├── tsconfig.json
└── package.json
```

---

## 5. Déploiement sur Raspberry Pi

### 5.1 Préparation du Raspberry Pi 5

```bash
# Mettre à jour le système
sudo apt update && sudo apt upgrade -y
sudo rpi-update

# Installer les dépendances système
sudo apt install -y \
    python3-pip python3-venv python3-dev \
    libatlas-base-dev libhdf5-dev libhdf5-serial-dev \
    libjasper-dev libqtgui4 libqt4-test \
    libilmbase-dev libopenexr-dev libgstreamer1.0-dev \
    libavcodec-dev libavformat-dev libswscale-dev \
    libv4l-dev libjpeg-dev libpng-dev \
    git cmake build-essential

# Installer Redis
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Installer Docker (optionnel)
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
```

### 5.2 Installation du service RPi

```bash
# Cloner le dépôt
git clone https://github.com/Junielaura/backend_robot.git
cd backend_robot

# Installer les dépendances RPi (légères)
pip install --break-system-packages -r rpi_service/requirements.txt

# Pour TensorFlow Lite (optimisé ARM64)
pip install --break-system-packages \
    https://github.com/KumaTea/tflite-runtime-armv7l/releases/download/v2.14.0/tflite_runtime-2.14.0-cp311-cp311-linux_armv7l.whl

# OU utiliser le fallback mock (sans accélération)
# Le système fonctionne en mode dégradé sans TFLite
```

### 5.3 Déploiement automatisé avec `deploy.sh`

Le script `rpi_service/deploy.sh` automatise l'intégralité du déploiement en 12 étapes :

```bash
sudo bash /home/pi/backend_robot/rpi_service/deploy.sh
```

**Étapes :**
1. Vérification Raspberry Pi 5
2. Installation packages système (Python, Redis, PostgreSQL, Nginx, V4L2, OpenCV, fail2ban, certbot)
3. Activation + démarrage Redis
4. Création base et utilisateur PostgreSQL (`dental_user` / `dental_db`)
5. Clonage ou mise à jour du backend depuis GitHub
6. Environnement virtuel Python + dépendances (avec fallback mock TFLite)
7. Création des dossiers (`captures/`, `uploads/`, `logs/`, `models/`)
8. Génération du fichier `.env` avec secrets aléatoires
9. Copie des 4 services systemd + activation
10. Migrations base de données + seed admin
11. Démarrage des services
12. Tests de vérification (API, Redis, caméra, Arduino)

### 5.4 Services systemd

4 services installés automatiquement par `deploy.sh` :

| Service | Fichier | Description |
|---------|---------|-------------|
| `dental-ai` | `rpi_service/systemd/dental-ai.service` | Orchestrateur RPi (5 threads, CPUAffinity 0-3) |
| `dental-flask` | `rpi_service/systemd/dental-flask.service` | API Flask (gunicorn 2 workers) |
| `dental-celery` | `rpi_service/systemd/dental-celery.service` | Celery worker (2 workers, 1000 taches max) |
| `dental-celery-beat` | `rpi_service/systemd/dental-celery-beat.service` | Celery beat scheduler |

**Fonctionnalités :**
- `dental-ai.service` : watchdog + CPU pinning + `SCHED_FIFO` (caméra 50, sensors 40) + restart policy 10s/5 tentatives
- `dental-flask.service` : migration Redis ping via `ExecStartPre`, workers synchrones `--preload`
- Tous les services : `ProtectSystem=full`, `PrivateTmp=true`, `NoNewPrivileges=true`

```bash
# Gestion des services
sudo systemctl status dental-ai
sudo journalctl -u dental-ai -f
sudo systemctl restart dental-ai
sudo systemctl stop dental-ai
```

### 5.5 Backend Flask + Celery sur RPi

```bash
# Option 1 : Directement sur le RPi (via systemd)
sudo systemctl start dental-flask
sudo systemctl start dental-celery
sudo systemctl start dental-celery-beat

# Option 2 : Docker (recommandé multi-serveur)
docker compose up -d
```

**Tâches Celery planifiées (beat) :**

| Tâche | Fréquence | Description |
|-------|-----------|-------------|
| `check_system_health` | Toutes les 5 min | Vérifie PostgreSQL, CPU, mémoire, température |
| `cleanup_old_data` | Chaque jour à 3h | Supprime sessions > 30 jours + logs santé > 7 jours |
| `backup_database` | Chaque dimanche à 4h | pg_dump → compression gzip → `/home/pi/backups/` |

### 5.6 Scripts utilitaires

| Fichier | Description |
|---------|-------------|
| `scripts/ssl-setup.sh` | Configuration Let's Encrypt + auto-renew crontab |
| `scripts/update.sh` | git pull → deps → migrations → restart services |
| `scripts/backup.sh` | Backup complet (DB + uploads + captures + config) + rétention 14 jours |

```bash
# SSL pour le domaine
sudo bash scripts/ssl-setup.sh api.dental-ai.com

# Mise à jour
sudo bash scripts/update.sh

# Sauvegarde manuelle
sudo bash scripts/backup.sh
```

Fail2ban est configuré avec 3 jails :
- `sshd` : 3 échecs → bannissement 24h
- `nginx-http-auth` : 5 échecs → 1h
- `dental-api` : 10 échecs en 5 min → 1h (filtre regex personnalisé)

### 5.7 Connexions matérielles

```
Arduino Uno R4 ←→ Raspberry Pi 5 (USB /dev/ttyACM0)
     │
     ├── Capteur pH salivaire     → A0 (analogique)
     ├── Capteur température       → A1 (analogique)
     ├── Capteur distance (HC-SR04)→ D2 (Trigger), D3 (Echo)
     ├── Capteur pression (FSR)    → A2 (analogique)
     ├── Capteur humidité          → A3 (analogique)
     └── Détecteur embout stérile  → D4 (digital, pull-up)

Caméra intra-orale USB ←→ Raspberry Pi 5 (USB /dev/video0)
```

### 5.8 Vérification du déploiement

```bash
# 1. Vérifier les services
sudo systemctl status dental-ai
sudo systemctl status dental-flask
sudo systemctl status dental-celery

# 2. Vérifier les logs
journalctl -u dental-ai -f
journalctl -u dental-flask -f

# 3. Vérifier Redis
redis-cli ping  # → PONG

# 4. Vérifier l'API
curl http://localhost:5000/api/health
# → {"status":"ok","message":"Dental AI Diagnostic API is running"}

curl http://localhost:5000/api/system/health -H "Authorization: Bearer <token>"
# → {"status":"healthy","components":{"postgresql":{"status":"ok"},"redis":{"status":"ok"},...}}

# 5. Vérifier les capteurs Arduino
cat /dev/ttyACM0 &
# → {"ph":6.8,"temp":36.2,"dist":12,"press":0.3,"hum":62,"tip":1}

# 6. Vérifier la caméra
v4l2-ctl --list-devices
# → devrait montrer la caméra intra-orale

# 7. Vérifier Flower (monitoring Celery)
# http://localhost:5555 (auth basic : voir nginx.conf)

# 8. Vérifier les backups
ls -la /home/pi/backups/
```

---

## 6. Docker Compose

### Services

```yaml
services:
  postgres:       # PostgreSQL 16 Alpine
  redis:          # Redis 7 Alpine
  backend:        # Flask + Gunicorn
  celery_worker:  # Celery worker (tâches async)
  celery_beat:    # Celery beat (tâches planifiées)
  flower:         # Monitoring Celery (port 5555)
  frontend:       # Next.js 15 standalone
  nginx:          # Reverse proxy + TLS + rate limiting
```

### Commandes utiles

```bash
# Tout lancer
cd backend_robot
docker compose up -d --build

# Voir les logs
docker compose logs -f backend
docker compose logs -f celery_worker

# Exécuter des commandes dans le conteneur
docker compose exec backend flask db upgrade
docker compose exec backend python manage.py create_admin

# Migrations
./scripts/migrate.sh backend init     # Initialiser
./scripts/migrate.sh backend migrate   # Nouvelle migration
./scripts/migrate.sh backend upgrade   # Appliquer

# Monitoring Celery
# Flower : http://localhost:5555 (auth basic définie dans nginx.conf)

# Accès via nginx
# API :  http://localhost/api/*
# Frontend : http://localhost/
# WebSocket : ws://localhost/socket.io/
#   Namespaces :
#     /camera        → flux vidéo, zoom, capture
#     /sensors       → données capteurs temps réel
#     /inference     → progression pipeline IA (7 étapes)
#     /notifications → push notifications utilisateur en temps réel
# Flower : http://localhost/flower/

# Arrêter
docker compose down
```

---

## 6.5 Schéma de la base de données

### Tables principales

#### `users`
| Colonne | Type | Contrainte |
|---------|------|------------|
| `id` | `INTEGER` | PK, auto-incrément |
| `email` | `VARCHAR(120)` | UNIQUE, NOT NULL |
| `full_name` | `VARCHAR(200)` | NOT NULL |
| `password_hash` | `VARCHAR(255)` | NOT NULL |
| `role` | `VARCHAR(20)` | NOT NULL (admin/dentiste/assistant) |
| `phone` | `VARCHAR(20)` | nullable |
| `is_active` | `BOOLEAN` | default TRUE |
| `last_login_at` | `DATETIME` | nullable |
| `created_at` | `DATETIME` | default now() |
| `updated_at` | `DATETIME` | default now() |

#### `patients`
| Colonne | Type | Contrainte |
|---------|------|------------|
| `id` | `INTEGER` | PK, auto-incrément |
| `nom` | `VARCHAR(100)` | NOT NULL |
| `prenom` | `VARCHAR(100)` | NOT NULL |
| `date_naissance` | `DATE` | NOT NULL |
| `genre` | `VARCHAR(10)` | nullable |
| `telephone` | `VARCHAR(20)` | nullable |
| `email` | `VARCHAR(120)` | nullable |
| `adresse` | `TEXT` | nullable |
| `created_by` | `INTEGER` | FK → users.id |

#### `diagnostics`
| Colonne | Type | Contrainte |
|---------|------|------------|
| `id` | `INTEGER` | PK, auto-incrément |
| `patient_id` | `INTEGER` | FK → patients.id |
| `dentiste_id` | `INTEGER` | FK → users.id |
| `description` | `TEXT` | nullable |
| `status` | `VARCHAR(20)` | default 'en_attente' |
| `maladie` | `VARCHAR(64)` | nullable |
| `confidence_score` | `FLOAT` | nullable |
| `ai_analysis_json` | `JSON` | nullable |

### Tables de scan (acquisition temps réel)

#### `scan_sessions`
| Colonne | Type | Contrainte |
|---------|------|------------|
| `id` | `INTEGER` | PK, auto-incrément |
| `patient_id` | `INTEGER` | FK → patients.id, INDEX |
| `user_id` | `INTEGER` | FK → users.id, INDEX |
| `status` | `VARCHAR(20)` | INDEX, default 'active' |
| `started_at` | `DATETIME` | default now() |
| `ended_at` | `DATETIME` | nullable |
| `raspberry_pi_id` | `VARCHAR(64)` | nullable |

Une session de scan regroupe plusieurs captures successives.

#### `scan_captures`
| Colonne | Type | Contrainte |
|---------|------|------------|
| `id` | `INTEGER` | PK, auto-incrément |
| `session_id` | `INTEGER` | FK → scan_sessions.id, INDEX |
| `original_path` | `VARCHAR(512)` | NOT NULL |
| `enhanced_path` | `VARCHAR(512)` | nullable |
| `thumbnail_path` | `VARCHAR(512)` | nullable |
| `quality_score` | `FLOAT` | INDEX, default 0.0 |
| `quality_blur` | `FLOAT` | default 0.0 |
| `quality_brightness` | `FLOAT` | default 0.0 |
| `quality_contrast` | `FLOAT` | default 0.0 |
| `quality_noise` | `FLOAT` | default 0.0 |
| `quality_motion` | `FLOAT` | default 0.0 |
| `quality_glare` | `FLOAT` | default 0.0 |
| `width` | `INTEGER` | default 0 |
| `height` | `INTEGER` | default 0 |
| `captured_at` | `DATETIME` | default now() |

Les 6 critères de qualité (blur, brightness, contrast, noise, motion, glare) sont stockés individuellement pour analyse historique et traçabilité.

#### `scan_results`
| Colonne | Type | Contrainte |
|---------|------|------------|
| `id` | `INTEGER` | PK, auto-incrément |
| `capture_id` | `INTEGER` | FK → scan_captures.id, UNIQUE |
| `maladie` | `VARCHAR(64)` | nullable |
| `confidence` | `FLOAT` | default 0.0 |
| `all_scores` | `JSON` | tous les scores par maladie |
| `model_version` | `VARCHAR(64)` | nullable |
| `analysis_time_ms` | `INTEGER` | default 0 |
| `risk_score` | `JSON` | score de risque global + composants |
| `xai_data` | `JSON` | explicabilité (features, heatmap) |
| `history_data` | `JSON` | historique patient |
| `recommendations_data` | `JSON` | recommandations |
| `sensor_snapshot` | `JSON` | état capteurs au moment capture |
| `created_at` | `DATETIME` | default now() |

#### `system_health_log` (monitoring watchdog)
| Colonne | Type | Contrainte |
|---------|------|------------|
| `id` | `INTEGER` | PK, auto-incrément |
| `component` | `VARCHAR(64)` | NOT NULL, INDEX |
| `status` | `VARCHAR(20)` | NOT NULL (ok/warning/critical/error/disconnected) |
| `cpu_percent` | `FLOAT` | nullable |
| `temp_celsius` | `FLOAT` | nullable |
| `mem_percent` | `FLOAT` | nullable |
| `disk_free_bytes` | `BIGINT` | nullable |
| `fps_current` | `FLOAT` | nullable |
| `inference_time_ms` | `FLOAT` | nullable |
| `error_message` | `TEXT` | nullable |
| `checked_at` | `DATETIME` | NOT NULL, INDEX, default now() |

### Relations

```
scan_sessions 1───* scan_captures 1───1 scan_results
system_health_log (indépendant — logs de monitoring)
```

- `scan_sessions` → *`scan_captures`* : une session contient N captures
- *`scan_captures`* → `scan_results` : une capture a 0 ou 1 résultat d'analyse
- `scan_results.capture_id` est UNIQUE (relation 1:1)
- Toutes les FK ont `ON DELETE CASCADE`

### Indexation

| Table | Index | Colonne | Type |
|-------|-------|---------|------|
| `scan_sessions` | `ix_scan_sessions_patient_id` | `patient_id` | B-tree |
| `scan_sessions` | `ix_scan_sessions_user_id` | `user_id` | B-tree |
| `scan_sessions` | `ix_scan_sessions_status` | `status` | B-tree |
| `scan_captures` | `ix_scan_captures_session_id` | `session_id` | B-tree |
| `scan_captures` | `ix_scan_captures_quality_score` | `quality_score` | B-tree |
| `scan_results` | `ix_scan_results_capture_id` | `capture_id` | UNIQUE B-tree |
| `system_health_log` | `ix_system_health_log_component` | `component` | B-tree |
| `system_health_log` | `ix_system_health_log_checked_at` | `checked_at` | B-tree |

### Migrations disponibles

| Fichier | Description |
|---------|-------------|
| `a08e3e9e3524_initial_migration.py` | Tables initiales (users, patients, diagnostics, monitoring) |
| `b2c4f8e6d0a2_add_scan_session_models.py` | Tables scan (sessions, captures, results avec FK cascade + indexes) |
| `e7f1a2b3c4d5_add_system_health_log.py` | Table system_health_log (monitoring watchdog + indexes) |

### Commandes

```bash
# Générer une nouvelle migration après modification des modèles
docker compose exec backend flask db migrate -m "description"

# Appliquer les migrations
docker compose exec backend flask db upgrade

# Annuler la dernière migration
docker compose exec backend flask db downgrade

# Voir l'historique
docker compose exec backend flask db history
```

---

## 7. Référence API

### Authentification

```
POST   /api/auth/login              # Connexion → JWT
POST   /api/auth/refresh            # Rafraîchir token
POST   /api/auth/logout             # Déconnexion
GET    /api/auth/me                  # Profil utilisateur
```

### Patients

```
GET    /api/patients                 # Liste paginée
POST   /api/patients                 # Créer
GET    /api/patients/:id             # Détail
PUT    /api/patients/:id             # Modifier
DELETE /api/patients/:id             # Supprimer
GET    /api/patients/:id/medical-record  # Dossier médical
PUT    /api/patients/:id/medical-record  # Modifier dossier
```

### Diagnostics

```
GET    /api/diagnostics              # Liste paginée
POST   /api/diagnostics              # Créer
GET    /api/diagnostics/:id          # Détail
PUT    /api/diagnostics/:id          # Modifier
POST   /api/diagnostics/:id/analyze  # Lancer analyse IA
GET    /api/diagnostics/stats        # Statistiques
```

### Scan

```
GET    /api/hardware/status                   # État du matériel
GET    /api/scan/check-conditions             # Vérifications pré-scan → { checks: CheckCondition[], all_passed, timestamp }
POST   /api/scan/session/start                # Démarrer session → ScanSession
GET    /api/scan/session/:id                  # Récupérer une session
POST   /api/scan/session/:id/complete         # Terminer une session
POST   /api/scan/session/:id/cancel           # Annuler une session
POST   /api/scan/capture                      # Capturer image → ScanCapture
POST   /api/scan/analyze/:capture_id          # Analyser image → { result: ScanResult }
GET    /api/scan/result/:capture_id           # Résultat IA d'une capture
GET    /api/scan/progress/:session_id         # Progression session
GET    /api/scan/progress/capture/:capture_id # Progression d'une capture
```

### Capteurs (Monitoring)

```
GET    /api/monitoring/current       # Dernières mesures
GET    /api/monitoring/history       # Historique
POST   /api/monitoring/record        # Enregistrer mesure
POST   /api/monitoring/session/start # Démarrer session
POST   /api/monitoring/session/stop  # Arrêter session
GET    /api/monitoring/stats         # Statistiques
```

### Rapports

```
GET    /api/reports                  # Liste rapports
GET    /api/reports/:id              # Détail rapport
POST   /api/reports/generate         # Générer PDF
GET    /api/reports/:id/download     # Télécharger PDF
```

### Images

```
POST   /api/images/upload            # Upload image
GET    /api/images/diagnostic/:id    # Images d'un diagnostic
GET    /api/images/:id               # Récupérer image
```

### Notifications

```
GET    /api/notifications            # Liste notifications
GET    /api/notifications/unread-count  # Nombre non lues
PATCH  /api/notifications/:id/read   # Marquer comme lue
PATCH  /api/notifications/read-all   # Tout marquer lu
DELETE /api/notifications/:id        # Supprimer
```

### Dashboard

```
GET    /api/dashboard/stats          # Statistiques agrégées
```

### Audit

```
GET    /api/audit                    # Journal d'audit
```

### Système (Monitoring interne)

```
GET    /api/system/health            # État global des composants (postgres, redis, cpu, mem, temp)
GET    /api/system/health/detail     # Derniers 60 logs de santé détaillés
GET    /api/system/monitoring        # Logs paginés (filtres : component, status)
POST   /api/system/cleanup           # Nettoyage manuel (sessions > 30j, logs > 7j) [admin]
```

Ces endpoints sont sécurisés par JWT. Les routes système sont protégées par rôle admin. Le `/api/system/health` est accessible à tout utilisateur authentifié pour le monitoring temps réel.

### API Documentation Interactive (Swagger)

Le frontend embarque une page **Documentation API** à l'URL `/dashboard/docs` qui affiche Swagger UI via une iframe pointant vers `/apidocs/` (Flasgger côté backend).

**Fonctionnement :**
- **Backend** : Flasgger sert l'UI Swagger sur `http://<backend>:5000/apidocs/`
- **Nginx** : La location `/apidocs/` proxyfie vers le backend (ajoutée aux 2 server blocks)
- **Frontend** : L'iframe utilise `NEXT_PUBLIC_APIDOCS_URL` (variable d'env, défaut `http://localhost:5000/apidocs/` en dev)
- **Sécurité** : `X-Frame-Options` est supprimé pour `/apidocs/` via nginx ; Flask fait de même dans le middleware `SecurityMiddleware`
- **CSP** : Le Content-Security-Policy de nginx autorise `frame-src 'self'` pour le chargement de l'iframe

**Configuration :**
```bash
# .env.local (frontend)
NEXT_PUBLIC_APIDOCS_URL=/apidocs/   # via nginx (production)
NEXT_PUBLIC_APIDOCS_URL=http://localhost:5000/apidocs/  # direct backend (développement)
```

### Notifications temps réel

Le système de notifications est entièrement dynamique et temps réel.

**Architecture :**

```
Analyse IA terminée (scan/routes.py)
  │
  ▼
NotificationService.create()
  ├── Enregistre en BDD (table `notifications`)
  └── Emet WebSocket → emit_notification(user_id, payload)
                           │
                           ▼
                  NotificationNamespace("/notifications")
                           │
                  socketio.emit("new_notification", ..., room=f"notif_{user_id}")
                           │
                           ▼ (WebSocket)
                  useNotificationSocket() (frontend)
                           │
                           ▼
                  notification.store → addRealtime()
                           │
                  ┌─────────┴──────────┐
                  ▼                    ▼
            Header (badge +1)   NotificationPanel (liste)
```

**Backend :**
- **Modèle** : `Notification` (user_id, type, title, message, link, is_read, created_at)
- **Service** : `NotificationService` (create, get_unread_count, get_user_notifications, mark_read, mark_all_read, delete)
- **API REST** : 5 endpoints sous `/api/notifications`
- **WebSocket** : Namespace `/notifications` — chaque utilisateur rejoint `notif_{user_id}` à la connexion
- **Types** : `diagnostic_created`, `diagnostic_analyzed`, `report_ready`, `system_alert`, `appointment_reminder`
- **Création automatique** : la route `POST /api/scan/analyze/:id` crée une notification `diagnostic_analyzed` après chaque analyse

**Frontend :**
| Fichier | Rôle |
|---------|------|
| `lib/api/notifications.api.ts` | Client Axios (list, unread-count, markRead, markAllRead, delete) |
| `stores/notification.store.ts` | Store Zustand (items, unreadCount, fetch poll 30s, addRealtime) |
| `hooks/use-notification-socket.ts` | Connexion WebSocket `/notifications` avec token JWT |
| `components/layout/notification-panel.tsx` | Dropdown : liste, marquer lu, supprimer, lien, date relative |
| `components/layout/header.tsx` | Badge dynamique, ouvre le panel au clic |
| `app/dashboard/notifications/page.tsx` | Page complète avec pagination, actions batch |

**Environnement :**
```bash
# .env.local (frontend)
NEXT_PUBLIC_WS_URL=http://localhost:5000  # URL de base Socket.IO (défaut)
```

---

## 8. Dépannage

### Problèmes courants

| Problème | Cause | Solution |
|----------|-------|----------|
| `SECRET_KEY` not set | .env manquant | Créer `.env` avec les secrets |
| Caméra non détectée | USB débranché | `v4l2-ctl --list-devices` |
| Arduino injoignable | Mauvais port | `ls /dev/tty*` |
| JWT invalide | Token expiré | Rafraîchir via `/auth/refresh` |
| 429 Too Many Requests | Rate limit | Attendre 1 minute |
| 500 Internal Error | Erreur serveur | Vérifier `docker compose logs backend` |
| Documentation API vide (iframe blanc) | URL hardcodée `localhost:5000` inaccessible ou CSP/X-Frame-Options bloque | Vérifier que `NEXT_PUBLIC_APIDOCS_URL` pointe vers `/apidocs/` via nginx ; s'assurer que nginx a la location `/apidocs/` et que `X-Frame-Options` est levé | |
| WebSocket déconnecté | Proxy manquant | Vérifier Nginx / CORS |
| Notifications non reçues en temps réel | WebSocket `/notifications` déconnecté | Vérifier que `NEXT_PUBLIC_WS_URL` est correct et que le token JWT est valide ; `docker compose logs backend` pour voir la connexion |
| Image qualité < 60 | Mauvaise luminosité | Ajuster éclairage / position |
| TFLite non chargé | Modèle manquant | Vérifier `models/multimodal_model.tflite` et `models/unet_model.tflite` |
| Service systemd en échec | Dépendance manquante | `journalctl -u dental-ai -f` pour le diagnostic |
| Celery beat ne tourne pas | Redis injoignable | `systemctl status dental-celery-beat` |
| Backup échoue | pg_dump non installé | `sudo apt install postgresql-client` |
| Certbot SSL échec | Domaine non résolu | Vérifier DNS A record → IP du RPi |

### Logs

```bash
# Backend
docker compose logs -f --tail=50 backend

# Celery
docker compose logs -f celery_worker

# RPi Service
journalctl -u dental-ai.service -f

# PostgreSQL
docker compose logs postgres

# Application Flask
cat logs/app.log
```

### Reset complet

```bash
# Réinitialiser la base
docker compose down -v
docker compose up -d postgres redis
docker compose exec backend flask db upgrade
docker compose exec backend python manage.py create_admin

# Réinitialiser le RPi
sudo systemctl stop dental-ai.service
rm -rf /home/pi/captures/*
sudo systemctl start dental-ai.service
```

---

## Licence

Projet développé par Laura Juni.
Système de diagnostic assisté par IA — Aide à la décision médicale.
Le praticien reste seul responsable du diagnostic final.
