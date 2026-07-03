# Dental AI Diagnostic

Système intelligent de diagnostic dentaire avec IA.

## Structure

```
projet_dental/
├── backend_robot/   → Application Flask (API REST)
└── frontend_robot/  → Application Next.js (Interface utilisateur)
```

## Démarrage rapide

```bash
# Cloner avec les sous-modules
git clone --recursive https://github.com/Junielaura/projet_dental.git

# Lancer le backend
cd projet_dental/backend_robot
docker compose up -d

# Lancer le frontend
cd ../frontend_robot
npm install && npm run dev
```
