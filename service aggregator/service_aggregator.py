import json
import time
import threading
import requests
import schedule
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable
import pymongo
import datetime

# --- CONFIG ---
KAFKA_BROKER = "kafka:9092"
KAFKA_TOPIC_IN = "bin-levels"
VRP_SERVICE_URL = "http://vrp-service:8000/solve_vrp"
TRUCK_SERVICE_URL = "http://truck-service:5001/api/trucks"
TRUCK_SERVICE_INTERNAL_URL = "http://truck-service:5000/api/trucks"
MONGO_URI = "mongodb://mongodb:27017/"
mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client["waste_management"]
routes_collection = db["routes_history"]
SIMULATOR_URL = "http://simulator:5000/empty"

# Dépôt central
DEPOT_COORDS = [43.7102, 7.2620]

# --- ÉTAT EN MÉMOIRE ---
BIN_STATE_DB = {}
DB_LOCK = threading.Lock()


def start_kafka_consumer():
    """Écoute Kafka dans un thread séparé et met à jour l'état des poubelles"""


    print("[Aggregator] Tentative de connexion au Consumer Kafka...", flush=True)

    consumer = None
    while consumer is None:
        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC_IN,
                bootstrap_servers=KAFKA_BROKER,
                auto_offset_reset='earliest',  # On prend tout depuis le début
                enable_auto_commit=True,
                group_id='aggregator-group',
                value_deserializer=lambda x: json.loads(x.decode('utf-8'))
            )
            print("[Aggregator] Connexion Kafka (Consumer) RÉUSSIE !", flush=True)
        except NoBrokersAvailable:
            print("[Aggregator] Kafka pas prêt... on attend 5s.", flush=True)
            time.sleep(5)
        except Exception as e:
            print(f"[Aggregator] Erreur connexion Kafka: {e}", flush=True)
            time.sleep(5)

    # Une fois connecté, on boucle pour lire les messages
    try:
        for message in consumer:
            bin_data = message.value
            bin_id = bin_data.get("bin_id")
            level_code = bin_data.get("level_code")

            if bin_id:
                # On protège l'écriture avec un Lock pour éviter les conflits avec le thread principal
                with DB_LOCK:
                    # On stocke les infos nécessaires pour le VRP
                    # Le message Kafka contient déjà coords (lat/lon) et weight grâce au service Fusion
                    coords = bin_data.get('coords', {})

                    BIN_STATE_DB[bin_id] = {
                        "level_code": level_code,
                        "weight": bin_data.get('weight', 0.0),
                        "lat": coords.get('lat', 0.0),
                        "lon": coords.get('lon', 0.0),
                        "last_update": time.time()
                    }

                #print(f"[Aggregator] Reçu: {bin_id} est à l'état {level_code}", flush=True)

    except Exception as e:
        print(f"[Aggregator] Erreur lecture Kafka : {e}", flush=True)


def get_available_trucks():
    """Récupère les camions actifs via l'API Truck"""
    try:
        # On utilise l'URL interne au réseau Docker
        response = requests.get(f"{TRUCK_SERVICE_INTERNAL_URL}?status=active")
        if response.status_code == 200:
            data = response.json()
            # Selon le format de ton API, ça peut être data directement ou data['trucks']
            # J'ai vu dans ton app.py que tu retournes jsonify(trucks) -> une liste
            if isinstance(data, list):
                return data
            return data.get("trucks", [])
        else:
            print(f"[Aggregator] Erreur API Camions: {response.status_code}")
            return []
    except Exception as e:
        print(f"[Aggregator] Exception connexion Camions: {e}")
        return []


def trigger_optimization():
    print("\n--- [Aggregator] Lancement cycle optimisation ---", flush=True)

    bins_to_collect = []

    with DB_LOCK:
        items = list(BIN_STATE_DB.items())

    for b_id, info in items:
        if info['level_code'] in ['E4', 'E5']:
            if info['lat'] != 0.0 and info['lon'] != 0.0:
                bins_to_collect.append({
                    "id": b_id,
                    "lat": info['lat'],
                    "lon": info['lon'],
                    "weight": info['weight']
                })

    if not bins_to_collect:
        print(f"[Aggregator] Rien à ramasser (Sur {len(items)} poubelles).", flush=True)
        return

    print(f"[Aggregator] {len(bins_to_collect)} poubelles critiques.", flush=True)

    trucks = get_available_trucks()
    if not trucks:
        trucks = [{"truck_id": "TEST-TRUCK-01", "capacity": 20.0}]

    vrp_trucks = []
    for t in trucks:
        tid = t.get('truck_id') or str(t.get('_id'))
        cap_kg = t.get('capacity', 10) * 1000
        vrp_trucks.append({"id": tid, "capacity": cap_kg})

    payload = {"bins": bins_to_collect, "trucks": vrp_trucks, "depot": DEPOT_COORDS}

    try:
        print("[Aggregator] Envoi demande au VRP...", flush=True)
        resp = requests.post(VRP_SERVICE_URL, json=payload)

        if resp.status_code == 200:
            result = resp.json()
            nb_routes = len(result.get('routes', []))
            print(f"[Aggregator] SUCCÈS VRP ! {nb_routes} tournées.", flush=True)

            if nb_routes > 0:
                save_data = {
                    "timestamp": datetime.datetime.utcnow(),
                    "total_fleet_time": result.get('total_fleet_time'),
                    "routes": result.get('routes')
                }
                routes_collection.insert_one(save_data)

                collected_ids = []
                for route in result['routes']:
                    for stop in route['stops']:
                        if "DEPOT" not in stop['point_id']:
                            collected_ids.append(stop['point_id'])

                if collected_ids:
                    print(f"[Aggregator] Envoi ordre de vidage pour {len(collected_ids)} poubelles...", flush=True)
                    try:
                        # Appel HTTP vers le simulateur
                        requests.post(SIMULATOR_URL, json={"bin_ids": collected_ids}, timeout=2)
                    except Exception as e:
                        print(f"[Aggregator] Erreur contact simulateur: {e}", flush=True)

        else:
            print(f"[Aggregator] Échec VRP: {resp.status_code}", flush=True)

    except Exception as e:
        print(f"[Aggregator] Erreur générale: {e}", flush=True)


if __name__ == "__main__":
    t = threading.Thread(target=start_kafka_consumer)
    t.daemon = True
    t.start()

    print("[Aggregator] Service démarré.", flush=True)
    time.sleep(5)

    while True:
        trigger_optimization()
        time.sleep(20)