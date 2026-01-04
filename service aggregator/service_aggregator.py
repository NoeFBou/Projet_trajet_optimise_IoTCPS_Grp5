import json
import time
import threading
import requests
import schedule
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

# --- CONFIG ---
KAFKA_BROKER = "kafka:9092"
KAFKA_TOPIC_IN = "bin-levels"  # Topic contenant les niveaux calculés (fusion)
VRP_SERVICE_URL = "http://vrp-service:8000/solve_vrp"
TRUCK_SERVICE_URL = "http://truck-service:5001/api/trucks"  # Attention au port 5001 (exposé) ou 5000 (interne docker)
# Si on est DANS le réseau docker, c'est truck-service:5000
TRUCK_SERVICE_INTERNAL_URL = "http://truck-service:5000/api/trucks"

# Dépôt central (coordonnées de Nice pour l'exemple)
DEPOT_COORDS = [43.7102, 7.2620]

# --- ÉTAT EN MÉMOIRE ---
# On utilise UN SEUL dictionnaire partagé
BIN_STATE_DB = {}
DB_LOCK = threading.Lock()


def start_kafka_consumer():
    """Écoute Kafka dans un thread séparé et met à jour l'état des poubelles"""
    # Pas besoin de global ici car BIN_STATE_DB est un objet mutable (dict)
    # mais pour être propre on peut le déclarer si on le réassignait

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
    """Logique principale : Sélection des poubelles -> Appel VRP"""
    print("\n--- [Aggregator] Lancement cycle optimisation ---", flush=True)

    bins_to_collect = []
    total_waste_weight = 0

    # Lecture sécurisée de la base mémoire
    with DB_LOCK:
        # On copie les items pour ne pas bloquer la DB trop longtemps
        items = list(BIN_STATE_DB.items())

    for b_id, info in items:
        # Critère de ramassage : E4 ou E5
        if info['level_code'] in ['E4', 'E5']:
            # Vérification simple des coordonnées (éviter les bugs à 0.0)
            if info['lat'] != 0.0 and info['lon'] != 0.0:
                bins_to_collect.append({
                    "id": b_id,
                    "lat": info['lat'],
                    "lon": info['lon'],
                    "weight": info['weight']
                })
                total_waste_weight += info['weight']

    if not bins_to_collect:
        print(f"[Aggregator] Rien à ramasser (Sur {len(items)} poubelles connues). On attend.", flush=True)
        return

    print(f"[Aggregator] {len(bins_to_collect)} poubelles critiques (Poids: {total_waste_weight:.2f}kg)", flush=True)

    # 2. Récupérer les camions
    trucks = get_available_trucks()

    # --- MOCK CAMION SI API VIDE (POUR TESTER) ---
    if not trucks:
        print("[Aggregator] Pas de camions API -> Utilisation camion de secours fictif.", flush=True)
        trucks = [{"truck_id": "TEST-TRUCK-01", "capacity": 20.0}]  # Capacité en tonnes ?
    # ---------------------------------------------

    # Formatter les camions pour le VRP
    vrp_trucks = []
    total_truck_capacity = 0

    for t in trucks:
        # On gère si l'ID est '_id' (Mongo) ou 'truck_id'
        tid = t.get('truck_id') or str(t.get('_id'))
        cap = t.get('capacity', 10)

        # Conversion Tonnes -> Kg (Hypothèse)
        cap_kg = cap * 1000

        vrp_trucks.append({
            "id": tid,
            "capacity": cap_kg
        })
        total_truck_capacity += cap_kg

    print(f"[Aggregator] Flotte: {len(vrp_trucks)} camions (Capacité: {total_truck_capacity}kg)", flush=True)

    # 3. Appel VRP
    payload = {
        "bins": bins_to_collect,
        "trucks": vrp_trucks,
        "depot": DEPOT_COORDS
    }

    try:
        print("[Aggregator] Envoi demande au VRP...", flush=True)
        resp = requests.post(VRP_SERVICE_URL, json=payload)

        if resp.status_code == 200:
            result = resp.json()
            # On affiche juste le résumé pour pas polluer les logs
            nb_routes = len(result.get('routes', []))
            print(f"[Aggregator] SUCCÈS VRP ! {nb_routes} tournées générées.", flush=True)
            if nb_routes > 0:
                print(f"[Aggregator] Exemple route 1: {result['routes'][0]['steps']} étapes", flush=True)
        else:
            print(f"[Aggregator] Échec VRP: {resp.status_code} - {resp.text}", flush=True)

    except Exception as e:
        print(f"[Aggregator] Erreur appel VRP: {e}", flush=True)


# --- BOUCLE PRINCIPALE ---
if __name__ == "__main__":
    # 1. Thread Kafka
    t = threading.Thread(target=start_kafka_consumer)
    t.daemon = True
    t.start()

    print("[Aggregator] Service démarré. En attente de données...", flush=True)
    time.sleep(5)

    while True:
        trigger_optimization()
        # On réduit le temps d'attente à 10s pour voir le résultat plus vite
        time.sleep(10)