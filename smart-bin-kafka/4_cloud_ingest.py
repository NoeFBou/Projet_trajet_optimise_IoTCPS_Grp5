import json
import time
from kafka import KafkaConsumer
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Config
TOPIC = "bin-sensor-data"
KAFKA_SERVER = "kafka:29092"
INFLUX_URL = "http://influxdb:8086"
INFLUX_TOKEN = "5QsGX__Bqx0w46bWf6rX2zP_oWHthKsmq5Z7re-3FsBM-gsy5OoO2VPCFS1HGSknV_Yy7uPPsH_9iqbZs-_kYw=="
INFLUX_ORG = "smartcity"
INFLUX_BUCKET = "bins"

# Connexion InfluxDB
try:
    db_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)
    print("[Service 4] InfluxDB Connecté")
except Exception as e:
    print(f"Erreur InfluxDB: {e}")
    exit()

# Connexion Kafka Consumer
print("Connexion Kafka...")
consumer = None
while consumer is None:
    try:
        print("Tentative de connexion au Topic Kafka...")
        consumer = KafkaConsumer(
            TOPIC,
            bootstrap_servers=KAFKA_SERVER,
            auto_offset_reset='earliest',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
        print("[Service 4] Kafka Consumer Connecté !")
    except Exception as e:
        print(f"Kafka pas encore prêt ({e}). Nouvelle tentative dans 5s...")
        time.sleep(5)
print("En attente de données...")

for message in consumer:
    try:
        data = message.value
        bin_id = data.get('bin_id')
        bin_type = data.get('bin_type')
        
        # Extraction des valeurs
        us_val = next((s['value'] for s in data['sensors'] if s['id'] == 'US-01'), 0)
        weight_val = next((s['value'] for s in data['sensors'] if s.get('id') == 'LC'), 0)
        
        # Détection Anomalie (Logique Métier)
        anomalie = 0
        if us_val > 90 and weight_val < 2.0:
            print(f"⚠️  ANOMALIE: {bin_id} (Plein mais léger)")
            anomalie = 1
        
        # Création du Point InfluxDB (TYPAGE FLOAT FORCÉ)
        point = Point("bin_status") \
            .tag("bin_id", bin_id) \
            .tag("type", bin_type) \
            .field("fill_level", float(us_val)) \
            .field("weight", float(weight_val)) \
            .field("battery", int(data['status']['battery_level'])) \
            .field("anomaly", int(anomalie))
        
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
        print(f"[Cloud] Sauvegardé: {bin_id} | Niv={us_val} | Poids={weight_val}")
        
    except Exception as e:
        print(f"Erreur traitement message: {e}")
