import time
import os
import requests
import threading
from flask import Flask, request, jsonify

# CONFIGURATION 
FUSION_API_URL = os.getenv("FUSION_API_URL", "http://fusion-service:6000/receive_data")
API_PORT = 5000

app = Flask(__name__)

# BUSINESS LOGIC
def fill_level_us(distance_cm, height):
    """Converts ultrasonic distance into fill percentage"""
    if height <= 0:
        return 0
    # Example: If bin is 100cm tall and distance is 20cm, it is 80% full.
    level = 100 - (distance_cm * 100 / height)
    return max(0, min(100, level))

def max_volume(height, width, depth):
    """Calculates theoretical maximum volume in cm³"""
    return height * width * depth

def current_volume(level_percent, volume_max):
    """Calculates estimated current volume in cm³"""
    return volume_max * (level_percent / 100)

def extract_bin_data(json_input):
    # Dimensions 
    dims = json_input.get("dims", {})
    height = dims.get("h", 0)
    width = dims.get("w", 0)
    depth = dims.get("d", 0)

    # Sensor Values
    us1 = json_input.get("us1", 0)
    us2 = json_input.get("us2", 0)
    weight = json_input.get("weight", 0) 
    ir_data = json_input.get("ir_levels", {})
    ir25 = ir_data.get("level_25", 0)
    ir50 = ir_data.get("level_50", 0)
    ir75 = ir_data.get("level_75", 0)

    return us1, us2, ir25, ir50, ir75, weight, height, width, depth

def detect_anomaly(data_tuple):
    us1, us2, ir25, ir50, ir75, weight, height, width, depth = data_tuple

    anomaly = False
    reason = "None"
    
    # Average of the two ultrasonic sensors
    level_us1 = fill_level_us(us1, height)
    level_us2 = fill_level_us(us2, height)

    if abs(level_us1 - level_us2) > 5 :
        anomaly = True
        reason = "Ultrasonic Sensors Inconsistent"
    avg_level = (level_us1 + level_us2) / 2

    vol_max = max_volume(height, width, depth)
    vol_current = current_volume(avg_level, vol_max)



    # Case 1 : IR Inconsistency (Top IR active, Middle/Bottom inactive)
    if ir75 == 1 and ir50 == 0:
        anomaly = True
        reason = "IR Incoherent (75% ON, 50% OFF)"
    elif ir50 == 1 and ir25 == 0:
        anomaly = True
        reason = "IR Incoherent (50% ON, 25% OFF)"

    # Case 2 : IR vs Ultrasonic Mismatch
    elif ir75 == 1 and avg_level < 50:
        anomaly = True
        reason = "Mismatch (IR 75% vs avg US)"
    elif ir75 == 0 and avg_level > 90:
        anomaly = True
        reason = "Mismatch (IR Low vs avg US)"
        
    return anomaly, reason, avg_level, vol_current, vol_max

def send_fusion_api(enriched_data):
    count = 10
    while count > 0:
        try:
            resp = requests.post(FUSION_API_URL, json=enriched_data, timeout=10)
            if resp.status_code in [200, 201]:
                print(f"[Anomaly Service] Sent to fusion service: {resp.status_code}")
                return
            else:
                print(f"[Anomaly Service Error] Request to fusion service error {resp.status_code}")
        except Exception as e:
            print(f"[Anomaly Service Error] Fusion Service unreachable: {e}")
        
        count -= 1
        time.sleep(2)

# API ENDPOINT
@app.route('/detect_anomaly', methods=['POST'])
def detect_anomaly_endpoint():
    try:
        # Retrieve JSON payload from request
        input_data = request.get_json()
        if not input_data:
            return jsonify({"error": "Empty payload"}), 400

        # Extract relevant data
        data_tuple = extract_bin_data(input_data)
        
        # Detect anomaly
        is_anomaly, reason, fill_percent, curr_vol, max_vol = detect_anomaly(data_tuple)

        # Enrich original data with results
        enriched_data = input_data.copy() 
        
        enriched_data.update({
            "is_anomaly": is_anomaly,
            "anomaly_reason": reason,
            "fill_level_percent": round(fill_percent, 2),
            "volume_cm3": round(curr_vol, 2),
            "max_volume_cm3": max_vol,
        })

        print(f"[Anomaly Service]  Data after anomalies detection : {enriched_data}")
        t = threading.Thread(target=send_fusion_api, args=(enriched_data,))
        t.start()
        # Return success response
        return '', 200

    except Exception as e:
        print(f"[Anomaly Critical Error] {e}")
        return '', 500
    
if __name__ == "__main__":
    print(f"Starting Anomaly Detector on port {API_PORT}...")
    app.run(host='0.0.0.0', port=API_PORT, debug=True)