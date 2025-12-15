# Image Python légère
FROM python:3.9-slim

# Dossier de travail
WORKDIR /app

# Installation des dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie de tous les scripts
COPY . .

# Force l'affichage des logs en temps réel (indispensable pour Docker)
ENV PYTHONUNBUFFERED=1
