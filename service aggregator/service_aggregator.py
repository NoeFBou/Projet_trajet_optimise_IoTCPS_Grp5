import json
import time
import requests
import threading
import os
from kafka import KafkaConsumer

# --- CONFIG ---
KAFKA_BROKER = "kafka:9092"
TOPIC_LEVELS = "bin-levels"  # Topic contenant les niveaux calculés (fusion)
VRP_SERVICE_URL = "http://vrp-service:8000/solve_vrp"
TRUCK_SERVICE_URL = "http://truck-service:5000/api/trucks"

# Dépôt central (coordonnées de Nice pour l'exemple, à adapter)
DEPOT_COORDS = [43.7102, 7.2620]

# --- ÉTAT EN MÉMOIRE ---
# Stocke le dernier état connu : { "BIN-01": { "level": "E5", "lat": 43.x, "lon": 7.y, "weight": 20.5 }, ... }
BIN_STATE_DB = {}
DB_LOCK = threading.Lock()


def start_kafka_consumer():
    """Thread qui met à jour l'état des poubelles en temps réel"""
    print("[Aggregator] Démarrage écoute Kafka...")
    consumer = KafkaConsumer(
        TOPIC_LEVELS,
        bootstrap_servers=KAFKA_BROKER,
        auto_offset_reset='earliest',  # Important pour relire l'état au démarrage
        enable_auto_commit=True,
        group_id='aggregator-group',
        value_deserializer=lambda x: json.loads(x.decode('utf-8'))
    )

    for message in consumer:
        try:
            data = message.value
            bin_id = data.get('bin_id')

            with DB_LOCK:
                # On met à jour ou on crée l'entrée pour cette poubelle
                BIN_STATE_DB[bin_id] = {
                    "id": bin_id,
                    "level_code": data.get('level_code'),  # E1 à E5
                    "weight": data.get('weight', 0),
                    "lat": data['coords']['lat'],
                    "lon": data['coords']['lon'],
                    "zone": data.get('zone'),
                    "last_update": time.time()
                }

                # Debug rapide si critique
                if data.get('level_code') in ['E4', 'E5']:
                    print(f"[Aggregator] Update Critique: {bin_id} est {data['level_code']}")

        except Exception as e:
            print(f"[Aggregator] Erreur lecture msg: {e}")


def get_available_trucks():
    """Récupère les camions actifs via l'API Truck"""
    try:
        # On peut filtrer par date ou statut via les params, ici on prend tout ce qui est 'active'
        response = requests.get(f"{TRUCK_SERVICE_URL}?status=active")
        if response.status_code == 200:
            data = response.json()
            return data.get("trucks", [])
        else:
            print(f"[Aggregator] Erreur API Camions: {response.status_code}")
            return []
    except Exception as e:
        print(f"[Aggregator] Exception connexion Camions: {e}")
        return []


def trigger_optimization():
    """Logique principale : Sélection des poubelles -> Appel VRP"""
    print("\n--- [Aggregator] Lancement cycle optimisation ---")

    # 1. Filtrer les poubelles pleines (E4, E5)
    bins_to_collect = []
    total_waste_weight = 0

    with DB_LOCK:
        for b_id, info in BIN_STATE_DB.items():
            # Critère de ramassage : E4 (Presque plein) ou E5 (Plein)
            if info['level_code'] in ['E4', 'E5']:
                bins_to_collect.append({
                    "id": b_id,
                    "lat": info['lat'],
                    "lon": info['lon'],
                    "weight": info['weight']
                })
                total_waste_weight += info['weight']

    if not bins_to_collect:
        print("[Aggregator] Aucune poubelle critique à ramasser. On attend.")
        return

    print(
        f"[Aggregator] {len(bins_to_collect)} poubelles critiques identifiées (Poids total: {total_waste_weight:.2f}kg)")

    # 2. Récupérer les camions
    trucks = get_available_trucks()
    if not trucks:
        print("[Aggregator] ALERTE: Aucun camion disponible !")
        return

    # Formatter les camions pour le VRP (L'API VRP attend id et capacity)
    vrp_trucks = []
    total_truck_capacity = 0
    for t in trucks:
        # Conversion capacité m3 ou tonnes -> kg (Hypothèse ici: capacity est en kg pour simplifier, ou conversion à faire)
        # Supposons que la capacité du camion dans MongoDB est en Tonnes, on convertit en kg
        cap_kg = t['capacity'] * 1000
        vrp_trucks.append({
            "id": t['truck_id'],
            "capacity": cap_kg
        })
        total_truck_capacity += cap_kg

    print(f"[Aggregator] Flotte disponible: {len(vrp_trucks)} camions (Capacité totale: {total_truck_capacity}kg)")

    # 3. Vérification de capacité sommaire (Le VRP fera le détail, mais on check le global)
    if total_waste_weight > total_truck_capacity:
        print("[Aggregator] ATTENTION: Plus de déchets que de capacité camion. Le VRP priorisera.")

    # 4. Construire la requête VRP
    payload = {
        "bins": bins_to_collect,
        "trucks": vrp_trucks,
        "depot": DEPOT_COORDS
    }

    # 5. Appeler le service VRP
    try:
        print("[Aggregator] Envoi demande au VRP...")
        resp = requests.post(VRP_SERVICE_URL, json=payload)
        if resp.status_code == 200:
            result = resp.json()
            if result['status'] == 'optimized':
                print(f"[Aggregator] SUCCÈS ! {len(result['routes'])} tournées générées.")
                print(json.dumps(result, indent=2))
                # ICI : On pourrait envoyer les routes résultantes dans un autre topic Kafka "truck-routes"
            else:
                print(f"[Aggregator] Échec VRP: {result.get('message')}")
        else:
            print(f"[Aggregator] Erreur HTTP VRP: {resp.text}")
    except Exception as e:
        print(f"[Aggregator] Erreur appel VRP: {e}")


# --- BOUCLE PRINCIPALE ---
if __name__ == "__main__":
    # 1. Lancer le consumer Kafka en arrière-plan
    t = threading.Thread(target=start_kafka_consumer)
    t.daemon = True
    t.start()

    # 2. Boucle de déclenchement (Toutes les 30 secondes pour la démo)
    # En prod, ce serait toutes les heures ou sur trigger spécifique
    print("[Aggregator] Service démarré. En attente de données...")
    time.sleep(10)  # Temps de chauffe

    while True:
        trigger_optimization()
        time.sleep(30)  # Pause entre les cycles