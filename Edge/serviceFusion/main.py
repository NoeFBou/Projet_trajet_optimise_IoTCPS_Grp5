import json
import time
import os
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import NoBrokersAvailable
import sys
from level_calculator_with_dempster_shafer import WasteBinMonitor

# --- CONFIGURATION KAFKA ---
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
INPUT_TOPIC = "topic-filtered-data"
OUTPUT_TOPIC = "bin-levels"

# --- INITIALISATION ---
monitor = WasteBinMonitor()

print(f"[Fusion] Connexion à Kafka ({KAFKA_BROKER})...")
# --- CONNEXION KAFKA ---
consumer = None
producer = None

while consumer is None:
    try:
        consumer = KafkaConsumer(
            INPUT_TOPIC,
            bootstrap_servers=KAFKA_BROKER,
            auto_offset_reset='earliest',
            group_id='fusion-service-group',
            value_deserializer=lambda x: json.loads(x.decode('utf-8'))
        )
        print("[Fusion] Consumer connecté !", flush=True) # <--- flush=True force l'affichage
    except NoBrokersAvailable:
        print("[Fusion] Kafka pas prêt (Consumer)... on attend 5s.")
        time.sleep(5)
    except Exception as e:
        print(f"[Fusion] Erreur Consumer: {e}")
        time.sleep(5)

while producer is None:
    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        print("[Fusion] Producer connecté !")
    except NoBrokersAvailable:
        print("[Fusion] Kafka pas prêt (Producer)... on attend 5s.")
        time.sleep(5)
    except Exception as e:
        print(f"[Fusion] Erreur Producer: {e}")
        time.sleep(5)

# --- BOUCLE DE TRAITEMENT ---
try:
    print("[Fusion] En attente de messages...", flush=True)
    for message in consumer:
        try:
            data = message.value

            meas = data.get('measurements', {})
            alerts = meas.get('fill_alerts', {})

            monitor_inputs = {
                'type': data.get('type', 'tout_type'),

                # Poids
                'weight': meas.get('weight_kg', 0.0),

                # Ultrasons (Mapping de la valeur unique vers les 2 entrées attendues)
                'us1': meas.get('fill_level_cm', 0.0),
                'us2': meas.get('fill_level_cm', 0.0),

                # Infrarouges (Conversion Booléen -> 0/1 si nécessaire)
                'ir25': 1 if alerts.get('level_25') else 0,
                'ir50': 1 if alerts.get('level_50') else 0,
                'ir75': 1 if alerts.get('level_75') else 0
            }

            # --- B. CALCUL (FUSION) ---
            etat, m_final = monitor.compute_level(monitor_inputs)

            # Récupération du conflit (optionnel, pour debug)
            conflit = m_final.get(frozenset(), 0.0)

            # --- C. PUBLICATION DU RÉSULTAT ---
            output_message = {
                "bin_id": data.get('bin_id') or data.get('metadata', {}).get('id'),
                "level_code": etat,  # Ex: E1, E3, E5
                "level_desc": f"Conflit: {conflit:.2f}",
                "weight": monitor_inputs['weight'],
                "coords": data.get('coords'),  # Important : on fait suivre la position GPS !
                "zone": data.get('zone'),  # Important : on fait suivre la zone !
                "timestamp": time.time()
            }

            key_bytes = str(output_message['bin_id']).encode('utf-8')
            producer.send(OUTPUT_TOPIC, key=key_bytes, value=output_message)

            print(f"[Fusion] {output_message['bin_id']} -> État: {etat} (Poids: {monitor_inputs['weight']}kg)")
            print(f"[Fusion] {output_message['bin_id']} -> État: {etat}", flush=True)
        except Exception as e:
            print(f"[Fusion] Erreur traitement message: {e}")

except KeyboardInterrupt:
    print("[Fusion] Arrêt du service.")
finally:
    consumer.close()
    producer.close()