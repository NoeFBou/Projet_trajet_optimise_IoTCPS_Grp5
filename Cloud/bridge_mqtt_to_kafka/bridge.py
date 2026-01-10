import json
import time
import os
import paho.mqtt.client as mqtt
from kafka import KafkaProducer

MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto-fog")
MQTT_TOPIC_IN = "city/bin"
KAFKA_SERVER = os.getenv("KAFKA_SERVER", "kafka:29092")
KAFKA_TOPIC_FULL = "bin-data"

producer = None

# Retry loop for Kafka connection
while producer is None:
    try:
        print(f"[Bridge] Connecting to Kafka ({KAFKA_SERVER})...", flush=True)
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_SERVER,
            value_serializer=lambda x: json.dumps(x).encode('utf-8') # To transform python dict to JSON
        )
        print("[Bridge Mqtt-to-Kafka] Kafka Connected!")
    except Exception:
        print("[Bridge Mqtt-to-Kafka Error] Kafka not ready. Retrying in 5s...")
        time.sleep(5)

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[Bridge Mqtt-to-Kafka] Subscribed to MQTT")
        client.subscribe(MQTT_TOPIC_IN, qos=2)
    else:
        print(f"[Bridge Mqtt-to-Kafka Error] Subscription Failed: {reason_code}")

def on_message(client, userdata, msg):
    print(f"[Bridge Mqtt-to-Kafka]Message received on topic {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode())
        bin_id = payload.get("bin_id", "unknown")

        # Send Full Data (Historic)
        producer.send(KAFKA_TOPIC_FULL, value=payload)


        producer.flush()
        print(f"[Bridge Mqtt-to-Kafka] Sent {bin_id} state to Kafka.")
    except Exception as e:
        print(f"[Bridge Mqtt-to-Kafka Error] {e}")

if __name__ == "__main__":
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"[Bridge] Connecting to MQTT...")
    while True:
        try:
            client.connect(MQTT_BROKER, 1884, 900)
            break
        except:
            time.sleep(5)

    client.loop_forever()