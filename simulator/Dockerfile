# simulator/Dockerfile
FROM python:3.9-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Si tu as un requirements.txt, décommente les lignes suivantes
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# Sinon on installe juste mqtt qui est souvent utilisé pour les capteurs
RUN pip install paho-mqtt

COPY . .

CMD ["python", "1_sensor_raw.py"]