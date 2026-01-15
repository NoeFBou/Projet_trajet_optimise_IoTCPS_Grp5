import json
import time
import random
import os
import paho.mqtt.client as mqtt

# CONFIGURATION
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto-edge")
TOPIC = "bin/sensors_values"
MEASURES_SIZE = 10 
DELAY = 1 # 1 pour 1 seconde

# Theoretical sensor height range for calculating logical ir states
SENSOR_MAX_RANGE = 93.0 # cm 
SENSOR_US_MIN = 10.0     # cm



# DATA GENERATION FUNCTIONS
def generate_weight_and_us_values(target_value, variation_pct=0.05, aberration_prob=0.1):
    """ Generate a tab of measures of possible noisy float values """
    values = []
    for _ in range(MEASURES_SIZE):
        if random.random() < aberration_prob:
            val = target_value * random.choice([0.1, 3.0]) 
        else:
            noise = random.uniform(-variation_pct, variation_pct)
            val = target_value * (1.0 + noise)
        values.append(max(0.0, round(val, 2)))
    return values

def generate_ir_measures(measured_distance, position_pct, max_range, noise_prob=0.1):
    """ Generate logical IR measures based on the target distance and senso ranges  """
    # Calculate the threshold distance for this sensor
    threshold_distance = max_range * (1.0 - position_pct)
    
    # Compare measured distance to threshold
    if measured_distance < threshold_distance:
        main_state = 1
    else:
        main_state = 0
    
    # Generate measures
    measures_values = []
    for _ in range(MEASURES_SIZE):
        if random.random() < noise_prob:
            measures_values.append(1 - main_state) 
        else:
            measures_values.append(main_state)      
    return measures_values

# SIMULATION LOOP
def run_simulation():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    
    while True:
        try:
            client.connect(MQTT_BROKER, 1883, 900)
            print("[Simulator] Connected to MQTT Broker.")
            break
        except Exception:
            print("[Simulator Error] MQTT connection failed. Retrying in 5s...")
            time.sleep(5)
    client.loop_start()
    while True:
        # Generate a random distance seen by the sensor
        target_distance = random.uniform(SENSOR_US_MIN, SENSOR_MAX_RANGE) 
        target_weight = random.uniform(0.0, 50.0)

        #  Generate ultrasound and weight measures
        raw_us_values = generate_weight_and_us_values(target_distance)
        raw_us_values_2 = generate_weight_and_us_values(target_distance)
        raw_weight_values = generate_weight_and_us_values(target_weight)

        # Generate IR measures based on the target distance
        ir_75_measures = generate_ir_measures(target_distance, 0.75, SENSOR_MAX_RANGE)
        ir_50_measures = generate_ir_measures(target_distance, 0.50, SENSOR_MAX_RANGE)
        ir_25_measures = generate_ir_measures(target_distance, 0.25, SENSOR_MAX_RANGE)

        # Build payload to publish in MQTT
        payload = {
            "us1": raw_us_values, # in cm
            "us2": raw_us_values_2,  # in cm
            "weight": raw_weight_values, # in kg
            "ir_levels": {
                "ir_75": ir_75_measures,  # 1 if above 75% of the bin, 0 otherwise
                "ir_50": ir_50_measures,  # 1 if above 50% of the bin, 0 otherwise
                "ir_25": ir_25_measures   # 1 if above 25% of the bin, 0 otherwise
            },
            "battery_percentage": random.randint(20, 100) # in %
        }

        # Publish to MQTT and printing values for monitoring
        info = client.publish(TOPIC, json.dumps(payload), qos=2, retain=True)
        info.wait_for_publish()
        print(f"[Simulator] Published new measures :\n US: {raw_us_values} cm\n US_2: {raw_us_values_2} cm\n Weight: {raw_weight_values} kg\n IR75: {ir_75_measures}\n IR50: {ir_50_measures}\n IR25: {ir_25_measures}\n battery: {payload['battery_percentage']}%")

        time.sleep(DELAY)

if __name__ == "__main__":
    run_simulation()