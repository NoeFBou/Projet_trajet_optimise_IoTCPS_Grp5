FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt



CMD ["python", "filter_service.py"]