# service aggregator/Dockerfile
FROM python:3.9-slim

WORKDIR /app

# On désactive le buffer pour voir les logs en temps réel
ENV PYTHONUNBUFFERED=1

# Installation des dépendances
# Je suppose que tu as besoin de kafka et requests vu ton docker-compose
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "service_aggregator.py"]