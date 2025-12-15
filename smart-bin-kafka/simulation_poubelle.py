import time
import json
import random
import statistics
from kafka import KafkaProducer

# --- CONFIGURATION KAFKA ---
TOPIC_NAME = "bin-sensor-data"
KAFKA_SERVER = "localhost:9092" 

print(f" Démarrage de la Simulation Avancée sur {KAFKA_SERVER}...")

try:
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_SERVER,
        value_serializer=lambda x: json.dumps(x).encode('utf-8')
    )
    print(" Connecté à Kafka.")
except Exception as e:
    print(f"Erreur Kafka : {e}")
    exit()

# --- CONFIGURATION PHYSIQUE STANDARD ---
STANDARD_DIMS = {"h": 93, "w": 48, "d": 55}

# Liste des poubelles
POUBELLES_CONFIG = [
    # 1. Verre (Lourd )
    {"id": "PBL-SOPH-01", "type": "Verre", "daily_growth": 10, "density": 0.35, 
     "dims": STANDARD_DIMS, "coords": {"lat": 43.6165, "lon": 7.0725}},
    
    # 2. Recyclable (Léger)
    {"id": "PBL-SOPH-02", "type": "Recyclable", "daily_growth": 40, "density": 0.05, 
     "dims": STANDARD_DIMS, "coords": {"lat": 43.6155, "lon": 7.0715}},
    
    # 3. Organique (Très Lourd )
    {"id": "PBL-SOPH-03", "type": "Organique", "daily_growth": 50, "density": 0.50, 
     "dims": STANDARD_DIMS, "coords": {"lat": 43.6170, "lon": 7.0730}},
    
    # 4. Recyclable (Léger )
    {"id": "PBL-SOPH-04", "type": "Recyclable", "daily_growth": 25, "density": 0.05, 
     "dims": STANDARD_DIMS, "coords": {"lat": 43.6150, "lon": 7.0710}},
    
    # 5. Verre (Lourd)
    {"id": "PBL-SOPH-05", "type": "Verre", "daily_growth": 15, "density": 0.35, 
     "dims": STANDARD_DIMS, "coords": {"lat": 43.6160, "lon": 7.0740}}
]

# --- ÉTAT INTERNE ---
bin_states = {}
for p in POUBELLES_CONFIG:
    bin_states[p['id']] = {
        "current_level": random.randint(0, 50), 
        "battery": random.randint(80, 100)
    }

UPDATE_INTERVAL_SEC = 2
DAY_DURATION_SEC = 30
TICKS_PER_DAY = DAY_DURATION_SEC / UPDATE_INTERVAL_SEC

class EdgeSensorSystem:
    def _simuler_rafale_brute(self, valeur_theorique, type_capteur):
        """Génère 10 valeurs brutes (avec bruit possible)"""
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

    def _filtrer_et_moyenniser(self, liste_valeurs):
        """Filtre Médian"""
        if not liste_valeurs: return 0.0
        mediane = statistics.median(liste_valeurs)
        valeurs_propres = [v for v in liste_valeurs if abs(v - mediane) <= 10.0]
        if not valeurs_propres: valeurs_propres = [mediane]
        return round(statistics.mean(valeurs_propres), 2)

    def acquisition_cycle(self, config, state):
        """Calcul Physique complet"""
        
        #  Calcul du Volume Actuel (Litres)
        vol_total_cm3 = config['dims']['h'] * config['dims']['w'] * config['dims']['d']
        vol_total_litres = vol_total_cm3 / 1000.0
        
        # Volume rempli actuel
        vol_actuel_litres = vol_total_litres * (state['current_level'] / 100.0)

        # Calcul Physique Théorique
        # Distance = Hauteur totale - Hauteur des déchets
        hauteur_dechets = (state['current_level'] / 100.0) * config['dims']['h']
        dist_theorique = config['dims']['h'] - hauteur_dechets
        
        # Poids = Volume (L) * Densité (kg/L)
        poids_theorique = vol_actuel_litres * config['density']

        # Acquisition Capteurs (Rafale)
        us_final = self._filtrer_et_moyenniser(self._simuler_rafale_brute(dist_theorique, "us"))
        poids_final = self._filtrer_et_moyenniser(self._simuler_rafale_brute(poids_theorique, "weight"))
        
        #JSON Final
        return {
            "bin_id": config["id"],
            "bin_type": config["type"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "GPS_value": config["coords"],
            "dimensions": config["dims"], 
            "sensors": [
                {"id": "US-01", "type": "ultrasonic", "value": us_final, "unit": "cm"},
                {"id": "US-02", "type": "ultrasonic", "value": us_final, "unit": "cm"},
                {"id": "IR-25", "type": "infrared", "value": 1 if state['current_level'] > 25 else 0, "threshold": 25},
                {"id": "IR-50", "type": "infrared", "value": 1 if state['current_level'] > 50 else 0, "threshold": 50},
                {"id": "IR-75", "type": "infrared", "value": 1 if state['current_level'] > 75 else 0, "threshold": 75},
                
                {"id": "LC", "type": "load_cell", "value": poids_final, "unit": "kg"}
            ],
            "status": {"battery_level": int(state['battery'])}
        }

# --- MAIN LOOP ---
sensor_system = EdgeSensorSystem()

try:
    while True:
        print("\n" + "="*60)
        for config in POUBELLES_CONFIG:
            state = bin_states[config['id']]
            
            # Simulation vie
            growth = (config['daily_growth'] / TICKS_PER_DAY) * random.uniform(0.8, 1.2)
            state['current_level'] += growth
            
            if state['current_level'] >= 100: 
                print(f" VIDAGE {config['id']} (Capacité: {int((config['dims']['h']*config['dims']['w']*config['dims']['d'])/1000)}L)")
                state['current_level'] = 0.0
            
            state['battery'] -= 0.05
            if state['battery'] <= 0: state['battery'] = 100

            # Acquisition
            message = sensor_system.acquisition_cycle(config, state)
            producer.send(TOPIC_NAME, value=message)
            
            # Affichage console détaillé
            poids = next(s['value'] for s in message['sensors'] if s.get('id') == 'LC')
            dist = next(s['value'] for s in message['sensors'] if s['id']=='US-01')
            
            print(f"{config['id']} ({config['type']}): {int(state['current_level'])}% Plein | Dist {dist}cm | Poids {poids}kg")
            
        producer.flush()
        time.sleep(UPDATE_INTERVAL_SEC)

except KeyboardInterrupt:
    print("\nArrêt.")
    producer.close()
