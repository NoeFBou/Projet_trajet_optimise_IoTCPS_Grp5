import time
import json
import random
from kafka import KafkaProducer


TOPIC_NAME = "bin-sensor-data"

KAFKA_SERVER = "localhost:9092"

print(f" Tentative de connexion à Kafka sur {KAFKA_SERVER}...")


try:
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_SERVER,
        value_serializer=lambda x: json.dumps(x).encode('utf-8')
    )
    print(" Connecté à Kafka avec succès !")
except Exception as e:
    print(f" Erreur de connexion : {e}")
    exit()

def generer_donnees_poubelle(bin_id):
    """Génère des fausses données capteurs"""
    us_value = random.randint(10, 100) 
    weight = random.uniform(0, 20.0)
    
    data = {
        "bin_id": bin_id,
        "bin_type": "Verre",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "GPS_value": {"lat": 43.6158, "lon": 7.0722},
        "sensors": [
            {"id": "US-01", "type": "ultrasonic", "value": us_value, "unit": "cm"},
            {"type": "load_cell", "value": round(weight, 2), "unit": "kg"}
        ],
        "status": {"battery_level": random.randint(50, 100)}
    }
    return data

print("Démarrage de l'envoi des données...")

try:
    while True:
        message = generer_donnees_poubelle("PBL-SOPH-12A7")
        
       
        producer.send(TOPIC_NAME, value=message)
        producer.flush() 
        
        print(f" Donnée envoyée : Niveau {message['sensors'][0]['value']}cm, Poids {message['sensors'][1]['value']}kg")
        
        
        time.sleep(5)

except KeyboardInterrupt:
    print("\nArrêt de la simulation.")
    producer.close()
