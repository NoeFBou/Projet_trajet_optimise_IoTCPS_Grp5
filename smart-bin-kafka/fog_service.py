import json
from kafka import KafkaConsumer
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


TOPIC_NAME = "bin-sensor-data"
KAFKA_SERVER = "localhost:9092"


INFLUX_URL = "http://localhost:8086"

INFLUX_TOKEN = "my-super-secret-auth-token"
INFLUX_ORG = "smartcity"
INFLUX_BUCKET = "bins"

print(" Connexion à InfluxDB...")
try:
    db_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = db_client.write_api(write_options=SYNCHRONOUS)
    print(" Connecté à InfluxDB !")
except Exception as e:
    print(f" Erreur InfluxDB : {e}")
    exit()

print(f" Démarrage du Consumer Kafka sur {KAFKA_SERVER}...")
try:
    consumer = KafkaConsumer(
        TOPIC_NAME,
        bootstrap_servers=KAFKA_SERVER,
        auto_offset_reset='latest',
        value_deserializer=lambda x: json.loads(x.decode('utf-8'))
    )
except Exception as e:
    print(f" Erreur Connexion Kafka : {e}")
    exit()

print(" En attente de données...")

try:
    for message in consumer:
        data = message.value

        bin_id = data.get('bin_id', 'Unknown')
        bin_type = data.get('bin_type', 'Unknown')
        

        us_sensor = next((s for s in data['sensors'] if s['id'] == 'US-01'), None)
        weight_sensor = next((s for s in data['sensors'] if s['type'] == 'load_cell'), None)
        
        if us_sensor and weight_sensor:

            niveau = float(us_sensor['value'])
            poids = float(weight_sensor['value'])
            
            print(f" [Kafka] Reçu {bin_id} ({bin_type}): Niv={niveau}cm, Poids={poids}kg")
            

            anomalie = False

            if niveau > 90 and poids < 2.0:
                print(f" ANOMALIE DÉTECTÉE ! (Incohérence Niveau/Poids)")
                anomalie = True
            


            point = Point("bin_status") \
                .tag("bin_id", bin_id) \
                .tag("type", bin_type) \
                .field("fill_level", niveau) \
                .field("weight", poids) \
                .field("battery", int(data['status']['battery_level'])) \
                .field("anomaly", int(anomalie))
            
            try:
                write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
                print("    [InfluxDB] Sauvegardé.")
            except Exception as e:
                print(f"  Erreur écriture InfluxDB: {e}")

except KeyboardInterrupt:
    print("\nArrêt du service.")
    consumer.close()
    db_client.close()
