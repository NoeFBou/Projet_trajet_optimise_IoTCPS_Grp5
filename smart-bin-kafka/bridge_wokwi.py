from flask import Flask, request
from kafka import KafkaProducer
import json
import time

app = Flask(__name__)

# Config Kafka
TOPIC_NAME = "bin-sensor-data"

KAFKA_SERVER = "localhost:9092" 

try:
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_SERVER,
        value_serializer=lambda x: json.dumps(x).encode('utf-8')
    )
    print(" Connecté à Kafka !")
except Exception as e:
    print(f" Erreur Kafka : {e}")

@app.route('/data', methods=['POST'])
def receive_data():
    try:
        # 1. Recevoir le JSON de Wokwi
        data = request.json
        
        # Ajouter le timestamp (car l'ESP32 n'a pas forcément la bonne heure)
        data['timestamp'] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Ajouter la position GPS (simulée car pas de GPS dans Wokwi simple)
        data['GPS_value'] = {"lat": 43.6158, "lon": 7.0722}

        print(f" Reçu de Wokwi : ID={data['bin_id']}")

        # 2. Envoyer à Kafka
        producer.send(TOPIC_NAME, value=data)
        producer.flush()
        
        return "OK", 200
    except Exception as e:
        print(f"Erreur : {e}")
        return "Error", 500

if __name__ == '__main__':
    # On écoute sur 0.0.0.0 pour être accessible par le Wokwi Gateway
    app.run(host='0.0.0.0', port=5000)
