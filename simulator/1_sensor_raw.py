import time
import json
import random
import paho.mqtt.client as mqtt
import os

# --- CONFIG MQTT ---
MQTT_BROKER = "mosquitto"
TOPIC_RAW = "bin/raw_signals"

# --- DÉFINITIONS PHYSIQUES ---
# Mapping des dimensions (H, W, D en cm)
DIMS_DEFINITIONS = {
    "STANDARD_DIMS": {"h": 93, "w": 48, "d": 55},
    "LARGE_DIMS": {"h": 110, "w": 60, "d": 70}
}

TICKS_PER_DAY = 30 / 2  # Vitesse de simulation


# --- FONCTIONS UTILITAIRES ---

def load_config(filepath="..\dataset\simulation_iot_poubelles_light.json"):
    """Charge la configuration depuis un fichier JSON"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            # Si le JSON est un objet unique, on le met dans une liste
            if isinstance(config_data, dict):
                config_data = [config_data]
            return config_data
    except FileNotFoundError:
        print(f"ERREUR: Le fichier {filepath} est introuvable.")
        exit()
    except json.JSONDecodeError:
        print(f"ERREUR: Le fichier {filepath} n'est pas un JSON valide.")
        exit()


def generer_rafale(valeur_theorique, type_capteur):
    """Génère 10 mesures brutes avec du bruit (US ou Poids)"""
    mesures = []
    for _ in range(10):
        valeur = valeur_theorique
        # Bruit naturel
        if type_capteur == "us":
            valeur += random.uniform(-1.0, 1.0)
        elif type_capteur == "weight":
            valeur += random.uniform(-0.1, 0.1)

        # Bruit accidentel (10% de chance)
        if random.random() < 0.1:
            if type_capteur == "us":
                valeur = random.choice([0, 500, valeur + 30])
            elif type_capteur == "weight":
                valeur += 5.0

        mesures.append(max(0, round(valeur, 2)))
    return mesures


def simuler_capteurs_ir(niveau_remplissage_pct):
    """
    Simule 3 capteurs IR (25%, 50%, 75%).
    Renvoie une liste [IR_LOW, IR_MID, IR_HIGH] (0 ou 1).
    Ajoute du bruit pour simuler des déchets non homogènes.
    """
    seuils = [25, 50, 75]
    resultats = []

    for seuil in seuils:
        # Simulation d'hétérogénéité :
        # Les déchets ne sont pas plats. Parfois un déchet dépasse vers le haut (+10%)
        # Parfois il y a un trou (-5%).
        variation_surface = random.uniform(-5.0, 15.0)
        niveau_percu = niveau_remplissage_pct + variation_surface

        # Simulation d'erreur capteur (saleté sur la lentille - 1% de chance)
        if random.random() < 0.01:
            lecteur = random.choice([0, 1])
        else:
            lecteur = 1 if niveau_percu >= seuil else 0

        resultats.append(lecteur)

    return resultats


# --- INITIALISATION ---

# 1. Chargement Config
configs = load_config()

# 2. Initialisation État Interne
# On utilise le current_level du JSON (qui est en Litres/Volume) pour définir le % de départ
bin_states = {}
for conf in configs:
    # Récupération des dimensions physiques
    dim_key = conf.get("dims", "STANDARD_DIMS")
    phys_dims = DIMS_DEFINITIONS.get(dim_key, DIMS_DEFINITIONS["STANDARD_DIMS"])

    # Calcul pourcentage initial (Volume Actuel / Capacité Max * 100)
    # Si max_capacity n'est pas défini, on estime via les dimensions
    cap_max = conf.get("max_capacity", (phys_dims['h'] * phys_dims['w'] * phys_dims['d']) / 1000)
    level_start = (conf.get("current_level", 0) / cap_max) * 100

    bin_states[conf['id']] = {
        "fill_pct": min(100.0, max(0.0, level_start)),  # Niveau en % (0-100)
        "battery": 100,
        "phys_dims": phys_dims,
        "max_capacity": cap_max
    }

# 3. Connexion MQTT
try:
    client = mqtt.Client()
    client.connect(MQTT_BROKER, 1883, 60)
    print(" [Service] Connecté au Broker MQTT")
except Exception as e:
    print(f" [Service] Erreur Connexion MQTT: {e}")
    # On continue pour tester la logique même sans MQTT si besoin (ou exit)
    # exit()

print(f" Démarrage simulation pour {len(configs)} poubelle(s)...")

# --- BOUCLE PRINCIPALE ---
while True:
    for conf in configs:
        b_id = conf['id']
        state = bin_states[b_id]
        dims = state['phys_dims']

        # --- A. SIMULATION PHYSIQUE (Interne) ---

        # Le daily_growth est souvent en Litres, on le convertit en % de la capacité totale
        growth_pct = (conf['daily_growth'] / state['max_capacity']) * 100

        # Ajout du remplissage (avec facteur aléatoire 0.8 à 1.2)
        state['fill_pct'] += (growth_pct / TICKS_PER_DAY) * random.uniform(0.8, 1.2)

        # Gestion vidage
        if state['fill_pct'] >= 100:
            print(f"* VIDAGE DETECTÉ : {b_id}")
            state['fill_pct'] = 0.0

        # Batterie
        state['battery'] -= 0.05
        if state['battery'] <= 0: state['battery'] = 100

        # --- B. SIMULATION CAPTEURS (Génération données) ---

        # 1. Ultrason (US) : Mesure le vide restant en cm
        # Distance = Hauteur totale - Hauteur des déchets
        hauteur_dechets_cm = (state['fill_pct'] / 100.0) * dims['h']
        dist_us_theorique = dims['h'] - hauteur_dechets_cm

        # 2. Poids : Volume * Densité
        vol_actuel_L = state['max_capacity'] * (state['fill_pct'] / 100.0)
        poids_theorique = vol_actuel_L * conf['density']

        # 3. Infrarouge (Nouveau) : 3 niveaux
        ir_readings = simuler_capteurs_ir(state['fill_pct'])

        # --- C. CONSTRUCTION PAYLOAD ---
        # On injecte les champs statiques du JSON + les données dynamiques
        payload = {
            "id": conf["id"],
            "type": conf["type"],
            "zone": conf.get("zone", "Unknown"),  # Nouveau champ
            "coords": conf["coords"],

            # Données Capteurs simulées
            "raw_us": generer_rafale(dist_us_theorique, "us"),
            "raw_weight": generer_rafale(poids_theorique, "weight"),
            "ir_levels": {  # Nouveau bloc IR
                "25_pct": ir_readings[0],
                "50_pct": ir_readings[1],
                "75_pct": ir_readings[2]
            },
            "battery": int(state['battery']),
            "timestamp": time.time()
        }

        # Note: On NE renvoie PAS state['fill_pct'] ni 'current_level' brut.
        # Le backend devra déduire le niveau via raw_us ou ir_levels.

        client.publish(TOPIC_RAW, json.dumps(payload))
        #print(f" [Emit] {b_id} | US_avg: {sum(payload['raw_us']) / 10:.1f}cm | IR: {ir_readings}")

    time.sleep(2)