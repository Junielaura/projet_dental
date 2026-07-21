# Plan de déploiement — Backend sur Raspberry Pi 4, Frontend sur Ubuntu

## Objectif
- Backend complet (Flask + PostgreSQL + Redis + Celery) déployé sur le RPi 4
- Frontend (Next.js) reste sur la machine Ubuntu
- Communication réseau LAN entre les deux
- Ajout Portainer + Adminer pour pilotage visuel
- Intégration modèle TFLite + capteurs Arduino

---

## Architecture cible

```
Ubuntu (machine utilisateur)                      Raspberry Pi 4
──────────────────────────────                    ─────────────────────────
                                                     ┌──────────────┐
                                                     │   Portainer  │ :9000
                                                     │   Adminer    │ :8080
                                                     │   Flower     │ :5555
                                                     └──────────────┘
Frontend Next.js :3000                                       │
  │                                                        │
  │ HTTP / WS vers http://<RPI_IP>:5000                    │
  │                                                        │
  └───────────────────── LAN ───────────────────── ────────┤
                                                     ┌──────┴──────────────┐
                                                     │   Nginx (optionnel) │
                                                     │   :80 → backend:5000│
                                                     └──────┬──────────────┘
                                                     ┌──────┴──────────────┐
                                                     │   Flask Backend     │
                                                     │   :5000             │
                                                     ├─────────────────────┤
                                                     │   PostgreSQL :5432  │
                                                     │   Redis :6379       │
                                                     │   Celery Worker     │
                                                     │   Celery Beat       │
                                                     ├─────────────────────┤
                                                     │   Model TFLite      │
                                                     │   dentalnet_v2...   │
                                                     ├─────────────────────┤
                                                     │   Arduino Uno R4    │
                                                     │   /dev/ttyACM0      │
                                                     │   pH, temp, dist    │
                                                     └─────────────────────┘
```

---

## Étape 1 — Préparer le Raspberry Pi 4

```bash
# 1.1 Mise à jour système
sudo apt update && sudo apt upgrade -y

# 1.2 Installer Docker + Compose
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
newgrp docker

# 1.3 Installer les outils système
sudo apt install -y git v4l-utils python3-pip python3-venv
```

---

## Étape 2 — Cloner le backend sur le RPi

```bash
cd /home/pi
git clone https://github.com/Junielaura/backend_robot.git
cd backend_robot

# Créer le .env avec des secrets forts
cp .env.example .env
# → Éditer SECRET_KEY et JWT_SECRET_KEY

# Créer les dossiers de données
mkdir -p uploads logs
```

---

## Étape 3 — Modifier docker-compose.yml (SANS frontend ni nginx)

> **Fichier à modifier** : `backend_robot/docker-compose.yml`

### 3.1 Supprimer les services inutiles sur le RPi
Retirer du fichier :
- `frontend:` (tout le bloc, lignes 120-134)
- `nginx:` (tout le bloc, lignes 136-149)
- L'upstream `frontend` dans `nginx.conf` (mais nginx n'est pas déployé)

### 3.2 Ajouter Portainer + Adminer
Ajouter après `flower:`:

```yaml
  portainer:
    image: portainer/portainer-ce:latest
    container_name: dental-portainer
    ports:
      - "9000:9000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - portainer_data:/data
    restart: unless-stopped

  adminer:
    image: adminer:latest
    container_name: dental-adminer
    ports:
      - "8080:8080"
    restart: unless-stopped
```

Ajouter dans `volumes:` :
```yaml
  portainer_data:
```

### 3.3 Modifier CORS_ORIGINS
Remplacer dans les 3 blocs (backend, celery_worker, celery_beat) :
```yaml
CORS_ORIGINS: http://localhost:3000
```
par :
```yaml
CORS_ORIGINS: http://<IP_UBUNTU>:3000
```
(ou `CORS_ORIGINS: http://localhost:3000,http://<IP_UBUNTU>:3000`)

### 3.4 Monter le port série Arduino
Ajouter dans le service `backend:` :
```yaml
    devices:
      - /dev/ttyACM0:/dev/ttyACM0
```

---

## Étape 4 — Configurer la communication Frontend ↔ Backend

### 4.1 Sur Ubuntu (frontend)
Modifier `frontend_robot/.env.local` :

```ini
NEXT_PUBLIC_API_URL=http://<IP_RPI>:5000/api
NEXT_PUBLIC_WS_URL=http://<IP_RPI>:5000
NEXT_PUBLIC_APIDOCS_URL=http://<IP_RPI>:5000/apidocs/
```

> Remplacer `<IP_RPI>` par l'adresse IP du Raspberry Pi sur le réseau local (ex: `192.168.1.42`)

### 4.2 Sur le RPi (backend)
Modifier la variable `CORS_ORIGINS` dans `docker-compose.yml` :

```yaml
CORS_ORIGINS: http://<IP_UBUNTU>:3000
```

> Remplacer `<IP_UBUNTU>` par l'adresse IP de la machine Ubuntu.

Pour supporter à la fois le développement et la production :
```yaml
CORS_ORIGINS: http://localhost:3000,http://<IP_UBUNTU>:3000
```

---

## Étape 5 — Démarrer le backend sur le RPi

```bash
cd /home/pi/backend_robot
docker compose up -d

# Vérifier que tous les conteneurs tournent
docker compose ps

# Voir les logs
docker compose logs -f --tail=50 backend

# Appliquer les migrations DB
docker compose exec backend flask db upgrade

# Créer l'admin
docker compose exec backend python manage.py create_admin
```

### Accès aux interfaces :
| Service     | URL                          |
|-------------|------------------------------|
| Portainer   | `http://<IP_RPI>:9000`       |
| Adminer     | `http://<IP_RPI>:8080`       |
| Flower      | `http://<IP_RPI>:5555`       |
| API         | `http://<IP_RPI>:5000/api`   |
| Swagger     | `http://<IP_RPI>:5000/apidocs` |

---

## Étape 6 — Intégrer le modèle TensorFlow Lite

```bash
# 6.1 Créer le dossier models
mkdir -p /home/pi/backend_robot/rpi_service/models

# 6.2 Copier ton modèle .tflite
# Depuis ta machine, scp le fichier vers le RPi :
scp rpi_service/models/multimodal_model.tflite pi@<IP_RPI>:/home/pi/dental/rpi_service/models/
scp rpi_service/models/unet_model.tflite pi@<IP_RPI>:/home/pi/dental/rpi_service/models/
```

### 6.3 Vérifier/modifier la configuration du modèle

**Fichier** : `rpi_service/tf_inference.py`

| Paramètre | Ligne | Valeur par défaut | À adapter selon ton modèle |
|-----------|-------|-------------------|-----------------------------|
| `TFLITE_MODEL` | 38 | `multimodal_model.tflite` | Nom de ton fichier `.tflite` |
| `CLASS_LABELS` | 16 | `["Healthy", "Caries", "Gingivitis", "Periodontitis"]` | Tes classes |
| Taille entrée | 106 | 224×224 | La taille attendue par ton modèle |
| Normalisation | 108 | `(img / 127.5) - 1.0` → [-1, 1] | `img / 255.0` si [0, 1] |

Si le modèle est directement utilisé par le backend Flask (et non par le RPi service), le chemin est :
**Fichier** : `backend_robot/app/services/ai_service.py`
- Ligne 27 : `self.model = tf.keras.models.load_model('model/dental_ai_model.h5')`
- Le dossier `backend_robot/model/` doit contenir le fichier

---

## Étape 7 — Intégrer les capteurs Arduino

### 7.1 Connexion physique
```
Arduino Uno R4 ←→ Capteur pH (broche analogique A0)
                ←→ Capteur température DS18B20 (broche digitale D2)
                ←→ Capteur distance HC-SR04 (broches D3/D4)
                ←→ USB vers Raspberry Pi (/dev/ttyACM0)
```

### 7.2 Arduino : format attendu
Le `sensor_manager.py` (RPi) s'attend à recevoir du JSON ligne par ligne sur le port série :

```json
{"ph":7.2,"temp":25.3,"dist":12.5,"press":1013.2,"hum":45.0,"tip":1,"seq":42,"cs":127}
```

Ton code Arduino doit envoyer ce JSON à chaque lecture de capteurs (toutes les ~1s).

### 7.3 Programme Arduino minimal
```cpp
void setup() {
  Serial.begin(115200);
}

void loop() {
  float ph = readPH();          // Ta fonction lecture pH
  float temp = readTemperature(); // Ta fonction lecture température
  float dist = readDistance();   // Ta fonction lecture distance
  float hum = readHumidity();    // optionnel

  // Checksum = XOR des valeurs * 100
  int cs = (int)(ph*100) ^ (int)(temp*100) ^ (int)(dist*100) ^ (int)(hum*100);

  Serial.print("{\"ph\":");
  Serial.print(ph, 2);
  Serial.print(",\"temp\":");
  Serial.print(temp, 2);
  Serial.print(",\"dist\":");
  Serial.print(dist, 2);
  Serial.print(",\"hum\":");
  Serial.print(hum, 2);
  Serial.print(",\"tip\":1");
  Serial.print(",\"seq\":");
  Serial.print(millis());
  Serial.print(",\"cs\":");
  Serial.print(cs & 0xFF);
  Serial.println("}");

  delay(1000);
}
```

### 7.4 Vérifier la communication
```bash
# Sur le RPi, tester la lecture série
screen /dev/ttyACM0 115200
# Tu dois voir les lignes JSON défiler

# Vérifier que le service backend voit l'Arduino
curl http://localhost:5000/api/hardware/status
# → arduino: "connected"
```

### 7.5 Si le port série est différent
Modifier dans `.env` :
```ini
RPI_SERIAL_PORT=/dev/ttyUSB0   # si c'est USB, pas ACM
RPI_BAUD_RATE=115200
```

---

## Étape 8 — Vérification finale

```bash
# 8.1 Sur le RPi : tous les conteneurs actifs ?
docker compose ps
# → dental-db, dental-redis, dental-backend, dental-celery-worker,
#   dental-celery-beat, dental-flower, dental-portainer, dental-adminer

# 8.2 API accessible ?
curl http://localhost:5000/api/system/health

# 8.3 DB accessible ?
curl http://localhost:8080  (Adminer)
# → login: postgres, user: dental_user, password: dental_pass, db: dental_db

# 8.4 Sur Ubuntu : le frontend communique ?
# 1. Lancer le frontend : npm run dev
# 2. Login sur http://localhost:3000
# 3. Vérifier que les données viennent bien du RPi

# 8.5 Test complet scan + IA
# Lancer un nouveau diagnostic sur le frontend
# Vérifier que l'analyse IA + les capteurs remontent
```

---

## Modifications de fichiers résumé

| Fichier | Ce qui change |
|---------|---------------|
| `backend_robot/docker-compose.yml` | Supprimer frontend/nginx ; ajouter Portainer + Adminer ; modifier `CORS_ORIGINS` ; ajouter `devices: [/dev/ttyACM0]` |
| `backend_robot/.env` | `SECRET_KEY`, `JWT_SECRET_KEY` forts |
| `frontend_robot/.env.local` | `NEXT_PUBLIC_API_URL=http://<IP_RPI>:5000/api` |
| `backend_robot/rpi_service/tf_inference.py` | Labels, taille entrée, normalisation selon modèle |
| `backend_robot/rpi_service/models/` | Copier le fichier `.tflite` |

---

## Sécurité

- Change les mots de passe PostgreSQL par défaut dans le `.env`
- Ne pas exposer le port 5000 directement sur Internet (utiliser un VPN ou SSH tunnel)
- Portainer expose l'API Docker — ne le laisse pas ouvert sans auth
- Pour un accès distant, préférer un tunnel SSH :
  ```bash
  ssh -L 5000:localhost:5000 pi@<IP_RPI>
  ```
  Puis le frontend local utilise `http://localhost:5000`
