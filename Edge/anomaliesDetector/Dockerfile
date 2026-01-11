# Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Create app directory
WORKDIR /app
COPY anomaly.py /app/anomaly.py
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 5000

CMD ["python", "anomaly.py"]
