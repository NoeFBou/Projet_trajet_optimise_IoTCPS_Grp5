import json
import time
import paho.mqtt.client as mqtt
from kafka import KafkaProducer

MQTT_BROKER = "mosquitto"
KAFKA_SERVER = "kafka:29092"
MQTT_TOPIC = "bin/filtered_data"
KAFKA_TOPIC = "bin-sensor-data"

# Init Kafka Producer
producer = None
while producer is None:
    try:
        print("Tentative de connexion à Kafka...")
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_SERVER,
            value_serializer=lambda x: json.dumps(x).encode('utf-8')
        )
        print("[Service 3] Kafka Producer Connecté !")
    except Exception as e:
        print(f"Kafka pas encore prêt ({e}). Nouvelle tentative dans 5s...")
        time.sleep(5)

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        
        # Envoi vers Kafka
        producer.send(KAFKA_TOPIC, value=data)
        producer.flush()
        print(f"[Bridge] {data['bin_id']} -> Kafka")
        
    except Exception as e:
        print(f"Erreur transfert: {e}")

# --- BOUCLE PRINCIPALE CORRIGÉE ---
client = mqtt.Client()

# !!! C'EST ICI QU'IL FALLAIT AJOUTER CETTE LIGNE !!!
client.on_message = on_message 

client.connect(MQTT_BROKER, 1883, 60)
client.subscribe(MQTT_TOPIC)
print("[Service 3] Bridge MQTT->Kafka démarré")
client.loop_forever()
