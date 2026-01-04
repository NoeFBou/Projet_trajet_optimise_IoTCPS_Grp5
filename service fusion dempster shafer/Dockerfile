FROM python:3.9-slim

WORKDIR /app

# On copie les requirements D'ABORD pour profiter du cache Docker
COPY requirements.txt .

# On installe les librairies
RUN pip install --no-cache-dir -r requirements.txt

# Ensuite on copie le reste du code
COPY . .

# Commande de lancement (adaptée à ce que je vois dans tes logs)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]