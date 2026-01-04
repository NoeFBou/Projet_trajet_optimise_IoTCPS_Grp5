import json
import statistics
import time
import os
import paho.mqtt.client as mqtt
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable
# --- CONFIGURATION ---
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
MQTT_TOPIC_IN = "bin/raw_signals"

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
KAFKA_TOPIC_OUT = "topic-filtered-data"

# API_PORT = 5000
# PRODUCER = KafkaProducer(
#     bootstrap_servers=['kafka:9092'],
#     value_serializer=lambda v: json.dumps(v).encode('utf-8')
# )

# Stockage en mémoire vive (Dernier état connu pour chaque poubelle)
# Structure : { "PBL-ID-01": { ...données filtrées... }, ... }
# DATA_STORE = {}
#
# # Initialisation de l'application API
# app = Flask(__name__)


# --- 1. LOGIQUE DE FILTRAGE (Votre logique existante) ---

# --- LOGIQUE DE FILTRAGE ---
def filtrer_valeurs_aberrantes(liste_valeurs, seuil_tolerance=10.0):
    """Filtre Médian + Suppression Outliers + Moyenne"""
    if not liste_valeurs: return 0.0

    mediane = statistics.median(liste_valeurs)
    valeurs_propres = [v for v in liste_valeurs if abs(v - mediane) <= seuil_tolerance]

    if not valeurs_propres: return round(mediane, 2)
    return round(statistics.mean(valeurs_propres), 2)

# --- CONNEXION KAFKA ---
print(f"[Filtre] Connexion à Kafka ({KAFKA_BROKER})...")
producer = None
while producer is None:
    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKER,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        print("[Filtre] Connexion Kafka RÉUSSIE !")
    except NoBrokersAvailable:
        print("[Filtre] Kafka pas encore prêt... Nouvelle tentative dans 5 secondes.")
        time.sleep(5)
    except Exception as e:
        print(f"[Filtre] Autre erreur Kafka : {e}")
        time.sleep(5)


# --- 3. CALLBACK MQTT ---
def on_message(client, userdata, msg):
    try:
        # Décodage du message
        raw = json.loads(msg.payload.decode())
        bin_id = raw.get('id')

        # A. Filtrage des données brutes
        us_clean = filtrer_valeurs_aberrantes(raw.get('raw_us', []), 20.0)
        weight_clean = filtrer_valeurs_aberrantes(raw.get('raw_weight', []), 2.0)
        ir_data = raw.get('ir_levels', {"25_pct": 0, "50_pct": 0, "75_pct": 0})

        # B. Construction de l'objet "propre" pour Kafka
        processed_data = {
            "bin_id": bin_id,
            "type": raw.get('type', 'tout_type'),
            "zone": raw.get('zone', "Unknown"),
            "coords": raw.get('coords', {'lat': 0, 'lon': 0}),
            "timestamp": time.time(),
            "measurements": {
                "fill_level_cm": us_clean,
                "weight_kg": weight_clean,
                "fill_alerts": {
                    "level_25": bool(ir_data.get("25_pct")),
                    "level_50": bool(ir_data.get("50_pct")),
                    "level_75": bool(ir_data.get("75_pct"))
                }
            },
            "battery": raw.get('battery')
        }

        # C. Envoi vers Kafka
        producer.send(KAFKA_TOPIC_OUT, processed_data)

        # Log (optionnel : commenter si trop verbeux)
        print(f"[Filtre] {bin_id} -> Traité et envoyé à Kafka")

    except Exception as e:
        print(f"[Filtre] Erreur processing: {e}")


# --- 4. DÉMARRAGE MQTT (Avec Retry) ---
if __name__ == '__main__':
    print(f"[Filtre] Démarrage du service...")
    print(f"[Filtre] Source MQTT: {MQTT_BROKER} ({MQTT_TOPIC_IN})")

    # Initialisation du client (Version 1 pour éviter le warning Deprecated)
    try:
        # Tente d'utiliser la constante si la librairie est récente
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        # Fallback pour les vieilles versions de paho-mqtt
        client = mqtt.Client()

    client.on_message = on_message

    # Boucle de connexion robuste pour MQTT
    mqtt_connected = False
    while not mqtt_connected:
        try:
            client.connect(MQTT_BROKER, 1883, 60)
            mqtt_connected = True
            print("[Filtre] Connexion MQTT RÉUSSIE !")
        except Exception as e:
            print(f"[Filtre] Impossible de joindre Mosquitto ({e})... Nouvel essai dans 5s.")
            time.sleep(5)

    client.subscribe(MQTT_TOPIC_IN)
    client.loop_forever()

# old enlever
# --- 2. GESTION MQTT (En arrière-plan) ---
# def on_message(client, userdata, msg):
#     try:
#         raw = json.loads(msg.payload.decode())
#         bin_id = raw['id']
#
#         # A. Filtrage
#         us_clean = filtrer_valeurs_aberrantes(raw.get('raw_us', []), 20.0)
#         weight_clean = filtrer_valeurs_aberrantes(raw.get('raw_weight', []), 2.0)
#         ir_data = raw.get('ir_levels', {"25_pct": 0, "50_pct": 0, "75_pct": 0})
#
#         # B. Construction de l'objet de données API
#         processed_data = {
#             "metadata": {
#                 "id": bin_id,
#                 "type": raw['type'],
#                 "zone": raw.get('zone', "Unknown"),
#                 "last_update": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
#                 "coords": raw['coords']
#             },
#             "measurements": {
#                 "fill_level_cm": us_clean,  # Distance ultrason filtrée
#                 "weight_kg": weight_clean,  # Poids filtré
#                 "fill_alerts": {  # Interprétation IR
#                     "level_25": bool(ir_data["25_pct"]),
#                     "level_50": bool(ir_data["50_pct"]),
#                     "level_75": bool(ir_data["75_pct"])
#                 }
#             },
#             "status": {
#                 "battery": raw['battery'],
#                 "sensor_health": "nominal"
#             }
#         }
#
#         print(f"[API Update] Données mises à jour pour {bin_id}")
#
#     except Exception as e:
#         print(f"Erreur processing MQTT: {e}")
#
#
# def start_mqtt_listener():
#     """Fonction lancée dans un thread séparé"""
#     try:
#         client = mqtt.Client()
#         client.on_message = on_message
#         client.connect(MQTT_BROKER, 1883, 60)
#         client.subscribe(TOPIC_IN)
#         print(f"[Background] Écoute MQTT sur {TOPIC_IN} active...")
#         client.loop_forever()
#     except Exception as e:
#         print(f"Erreur connexion MQTT: {e}")
#

# --- 3. ROUTES DE L'API HTTP ---

# @app.route('/api/bins', methods=['GET'])
# def get_all_bins():
#     """Récupérer la liste de toutes les poubelles"""
#     # On transforme le dictionnaire en liste pour le JSON
#     return jsonify(list(DATA_STORE.values()))
#
#
# @app.route('/api/bins/<bin_id>', methods=['GET'])
# def get_single_bin(bin_id):
#     """Récupérer une poubelle spécifique par son ID"""
#     if bin_id in DATA_STORE:
#         return jsonify(DATA_STORE[bin_id])
#     else:
#         return jsonify({"error": "Poubelle non trouvée ou pas encore de données"}), 404
#
#
# @app.route('/api/zones/<zone_name>', methods=['GET'])
# def get_bins_by_zone(zone_name):
#     """Filtrer les poubelles par zone (ex: Quartier Nord)"""
#     bins_in_zone = [b for b in DATA_STORE.values() if b['metadata']['zone'] == zone_name]
#     return jsonify(bins_in_zone)
#
#
# # --- 4. DÉMARRAGE ---
# if __name__ == '__main__':
#     print(f"[Filtre] Démarrage du service...")
#     print(f"[Filtre] Source MQTT: {MQTT_BROKER} ({MQTT_TOPIC_IN})")
#     print(f"[Filtre] Cible Kafka: {KAFKA_BROKER} ({KAFKA_TOPIC_OUT})")
#
#     try:
#         client = mqtt.Client()
#         client.on_message = on_message
#         client.connect(MQTT_BROKER, 1883, 60)
#         client.subscribe(MQTT_TOPIC_IN)
#
#         client.loop_forever()
#
#     except KeyboardInterrupt:
#         print("\n[Filtre] Arrêt du service.")
#     except Exception as e:
#         print(f"[Filtre] Erreur critique MQTT: {e}")