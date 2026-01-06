from confluent_kafka import Consumer, KafkaError
import json

# Utility functions
def fill_level_us(distance_cm, height):
    """Convert ultrasonic distance to fill percentage."""
    level = 100 - (distance_cm * 100 / height)
    return max(0, min(100, level))

def max_volume(height, width, depth):
    """Calculate maximum volume in cm³."""
    return height * width * depth

def current_volume(level_percent, volume_max):
    """Calculate current volume from max volume and fill percentage."""
    return volume_max * (level_percent / 100)

def detect_anomaly(data):
    """Detect anomalies in bin data."""
    us1, us2, ir25, ir50, ir75, weight, height, width, depth = data

    avg_level = (fill_level_us(us1, height) + fill_level_us(us2, height)) / 2
    vol_max = max_volume(height, width, depth)
    vol_current = current_volume(avg_level, vol_max)

    anomaly = False

    # 1. Inconsistent IR readings
    if ir75 == 1 and ir50 == 0:
        anomaly = True
    if ir50 == 1 and ir25 == 0:
        anomaly = True

    # 2. IR vs Ultrasonic inconsistency
    if ir75 == 1 and avg_level < 50 or ir75 == 0 and avg_level > 70:
        anomaly = True
    if ir50 == 1 and avg_level < 30 or ir50 == 0 and avg_level > 50:
        anomaly = True
    if ir25 == 1 and avg_level < 10 or ir25 == 0 and avg_level > 20:
        anomaly = True

    return anomaly, avg_level, vol_current, vol_max, weight

def extract_bin_data(json_input):
    sensors = json_input.get("sensors", [])
    sensor_map = {s.get("id"): s.get("value") for s in sensors if "id" in s}

    height = json_input.get("height", 0)
    width = json_input.get("width", 0)
    depth = json_input.get("depth", 0)
 
    formatted_list = [
        sensor_map.get("US-01", 0),
        sensor_map.get("US-02", 0),
        sensor_map.get("IR-25", 0),
        sensor_map.get("IR-50", 0),
        sensor_map.get("IR-75", 0),
        sensor_map.get("LC", 0),
        height, 
        width,
        depth
    ]

    return formatted_list

if __name__ == "__main__":
    # Kafka Consumer Configuration
    conf = {
        'bootstrap.servers': 'localhost:9092',  # Replace with your Kafka broker
        'group.id': 'bin-anomaly-detector',
        'auto.offset.reset': 'earliest'         # Explanation below
    }

    consumer = Consumer(conf)
    topic = 'bin-sensor-data'
    consumer.subscribe([topic])

    print(f"Subscribed to topic '{topic}'. Waiting for messages...\n")

    # Main loop to consume messages
    try:
        while True:
            msg = consumer.poll(timeout=None)  # passive wait
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    print("Kafka error:", msg.error())
                continue

            # Assume message is JSON: [ultrasonic_1, ultrasonic_2, ir25, ir50, ir75, weight, height, width, depth]
            try:
                data = json.loads(msg.value().decode('utf-8'))
            except json.JSONDecodeError:
                print("JSON decode error:", msg.value())
                continue

            data = extract_bin_data(data)
            anomaly, level, vol_current, vol_max, weight = detect_anomaly(data)
            consumer.commit(message=msg)
            print(f"\n--- New message ---")
            print(f"Received data: {data}")
            print(f"Estimated fill level: {level:.1f}%")
            print(f"Current volume: {vol_current:.1f} cm³ / Max volume: {vol_max} cm³")
            print("ANOMALY DETECTED!" if anomaly else "OK")

    except KeyboardInterrupt:
        print("\nStopping consumer...")

    finally:
        consumer.close()
