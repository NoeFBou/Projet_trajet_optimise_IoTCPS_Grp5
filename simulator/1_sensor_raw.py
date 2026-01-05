import json
import time
import random
import os
import threading
import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify

# --- CONFIGURATION ---
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
TOPIC = "bin/raw_signals"
INPUT_FILE = "simulation_iot_poubelles_light.json"

# Nombre de mesures envoyées en une seule fois (pour le filtre médian)
BURST_SIZE = 10

# --- FLASK APP ---
app = Flask(__name__)
SENSORS = []


def load_sensors():
    global SENSORS
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "r") as f:
            SENSORS = json.load(f)
        print(f"[Simulateur] {len(SENSORS)} capteurs chargés.")
    else:
        print(f"[Simulateur] ERREUR: Fichier {INPUT_FILE} introuvable.")
        SENSORS = []


# --- ROUTE POUR VIDER (FEEDBACK LOOP) ---
@app.route('/empty', methods=['POST'])
def empty_bins():
    ids_to_empty = request.json.get('bin_ids', [])
    count = 0
    for sensor in SENSORS:
        if sensor['id'] in ids_to_empty:
            sensor['current_level'] = 0
            count += 1
    print(f"[Simulateur] 🗑️ ORDRE REÇU : {count} poubelles vidées !", flush=True)
    return jsonify({"status": "ok", "emptied_count": count})


# --- SIMULATION INTELLIGENTE ---
def generate_burst_values(target_value, variation_pct=0.05, aberration_prob=0.1):
    """
    Génère une liste de valeurs autour de la cible avec du bruit
    et parfois une valeur totalement fausse (aberration) pour tester le filtre.
    """
    values = []
    for _ in range(BURST_SIZE):
        # 10% de chance d'avoir une valeur aberrante (ex: capteur sale ou bug)
        if random.random() < aberration_prob:
            # On génère une valeur absurde (ex: 3x la valeur ou 0)
            val = target_value * random.choice([0.1, 3.0])
        else:
            # Valeur normale avec bruit (+/- 5%)
            noise = random.uniform(-variation_pct, variation_pct)
            val = target_value * (1.0 + noise)

        # Pas de valeurs négatives
        values.append(max(0.0, round(val, 2)))
    return values


def simulation_loop():
    client = mqtt.Client()
    connected = False
    while not connected:
        try:
            client.connect(MQTT_BROKER, 1883, 60)
            connected = True
            print("[Simulateur] Connecté à MQTT.")
        except:
            print("[Simulateur] Attente MQTT...")
            time.sleep(5)

    print("[Simulateur] Démarrage simulation (Mode Rafale + Ralenti)...")

    while True:
        for sensor in SENSORS:
            # 1. Remplissage lent
            growth = sensor['daily_growth'] / 100.0
            sensor['current_level'] += growth
            if sensor['current_level'] > sensor['max_capacity']:
                sensor['current_level'] = sensor['max_capacity']

            # 2. Calcul des valeurs théoriques
            remplissage_pct = sensor['current_level'] / sensor['max_capacity']

            # Ultrason : Plus c'est plein, moins il y a de distance
            # On suppose une poubelle de 100cm de profondeur pour simplifier
            dist_max_cm = 150.0
            dist_theorique = (1.0 - remplissage_pct) * dist_max_cm

            # Poids théorique
            poids_theorique = sensor['current_level'] * sensor['density']

            # 3. GÉNÉRATION DES RAFALES (BURSTS)
            # C'est ici qu'on crée le travail pour le Filter Service
            raw_us_burst = generate_burst_values(dist_theorique, variation_pct=0.1)  # Bruit 10% sur US
            raw_weight_burst = generate_burst_values(poids_theorique, variation_pct=0.02)  # Bruit 2% sur poids

            # 4. Envoi MQTT
            payload = {
                "id": sensor['id'],
                "type": sensor['type'],
                "zone": sensor.get('zone', 'Unknown'),
                "coords": sensor['coords'],

                # On envoie les listes complètes !
                "raw_us": raw_us_burst,
                "raw_weight": raw_weight_burst,

                "ir_levels": {
                    "25_pct": int(remplissage_pct > 0.25),
                    "50_pct": int(remplissage_pct > 0.50),
                    "75_pct": int(remplissage_pct > 0.75)
                },
                "battery": 95,
                "timestamp": time.time()
            }

            client.publish(TOPIC, json.dumps(payload))

        # Pause de 10s pour observer
        time.sleep(10)


if __name__ == "__main__":
    load_sensors()

    t = threading.Thread(target=simulation_loop)
    t.daemon = True
    t.start()

    app.run(host='0.0.0.0', port=5000)