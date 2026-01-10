import json
import statistics
import time
import os
import requests
import threading
import paho.mqtt.client as mqtt
from typing import List, Dict, Any

# CONFIGURATION
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto-edge")
MQTT_TOPIC_IN = "bin/sensors_values"
REST_API_URL = os.getenv("REST_API_URL", "http://anomalies-detector:5000/detect_anomaly")
SIM = os.getenv("SIM", "true")

# Load Configuration (annotations of the data with new values from config.json)
CONFIG_FILE = "config.json"
bin_counter = 0
config_list = []
def load_config_list():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                print(f"[Filter Service] Successfully loaded bin configurations.")
                return data
            else:
                print("[Filter Warning] Config file is not a list")
                return []
    except Exception as e:
        print(f"[Filter Warning] Error reading config file: {e}")
        return []



# FILTERING LOGIC
def filter_weight_and_us_values(values,tolerance) :
    print(f"[Filter Service] Raw values: {values}")
    if not values: # Should not happen
        return 0.0
    # Find median
    median_val = statistics.median(values)
    print(f"[Filter Service] Median: {median_val}")
    
    # Keep only values within tolerance of median
    clean_values = [v for v in values if abs(v - median_val) <= tolerance]
    print(f"[Filter Service] Clean values: {clean_values}")
    # If there are no clean values, return the median
    if not clean_values: 
        return round(median_val, 2)
    # Smooth the result using the mean
    return round(statistics.mean(clean_values), 2)

def filter_ir_values(binary_list):
    if not binary_list: 
        return 0
    return statistics.mode(binary_list)

# TRANSMIT TO ANOMALY DETECTION API
def send_to_api(payload):
    count = 10
    while count > 0:
        try:
            resp = requests.post(REST_API_URL, json=payload, timeout=60)
            if resp.status_code in [200, 201]:
                print(f"[Filter Service] Data sent to API successfully: {resp.status_code}")
                break
            else:
                print(f"[Filter Service Error] API Error {resp.status_code}: {resp.text}, Retrying...")
        except Exception as e:
            print(f"[Filter Service Error] Error sending data to API: {e}")
        count -= 1
        time.sleep(5)

# MQQT CONSUMER CALLBACK
def on_message(client, userdata, msg):
    global bin_counter 
    global config_list
    try:
        # extract simulator data from mosquitto
        raw = json.loads(msg.payload.decode())
        
        # Apply filters on the sensors data
        us1_clean = filter_weight_and_us_values(raw.get('us1', []), 5.0)
        print(f"[Filter Service] US1 Cleaned: {us1_clean}")
        us2_clean = filter_weight_and_us_values(raw.get('us2', []), 5.0)
        print(f"[Filter Service] US2 Cleaned: {us2_clean}")
        w_clean = filter_weight_and_us_values(raw.get('weight', []), 0.5)
        print(f"[Filter Service] Weight Cleaned: {w_clean}")
        
        ir = raw.get('ir_levels', {})
        ir_75 = filter_ir_values(ir.get('ir_75', []))
        print(f"[Filter Service] IR75 Cleaned: {ir_75}")
        ir_50 = filter_ir_values(ir.get('ir_50', []))
        print(f"[Filter Service] IR50 Cleaned: {ir_50}")
        ir_25 = filter_ir_values(ir.get('ir_25', []))
        print(f"[Filter Service] IR25 Cleaned: {ir_25}")

        # Construct Base Payload
        processed_data = {
            "us1": us1_clean,
            "us2": us2_clean,
            "weight": w_clean,
            "ir_levels": {
                "level_75": ir_75,
                "level_50": ir_50,
                "level_25": ir_25
            },
            "timestamp": time.time(),
            "battery_percentage": raw.get('battery_percentage', -1)
        }

        # Merge the bin configuration directly into the root of the payload
        if SIM.lower() == "true":
            current_index = bin_counter  % len(config_list)
            current_config = config_list[current_index]
            processed_data.update(current_config)
            bin_counter += 1
        else:
            current_config = config_list[0]
        processed_data.update(current_config)     
        # Send to anomaly Detection API
        print(f"[Filter Service] Processed Data: {processed_data}")
        t = threading.Thread(target=send_to_api, args=(processed_data,))
        t.start()

    except Exception as e:
        print(f"[Filter Service Error] Error processing MQTT message: {e}")

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[Filter Service] Connected to MQTT Broker!")
        client.subscribe(MQTT_TOPIC_IN, qos=2)
        print(f"[Filter Service] Subscribed to {MQTT_TOPIC_IN}")
    else:
        print(f"[Filter Service Error] Connection failed with code {reason_code}")

def on_disconnect(client, userdata, reason_code, properties):
    print(f"[Filter Service] Disconnected from MQTT broker, reason_code={reason_code}")

if __name__ == '__main__':
    config_list = load_config_list()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    
    print(f"[Filter Service] Connecting to {MQTT_BROKER}...")
    try:
        client.connect(MQTT_BROKER, 1883, 900)
        client.loop_forever()
    except Exception as e:
        print(f"[Filter Service Error] Could not connect: {e}")