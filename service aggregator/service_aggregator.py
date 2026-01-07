import json
import time
import threading
import requests
import datetime
import pymongo
from flask import Flask, jsonify
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable


# --- CONFIG ---
KAFKA_BROKER = "kafka:9092"
KAFKA_TOPIC_IN = "bin-levels"
VRP_SERVICE_URL = "http://vrp-service:8000/solve_vrp"
TRUCK_SERVICE_INTERNAL_URL = "http://truck-service:5000/api/trucks"
SIMULATOR_URL = "http://simulator:5000/empty"

MONGO_URI = "mongodb://mongodb:27017/"
mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client["waste_management"]
routes_collection = db["routes_history"]

DEPOT_COORDS = [43.7102, 7.2620]
BIN_STATE_DB = {}
DB_LOCK = threading.Lock()

# --- FLASK APP ---
app = Flask(__name__)

# --- KAFKA CONSUMER ---
def start_kafka_consumer():
    print("[Aggregator] Connexion Kafka...", flush=True)
    consumer = None
    while consumer is None:
        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC_IN,
                bootstrap_servers=KAFKA_BROKER,
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                group_id='aggregator-group',
                value_deserializer=lambda x: json.loads(x.decode('utf-8'))
            )
            print("[Aggregator] Kafka Connecté !", flush=True)
        except NoBrokersAvailable:
            time.sleep(5)
        except Exception:
            time.sleep(5)

    try:
        for message in consumer:
            bin_data = message.value
            bin_id = bin_data.get("bin_id")
            level_code = bin_data.get("level_code")

            if bin_id:
                with DB_LOCK:
                    coords = bin_data.get('coords', {})
                    BIN_STATE_DB[bin_id] = {
                        "type": bin_data.get("type", "Indefini"),
                        "level_code": level_code,
                        "weight": bin_data.get('weight', 0.0),
                        "lat": coords.get('lat', 0.0),
                        "lon": coords.get('lon', 0.0),
                        "last_update": time.time()
                    }
    except Exception as e:
        print(f"[Aggregator] Erreur Kafka : {e}", flush=True)

def get_available_trucks():
    try:
        response = requests.get(f"{TRUCK_SERVICE_INTERNAL_URL}?status=active", timeout=2)
        if response.status_code == 200:
            data = response.json()
            raw = data if isinstance(data, list) else data.get("trucks", [])

            # Dédoublonnage
            unique_map = {}
            for t in raw:
                tid = t.get('truck_id') or t.get('id') or str(t.get('_id'))
                if tid:
                    t['truck_id'] = tid
                    unique_map[tid] = t
            return list(unique_map.values())
        return []
    except:
        return []



@app.route('/run-optimization', methods=['POST'])
def run_optimization():
    print("\n--- [Aggregator] Optimisation Manuelle Demandée ---", flush=True)

    # 1. Récupération et Tri des poubelles par TYPE
    bins_by_type = {}  # Ex: {'Verre': [bin1, bin2], 'Recyclable': [bin3]}
    total_weight_global = 0

    with DB_LOCK:
        items = list(BIN_STATE_DB.items())

    for b_id, info in items:
        if info['level_code'] in ['E4', 'E5']:
            if info['lat'] != 0.0 and info['lon'] != 0.0:
                b_type = info.get('type', 'Autre')

                if b_type not in bins_by_type:
                    bins_by_type[b_type] = []

                bins_by_type[b_type].append({
                    "id": b_id,
                    "lat": info['lat'],
                    "lon": info['lon'],
                    "weight": info['weight']
                })
                total_weight_global += info['weight']

    if not bins_by_type:
        return jsonify({"status": "no_action", "message": "Aucune poubelle à vider."})

    # 2. Récupération de la flotte
    trucks = get_available_trucks()
    if not trucks:
        trucks = [{"truck_id": "DEFAUT-01", "capacity": 20.0}]

    # Préparation camions pour VRP
    vrp_trucks = []
    for t in trucks:
        tid = t.get('truck_id')
        cap = float(t.get('capacity', 10))
        vrp_trucks.append({"id": tid, "capacity": cap * 1000})

    # 3. LANCEMENT DES VRP PAR TYPE (BOUCLE)
    final_routes = []
    total_fleet_time = 0
    collected_ids = []

    print(f"[Aggregator] Début optimisation multi-flux ({len(bins_by_type)} types)...", flush=True)

    for waste_type, bins in bins_by_type.items():
        print(f"   > Traitement du flux : {waste_type} ({len(bins)} poubelles)", flush=True)

        # On appelle le VRP pour CE type de déchet uniquement
        # (On suppose ici que TOUS les camions sont compatibles/nettoyés,
        # ou alors on pourrait filtrer les camions si on avait un type dans la DB camions)
        payload = {"bins": bins, "trucks": vrp_trucks, "depot": DEPOT_COORDS}

        try:
            resp = requests.post(VRP_SERVICE_URL, json=payload, timeout=60)

            if resp.status_code == 200:
                result = resp.json()
                if result['status'] == 'optimized':
                    # On ajoute les routes générées à la liste globale
                    # On peut ajouter un tag au camion pour dire ce qu'il transporte
                    for route in result['routes']:
                        # Petit hack : on modifie l'ID du camion pour afficher le type dans le dashboard
                        # Ex: "T001" devient "T001 (Verre)"
                        route['truck_id'] = f"{route['truck_id']} ({waste_type})"
                        final_routes.append(route)

                        # Collecte des IDs pour le simulateur
                        for stop in route['stops']:
                            if "DEPOT" not in stop['point_id']:
                                collected_ids.append(stop['point_id'])

                    total_fleet_time += result.get('total_fleet_time', 0)
            else:
                print(f"   ! Erreur VRP sur flux {waste_type}: {resp.status_code}")

        except Exception as e:
            print(f"   ! Exception VRP sur flux {waste_type}: {e}")

    # 4. Résultat Global
    if final_routes:
        # Sauvegarde Mongo
        save_data = {
            "timestamp": datetime.datetime.utcnow(),
            "total_fleet_time": total_fleet_time,
            "routes": final_routes
        }
        routes_collection.insert_one(save_data)

        # Feedback Simulateur
        if collected_ids:
            try:
                requests.post(SIMULATOR_URL, json={"bin_ids": collected_ids}, timeout=2)
            except:
                pass

        return jsonify({
            "status": "success",
            "routes_count": len(final_routes),
            "collected_bins": len(collected_ids),
            "trucks_used": len(vrp_trucks),
            "details": f"Optimisé par flux : {list(bins_by_type.keys())}"
        })
    else:
        return jsonify({"status": "failed", "message": "Aucune solution trouvée pour aucun flux."})

if __name__ == "__main__":
    # Lancement du consumer Kafka
    t = threading.Thread(target=start_kafka_consumer)
    t.daemon = True
    t.start()

    print("[Aggregator] API prête sur le port 5000", flush=True)
    # Lancement du serveur Web
    app.run(host='0.0.0.0', port=5000)

# def trigger_optimization():
#     print("\n--- [Aggregator] Lancement cycle optimisation ---", flush=True)
#
#     bins_to_collect = []
#
#     with DB_LOCK:
#         items = list(BIN_STATE_DB.items())
#
#     for b_id, info in items:
#         if info['level_code'] in ['E4', 'E5']:
#             if info['lat'] != 0.0 and info['lon'] != 0.0:
#                 bins_to_collect.append({
#                     "id": b_id,
#                     "lat": info['lat'],
#                     "lon": info['lon'],
#                     "weight": info['weight']
#                 })
#
#     if not bins_to_collect:
#         print(f"[Aggregator] Rien à ramasser (Sur {len(items)} poubelles).", flush=True)
#         return
#
#     print(f"[Aggregator] {len(bins_to_collect)} poubelles critiques.", flush=True)
#
#     trucks = get_available_trucks()
#     if not trucks:
#         trucks = [{"truck_id": "TEST-TRUCK-01", "capacity": 20.0}]
#     print(len(trucks), "camions disponibles.", flush=True
#           )
#     vrp_trucks = []
#     for t in trucks:
#         tid = t.get('truck_id') or str(t.get('_id'))
#         cap_kg = t.get('capacity', 10) * 1000
#         vrp_trucks.append({"id": tid, "capacity": cap_kg})
#         print(tid, cap_kg, flush=True)
#         print("aled")
#
#     payload = {"bins": bins_to_collect, "trucks": vrp_trucks, "depot": DEPOT_COORDS}
#
#     try:
#         print("[Aggregator] Envoi demande au VRP...", flush=True)
#         resp = requests.post(VRP_SERVICE_URL, json=payload)
#
#         if resp.status_code == 200:
#             result = resp.json()
#             nb_routes = len(result.get('routes', []))
#             print(f"[Aggregator] SUCCÈS VRP ! {nb_routes} tournées.", flush=True)
#
#             if nb_routes > 0:
#                 save_data = {
#                     "timestamp": datetime.datetime.utcnow(),
#                     "total_fleet_time": result.get('total_fleet_time'),
#                     "routes": result.get('routes')
#                 }
#                 routes_collection.insert_one(save_data)
#
#                 collected_ids = []
#                 for route in result['routes']:
#                     for stop in route['stops']:
#                         if "DEPOT" not in stop['point_id']:
#                             collected_ids.append(stop['point_id'])
#
#                 if collected_ids:
#                     print(f"[Aggregator] Envoi ordre de vidage pour {len(collected_ids)} poubelles...", flush=True)
#                     try:
#                         # Appel HTTP vers le simulateur
#                         requests.post(SIMULATOR_URL, json={"bin_ids": collected_ids}, timeout=2)
#                     except Exception as e:
#                         print(f"[Aggregator] Erreur contact simulateur: {e}", flush=True)
#
#         else:
#             print(f"[Aggregator] Échec VRP: {resp.status_code}", flush=True)
#
#     except Exception as e:
#         print(f"[Aggregator] Erreur générale: {e}", flush=True)


# --- ROUTE API ---
