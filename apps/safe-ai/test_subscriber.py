import zmq

context = zmq.Context()
socket = context.socket(zmq.SUB)
socket.connect("tcp://localhost:5400")
socket.setsockopt_string(zmq.SUBSCRIBE, "")

while True:
    message = socket.recv_string()
    print(f"📥 Received: {message}")
