import json
import statistics
import time
import paho.mqtt.client as mqtt

MQTT_BROKER = "mosquitto"
TOPIC_IN = "bin/raw_signals"
TOPIC_OUT = "bin/filtered_data"

def filtrer_signal(liste_valeurs):
    """Filtre Médian + Moyenne"""
    if not liste_valeurs: return 0.0
    
    mediane = statistics.median(liste_valeurs)
    valeurs_propres = [v for v in liste_valeurs if abs(v - mediane) <= 10.0]
    
    if not valeurs_propres: valeurs_propres = [mediane] 
    
    return round(statistics.mean(valeurs_propres), 2)

def on_message(client, userdata, msg):
    try:
        raw = json.loads(msg.payload.decode())
        
        # 1. Traitement
        us_clean = filtrer_signal(raw['raw_us'])
        weight_clean = filtrer_signal(raw['raw_weight'])
        
        # 2. Construction JSON
        clean_data = {
            "bin_id": raw['id'],
            "bin_type": raw['type'],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "GPS_value": raw['coords'],
            "sensors": [
                {"id": "US-01", "type": "ultrasonic", "value": us_clean, "unit": "cm"},
                {"id": "US-02", "type": "ultrasonic", "value": us_clean, "unit": "cm"},
                {"id": "LC", "type": "load_cell", "value": weight_clean, "unit": "kg"}
            ],
            "status": {
                "battery_level": raw['battery'],
                "quality": "filtered_v2"
            }
        }
        
        client.publish(TOPIC_OUT, json.dumps(clean_data))
        print(f"[Edge] {raw['id']} : Filtré ! US={us_clean}cm (Brut: {raw['raw_us'][0]})")
        
    except Exception as e:
        print(f"Erreur traitement: {e}")

try:
    client = mqtt.Client()
    
  
    client.on_message = on_message 
    
    client.connect(MQTT_BROKER,1883, 60)
    client.subscribe(TOPIC_IN)
    print("[Service 2] Edge Computer démarré (Filtre Actif)")
    client.loop_forever()
except Exception as e:
    print(f"Erreur connexion: {e}")
