import time
import json
import random
import statistics
from kafka import KafkaProducer


TOPIC_NAME = "bin-sensor-data"

KAFKA_SERVER = "localhost:9092" 


POUBELLES_CONFIG = [
    {"id": "PBL-SOPH-12A7", "type": "Verre", "coords": {"lat": 43.6158, "lon": 7.0722}},
    {"id": "PBL-ANT-03X9",  "type": "Plastique", "coords": {"lat": 43.5800, "lon": 7.1200}},
    {"id": "PBL-NICE-55B2", "type": "Papier", "coords": {"lat": 43.7102, "lon": 7.2620}},
    {"id": "PBL-CANNES-01", "type": "Verre", "coords": {"lat": 43.5528, "lon": 7.0174}},
    {"id": "PBL-GRASSE-99", "type": "OrdureMenagere", "coords": {"lat": 43.6602, "lon": 6.9264}}
]

class EdgeProcessor:
    def __init__(self):

        self.history = {} 

    def _generer_mesure_brute(self, poubelle_config):
        """Simule le capteur physique (avec du bruit possible sur les 2 capteurs)"""
        
   
        niveau_reel = random.randint(20, 80)

        if random.random() < 0.1:
            niveau_mesure = random.choice([0, 150]) 
        else:
            niveau_mesure = niveau_reel + random.randint(-2, 2)


        poids_reel = random.uniform(5.0, 30.0)

        if random.random() < 0.1:
            poids_mesure = 0.0
        else:
            poids_mesure = poids_reel + random.uniform(-0.5, 0.5)
        
        return {
            "us_raw": niveau_mesure,
            "weight_raw": round(poids_mesure, 2),
            "battery_raw": random.randint(20, 100)
        }

    def filtrer_donnees(self, bin_id, raw_data):
       
        if bin_id not in self.history:
            self.history[bin_id] = {'us': [], 'weight': []}

      
        val_us = raw_data['us_raw']
        val_weight = raw_data['weight_raw']
        
        hist_us = self.history[bin_id]['us']
        hist_weight = self.history[bin_id]['weight']

     
        if len(hist_us) >= 3:
            moyenne_us = statistics.mean(hist_us)
            if abs(val_us - moyenne_us) > 30: # Écart > 30cm = Bruit
                print(f"   [Filtre US] Rejet {val_us}cm. Remplacement par {moyenne_us:.1f}cm")
                val_us = round(moyenne_us, 1)
        
       
        if len(hist_weight) >= 3:
            moyenne_weight = statistics.mean(hist_weight)
            if abs(val_weight - moyenne_weight) > 10.0: # Écart > 10kg = Bruit
                print(f"   [Filtre Poids] Rejet {val_weight}kg. Remplacement par {moyenne_weight:.1f}kg")
                val_weight = round(moyenne_weight, 2)

        # Mise à jour des historiques (fenêtre glissante de 5)
        hist_us.append(val_us)
        hist_weight.append(val_weight)
        
        if len(hist_us) > 5: hist_us.pop(0)
        if len(hist_weight) > 5: hist_weight.pop(0)
            
        return {
            "us_filtered": val_us,
            "weight_filtered": val_weight,
            "battery": raw_data['battery_raw']
        }

    def annoter_donnees(self, config, filtered_data):
        """SERVICE D'ANNOTATION"""
        annotation = {
            "bin_id": config["id"],
            "bin_type": config["type"],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "GPS_value": config["coords"],
            "sensors": [
                {"id": "US-01", "type": "ultrasonic", "value": filtered_data["us_filtered"], "unit": "cm"},
                {"type": "load_cell", "value": filtered_data["weight_filtered"], "unit": "kg"}
            ],
            "status": {
                "battery_level": filtered_data["battery"],
                "quality": "filtered_v2"
            }
        }
        return annotation



print(f" Démarrage Edge Simulation (5 Poubelles) sur {KAFKA_SERVER}...")

try:
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_SERVER,
        value_serializer=lambda x: json.dumps(x).encode('utf-8')
    )
    print(" Connecté à Kafka.")
except Exception as e:
    print(f" Erreur Kafka : {e}")
    exit()

processor = EdgeProcessor()

print(" Début de la collecte (Intervalle: 60s)...")

try:
    while True:

        for config in POUBELLES_CONFIG:
            
            
            raw = processor._generer_mesure_brute(config)
            
            
            clean_data = processor.filtrer_donnees(config["id"], raw)
            
           
            message_final = processor.annoter_donnees(config, clean_data)
            
            
            producer.send(TOPIC_NAME, value=message_final)
            
            print(f" [EDGE] {config['id']} ({config['type']}) : Niv {clean_data['us_filtered']}cm | Poids {clean_data['weight_filtered']}kg")
        
        producer.flush()
        
        
        print(" Attente 60 secondes...")
        time.sleep(60)

except KeyboardInterrupt:
    print("\nArrêt.")
    producer.close()
