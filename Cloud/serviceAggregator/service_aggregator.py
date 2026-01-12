"""
Service Aggregator
------------------
Orchestrateur central du système de gestion des déchets.

Responsabilités :
1. Consommation des événements Kafka (niveaux de remplissage).
2. Maintenance de l'état global des poubelles en mémoire.
3. Déclenchement de l'optimisation des tournées (VRP) par type de déchet.
4. Enregistrement de l'historique et communication avec le simulateur.

Auteur : moi
"""
import os
import json
import time
import threading
import datetime
import concurrent.futures
from typing import List, Dict, Any, Optional

import requests
import pymongo
from flask import Flask, jsonify
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

# --- CONFIGURATION & CONSTANTES ---

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka:29092")
KAFKA_TOPIC_IN = "bin-data"
KAFKA_GROUP_ID = "aggregator-group"

# Endpoints des microservices
VRP_SERVICE_URL = os.getenv("VRP_URL", "http://vrp-service:8000/solve_vrp")
TRUCK_SERVICE_URL = os.getenv("TRUCK_URL", "http://truck-api:5000/api/trucks")
SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://simulator:5000/empty")

# Base de données
MONGO_URI = "mongodb://mongodb:27017/"
DB_NAME = "waste_management"
COLLECTION_HISTORY = "routes_history"

# Paramètres Géographiques
DEPOT_COORDS = [43.7102, 7.2620]  # [Lat, Lon]

# Mapping Code Poubelle (Suffixe ID) -> Type Camion (API Truck)
TYPE_MAPPING = {
    "VER": "Verre",  # Verre
    "REC": "Recyclable",  # Emballages/Jaune
    "ORG": "Organique",  # Compost
    "TOU": "TousDechets"  # Ordures Ménagères
}

# État Global (Thread-Safe)
BIN_STATE_DB: Dict[str, Dict] = {}
DB_LOCK = threading.Lock()

# --- INITIALISATION ---

app = Flask(__name__)

try:
    mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    routes_collection = db[COLLECTION_HISTORY]
except Exception as e:
    print(f"[FATAL] Impossible de connecter MongoDB : {e}")
    routes_collection = None


# --- LOGIQUE KAFKA ---

def start_kafka_consumer():
    """
    Démarre le consommateur Kafka en arrière-plan.
    Met à jour BIN_STATE_DB à chaque nouveau message reçu.
    """
    print("[Aggregator] Démarrage du thread Kafka...", flush=True)
    consumer = None

    # Tentative de reconnexion résiliente
    while consumer is None:
        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC_IN,
                bootstrap_servers=KAFKA_BROKER,
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                group_id=KAFKA_GROUP_ID,
                value_deserializer=lambda x: json.loads(x.decode('utf-8'))
            )
            print(f"[Aggregator] Connecté au broker {KAFKA_BROKER}", flush=True)
        except NoBrokersAvailable:
            print("[Aggregator] Broker indisponible, nouvelle tentative dans 5s...")
            time.sleep(5)
        except Exception as e:
            print(f"[Aggregator] Erreur inattendue Kafka: {e}")
            time.sleep(5)

    # Boucle de consommation
    try:
        for message in consumer:
            bin_data = message.value
            bin_id = bin_data.get("bin_id")
            level_code = bin_data.get("level_code")

            if bin_id:
                with DB_LOCK:
                    coords = bin_data.get('coords', {})

                    # Déduction du type de déchet (Priorité: Message > Suffixe ID > Défaut)
                    detected_type = bin_data.get("type")

                    if not detected_type or detected_type == "Indefini":
                        try:
                            # Extraction du suffixe (ex: PBL-001-VER -> VER)
                            suffix = bin_id.split('-')[-1]
                            detected_type = TYPE_MAPPING.get(suffix, "general")
                        except IndexError:
                            detected_type = "general"
                    weight_val = bin_data.get('weight_kg')
                    # Mise à jour de l'état local
                    BIN_STATE_DB[bin_id] = {
                        "type": detected_type,
                        "level_code": level_code,
                        "weight": weight_val,
                        "lat": coords.get('lat', 0.0),
                        "lon": coords.get('lon', 0.0),
                        "last_update": time.time()
                    }
    except Exception as e:
        print(f"[Aggregator] Arrêt du consumer suite à une erreur : {e}", flush=True)


# --- LOGIQUE MÉTIER ---

def get_available_trucks() -> List[Dict]:
    """
    Récupère la liste des camions actifs depuis le microservice TruckAPI.

    Returns:
        List[Dict]: Liste de dictionnaires représentant les camions.
                    Format normalisé: {'truck_id': str, 'capacity': float, 'waste_type': str}
    """
    try:
        response = requests.get(f"{TRUCK_SERVICE_URL}?status=active", timeout=2)
        if response.status_code == 200:
            data = response.json()
            raw_trucks = data if isinstance(data, list) else data.get("trucks", [])

            unique_trucks = {}
            for t in raw_trucks:
                # Normalisation de l'ID (MongoDB _id vs id string)
                tid = t.get('truck_id') or t.get('id') or str(t.get('_id'))
                if tid:
                    t['truck_id'] = tid
                    t.setdefault('waste_type', 'general')  # Valeur par défaut
                    unique_trucks[tid] = t

            return list(unique_trucks.values())
        else:
            print(f"[Aggregator] Erreur TruckAPI: HTTP {response.status_code}")
            return []
    except Exception as e:
        print(f"[Aggregator] Exception lors de la récupération des camions : {e}")
        return []


def prepare_vrp_payloads(bins_db: Dict, trucks: List[Dict]) -> Dict[str, Dict]:
    """
    Prépare les données pour le service VRP, segmentées par type de déchet.

    Args:
        bins_db (Dict): Base de données locale des poubelles.
        trucks (List[Dict]): Liste complète des camions disponibles.

    Returns:
        Dict[str, Dict]: Dictionnaire {waste_type: payload_vrp}
    """
    # 1. Filtrage des poubelles critiques (E4, E5)
    bins_by_type = {}
    for b_id, info in bins_db.items():
        if info['level_code'] in ['E4', 'E5']:
            if info['lat'] != 0.0 and info['lon'] != 0.0:
                b_type = info.get('type', 'general')

                if b_type not in bins_by_type:
                    bins_by_type[b_type] = []

                bins_by_type[b_type].append({
                    "id": b_id,
                    "lat": info['lat'],
                    "lon": info['lon'],
                    "weight": info['weight']
                })

    if not bins_by_type:
        return {}

    # 2. Construction des payloads par flux
    payloads = {}
    for waste_type, bins_list in bins_by_type.items():
        # Filtrage des camions compatibles pour ce flux
        compatible_trucks = []
        for t in trucks:
            t_type = t.get('waste_type', 'general')
            # On garde le camion s'il correspond au type ou s'il est polyvalent ('all')
            if t_type == waste_type or t_type == 'all':
                tid = t.get('truck_id')
                cap_tonnes = float(t.get('capacity', 10))
                compatible_trucks.append({
                    "id": tid,
                    "capacity": cap_tonnes * 1000  # Conversion Tonnes -> Kg
                })

        if compatible_trucks:
            payloads[waste_type] = {
                "bins": bins_list,
                "trucks": compatible_trucks,
                "depot": DEPOT_COORDS
            }
        else:
            print(f"[Attention] Aucun camion disponible pour le flux : {waste_type}")

    return payloads


# --- API ---

@app.route('/run-optimization', methods=['POST'])
def run_optimization():
    """
    Endpoint principal pour déclencher le calcul des tournées.
    Exécute les appels VRP en parallèle pour chaque type de déchet.
    """
    print("\n--- [Cycle Optimisation] Démarrage ---", flush=True)

    # 1. Instantané Thread-Safe de la DB
    with DB_LOCK:
        snapshot_db = BIN_STATE_DB.copy()

    # 2. Récupération ressources
    available_trucks = get_available_trucks()
    payloads = prepare_vrp_payloads(snapshot_db, available_trucks)

    if not payloads:
        return jsonify({"status": "no_action", "message": "Aucune poubelle critique ou camions manquants."})

    # 3. Exécution Parallèle (VRP Multi-Flux)
    final_routes = []
    total_fleet_time = 0
    collected_bin_ids = []

    print(f"[Aggregator] Traitement parallèle de {len(payloads)} flux : {list(payloads.keys())}")

    with concurrent.futures.ThreadPoolExecutor() as executor:
        # Soumission des requêtes
        future_map = {
            executor.submit(requests.post, VRP_SERVICE_URL, json=p): w_type
            for w_type, p in payloads.items()
        }

        for future in concurrent.futures.as_completed(future_map):
            waste_type = future_map[future]
            try:
                response = future.result()
                if response.status_code == 200:
                    result = response.json()

                    if result.get('status') == 'optimized':
                        routes = result.get('routes', [])
                        print(f"   [OK] Flux {waste_type} : {len(routes)} tournée(s) générée(s).")

                        for route in routes:
                            # Enrichissement ID camion pour affichage dashboard (ex: "T1 (glass)")
                            route['truck_id'] = f"{route['truck_id']} ({waste_type})"
                            final_routes.append(route)

                            # Collecte des IDs poubelles pour le simulateur
                            for stop in route['stops']:
                                if "DEPOT" not in stop['point_id']:
                                    collected_bin_ids.append(stop['point_id'])

                        total_fleet_time += result.get('total_fleet_time', 0)
                    else:
                        print(f"   [Echec VRP] Flux {waste_type} : {result.get('message')}")
                else:
                    print(f"   [Erreur HTTP] Flux {waste_type} : Code {response.status_code}")

            except Exception as e:
                print(f"   [Exception] Erreur lors du traitement du flux {waste_type} : {e}")

    # 4. Sauvegarde et Feedback
    if final_routes:
        # Persistance dans MongoDB
        if routes_collection is not None:
            history_entry = {
                "timestamp": datetime.datetime.utcnow(),
                "total_fleet_time": total_fleet_time,
                "routes": final_routes,
                "optimization_details": list(payloads.keys())
            }
            routes_collection.insert_one(history_entry)

        # Notification Simulateur (Vidage des poubelles)
        if collected_bin_ids:
            try:
                requests.post(SIMULATOR_URL, json={"bin_ids": collected_bin_ids}, timeout=1)
            except Exception:
                pass  # Non-bloquant pour la prod

        return jsonify({
            "status": "success",
            "routes_count": len(final_routes),
            "collected_bins": len(collected_bin_ids),
            "details": f"Optimisé pour : {', '.join(payloads.keys())}"
        })

    return jsonify({"status": "failed", "message": "Aucune solution trouvée par le VRP."})


if __name__ == "__main__":
    # Lancement du consumer dans un thread daemon
    kafka_thread = threading.Thread(target=start_kafka_consumer, daemon=True)
    kafka_thread.start()

    print("[Aggregator] Service API prêt sur le port 5000", flush=True)
    app.run(host='0.0.0.0', port=5000)