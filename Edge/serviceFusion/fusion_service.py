import json
import time
import os
import paho.mqtt.client as mqtt
from flask import Flask, request

from level_calculator_with_dempster_shafer import WasteBinMonitor

# CONFIGURATION 
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto-fog")
MQTT_PORT = 1883
MQTT_TOPIC_OUT = "city/bin"
API_PORT = 6000

# INITIALIZATION 
app = Flask(__name__)
monitor = WasteBinMonitor()
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)


# BUSINESS LOGIC
def process_fusion(data):

    bin_id = data.get('id', 'unknown_bin')
    ir_data = data.get('ir_levels', {})
    
    # Construct the input object expected by WasteBinMonitor
    monitor_inputs = {
        'type': data.get('type', 'all_type'),
        'weight': round( data.get('weight', 0.0), 2),
        'us1': round(data.get('us1', 0.0), 2),
        'us2': round(data.get('us2', 0.0), 2),
        'ir25': 1 if ir_data.get('level_25') == 1 else 0,
        'ir50': 1 if ir_data.get('level_50') == 1 else 0,
        'ir75': 1 if ir_data.get('level_75') == 1 else 0
    }

    # DEMPSTER-SHAFER FUSION
    final_state, final_mass = monitor.compute_level(monitor_inputs)
    print(f"[Fusion Service] Bin {bin_id} - Final State: {final_state}, Masses: {final_mass}")
    conflict = final_mass.get(frozenset(), 0.0)
    print(f"[Fusion Service] Bin {bin_id} - Conflict: {conflict:.2f}")

    # Prepare output message
    output_message = {
        "bin_id": bin_id,
        "timestamp": time.time(),

        # Fusion Results
        "level_code": final_state,      
        "level_desc": f"Calculated State: {final_state} (Conflict: {conflict:.2f})",
        
        # Pass through useful metadata for the dashboard
        "weight_kg": monitor_inputs['weight'],
        "coords": data.get('coords'),
        "zone": data.get('zone'),
        "us1_cm": monitor_inputs['us1'],
        "us2_cm": monitor_inputs['us2'],
        "ir_levels": ir_data,
        "battery_percentage": data.get('battery_percentage', -1),
        
        # Include anomaly info received from the previous step
        "anomaly_detected": data.get('is_anomaly', False),
        "anomaly_reason": data.get('anomaly_reason', "None"),
        "volume_cm3": data.get('volume_cm3', 0.0),
        "max_volume_cm3": data.get('max_volume_cm3', 0.0),
        
    }
    # PUBLISH TO MQTT
    payload = json.dumps(output_message)
    info = mqtt_client.publish(MQTT_TOPIC_OUT, payload, qos=2, retain=True)
    info.wait_for_publish()
    
    print(f"[Fusion Service] Published fused data for Bin {bin_id} to MQTT Topic '{MQTT_TOPIC_OUT}': {output_message}")
    return output_message



# FLASK ROUTES
@app.route('/receive_data', methods=['POST'])
def receive_data_endpoint():
    """HTTP Endpoint called by Anomaly Detector"""

    data = request.get_json()
    if not data:
        return '', 400
    
    result = process_fusion(data)
    
    if result:
        # Return success to the caller
        return '', 200
    else:
        return '', 500
 
if __name__ == "__main__":
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, 1884, 900)
            mqtt_client.loop_start()
            print("[Fusion Service] Connected to MQTT Broker.")
            break
        except Exception:
            print("[Fusion Service Error] MQTT connection failed. Retrying in 5s...")
            time.sleep(5)
    print(f"Starting Fusion Service on port {API_PORT}...")
    app.run(host='0.0.0.0', port=API_PORT, debug=True)