import time
import json
import random
import paho.mqtt.client as mqtt

# --- CONFIG MQTT ---
# "mosquitto" est le nom du conteneur dans le réseau Docker
MQTT_BROKER = "mosquitto" 
TOPIC_RAW = "bin/raw_signals"

# --- CONFIGURATION PHYSIQUE (Standard 120L) ---
STANDARD_DIMS = {"h": 93, "w": 48, "d": 55} # cm

POUBELLES_CONFIG = [
    {"id": "PBL-SOPH-01", "type": "Verre",      "daily_growth": 10, "density": 0.35, "dims": STANDARD_DIMS, "coords": {"lat": 43.6165, "lon": 7.0725}},
    {"id": "PBL-SOPH-02", "type": "Recyclable", "daily_growth": 40, "density": 0.05, "dims": STANDARD_DIMS, "coords": {"lat": 43.6155, "lon": 7.0715}},
    {"id": "PBL-SOPH-03", "type": "Organique",  "daily_growth": 50, "density": 0.50, "dims": STANDARD_DIMS, "coords": {"lat": 43.6170, "lon": 7.0730}},
    {"id": "PBL-SOPH-04", "type": "Recyclable", "daily_growth": 25, "density": 0.05, "dims": STANDARD_DIMS, "coords": {"lat": 43.6150, "lon": 7.0710}},
    {"id": "PBL-SOPH-05", "type": "Verre",      "daily_growth": 15, "density": 0.35, "dims": STANDARD_DIMS, "coords": {"lat": 43.6160, "lon": 7.0740}}
]

# --- ÉTAT INTERNE ---
bin_states = {p['id']: {"current_level": random.randint(0, 50), "battery": 100} for p in POUBELLES_CONFIG}
TICKS_PER_DAY = 30 / 2 # 1 jour = 30s, update = 2s

def generer_rafale(valeur_theorique, type_capteur):
    """Génère 10 mesures brutes avec du bruit"""
    mesures = []
    for _ in range(10):
        valeur = valeur_theorique
        # Bruit naturel
        if type_capteur == "us": valeur += random.uniform(-1.0, 1.0)
        elif type_capteur == "weight": valeur += random.uniform(-0.1, 0.1)
        
        # Bruit accidentel (10% de chance)
        if random.random() < 0.1:
            if type_capteur == "us": valeur = random.choice([0, 500, valeur + 30])
            elif type_capteur == "weight": valeur += 5.0
            
        mesures.append(max(0, round(valeur, 2)))
    return mesures

# --- CONNEXION MQTT ---
try:
    client = mqtt.Client()
    client.connect(MQTT_BROKER, 1883, 60)
    print(" [Service 1] Connecté au Broker MQTT (Mosquitto)")
except Exception as e:
    print(f" [Service 1] Erreur Connexion MQTT: {e}")
    exit()

print(" Démarrage simulation physique...")

while True:
    for config in POUBELLES_CONFIG:
        state = bin_states[config['id']]
        
        #Évolution Physique (Remplissage)
        state['current_level'] += (config['daily_growth'] / TICKS_PER_DAY) * random.uniform(0.8, 1.2)
        if state['current_level'] >= 100: 
            print(f"* VIDAGE {config['id']}")
            state['current_level'] = 0.0
        
        state['battery'] -= 0.05
        if state['battery'] <= 0: state['battery'] = 100

        # Calcul des Valeurs Théoriques
        vol_total_L = (config['dims']['h'] * config['dims']['w'] * config['dims']['d']) / 1000.0
        vol_actuel_L = vol_total_L * (state['current_level'] / 100.0)
        
        dist_theorique = config['dims']['h'] - ((state['current_level'] / 100.0) * config['dims']['h'])
        poids_theorique = vol_actuel_L * config['density']

        # Génération des Rafales (Données Sales)
        payload = {
            "id": config["id"],
            "type": config["type"],
            "coords": config["coords"],
            "raw_us": generer_rafale(dist_theorique, "us"),      # Liste de 10 valeurs
            "raw_weight": generer_rafale(poids_theorique, "weight"), # Liste de 10 valeurs
            "battery": int(state['battery'])
        }

        client.publish(TOPIC_RAW, json.dumps(payload))
        print(f" [Raw] {config['id']} : Envoi rafales (US: {payload['raw_us'][:3]}...)")

    time.sleep(2)
