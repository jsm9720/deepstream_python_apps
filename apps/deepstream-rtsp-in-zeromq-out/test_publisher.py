import zmq
import time
import random
import json

# ZeroMQ PUB 설정
context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.bind("tcp://*:5555")

while True:
    data = {
        "camera_id": random.randint(1, 36),
        "object_id": random.randint(1000, 9999),
        "label": random.choice(["person", "car", "dog"]),
        "confidence": round(random.uniform(0.5, 1.0), 2),
        "bbox": {
            "x": random.randint(0, 640),
            "y": random.randint(0, 480),
            "width": random.randint(50, 200),
            "height": random.randint(50, 200),
        }
    }

    json_data = json.dumps(data)
    print(f"📤 Sent: {json_data}")
    socket.send_string(json_data)

    time.sleep(0.5)
