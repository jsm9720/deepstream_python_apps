# 6 (camera 6)
#python3 deepstream_test1_rtsp_in_rtsp_out.py -i rtsp://admin:total!23@192.168.0.101:554 rtsp://admin:total!23@192.168.0.102:554 rtsp://admin:total!23@192.168.0.103:554 rtsp://admin:total!23@192.168.0.104:554 rtsp://admin:total!23@192.168.0.105:554 rtsp://admin:total!23@192.168.0.106:554/trackID=1 -g nvinfer

# 2 (camera 2)
python3 deepstream_test1_rtsp_in_zmq_out.py -i rtsp://admin:total!23@192.168.0.101:554 rtsp://admin:total!23@192.168.0.102:554 -g nvinfer
