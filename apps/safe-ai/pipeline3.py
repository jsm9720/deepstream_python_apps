import os
import configparser
import math
from datetime import datetime, timezone
import time
import sys
sys.path.append("../")

from collections import defaultdict
from common.bus_call import bus_call
from common.platform_info import PlatformInfo
import pyds
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

from shapely.geometry import Point, Polygon
import zmq


# EVENT_CLASS = ["Bicycle", "Car", "Person", "RoadSign"] # Unit Test
EVENT_CLASS = ["OTHER", "OTHER", "NO_HELMET", "OTHER", "INVADE"] # Integration Test
RECORD_START_THRESHOLD = 3
RECORD_STOP_THRESHOLD = 3
ZM_PORT = 15400
FILE_OUTPUT = "/data/events/cache"
zone_info = {0:Polygon([(1261.7794189453125, 229.26934814453125), (1261.7794189453125, 990.0), (1562.9775390625, 1080.0), (1562.0, 100.0)])}
input_file_list = [
        "rtsp://admin:total!23@192.168.0.201:554",
        "rtsp://admin:total!23@192.168.0.202:554",
        "rtsp://admin:total!23@192.168.0.205:554/trackID=1",
        "rtsp://admin:total!23@192.168.0.201:554",
        "rtsp://admin:total!23@192.168.0.202:554",
        "rtsp://admin:total!23@192.168.0.205:554/trackID=1",
        "rtsp://admin:total!23@192.168.0.201:554",
        "rtsp://admin:total!23@192.168.0.202:554",
        "rtsp://admin:total!23@192.168.0.205:554/trackID=1",
        "rtsp://admin:total!23@192.168.0.201:554",
        "rtsp://admin:total!23@192.168.0.202:554",
        "rtsp://admin:total!23@192.168.0.205:554/trackID=1",
]


class PipelineContext:
    def __init__(self, zmq_socket):
        self.zmq_socket = zmq_socket
        self.object_tracker = defaultdict(lambda: {
            "first_seen": None,
            "last_seen": None,
            "is_recording": False,
            "event_id": None,
            "objet_id": None,
        })


def cb_newpad(decodebin, decoder_src_pad,data):
    print("In cb_newpad\n")
    caps=decoder_src_pad.get_current_caps()
    if not caps:
        caps = decoder_src_pad.query_caps()
    gststruct=caps.get_structure(0)
    gstname=gststruct.get_name()
    source_bin=data
    features=caps.get_features(0)

    print("gstname=",gstname)
    if(gstname.find("video")!=-1):
        if features.contains("memory:NVMM"):
            bin_ghost_pad=source_bin.get_static_pad("src")
            if not bin_ghost_pad.set_target(decoder_src_pad):
                sys.stderr.write("Failed to link decoder src pad to source bin ghost pad\n")
        else:
            sys.stderr.write(" Error: Decodebin did not pick nvidia decoder plugin.\n")


def decodebin_child_added(child_proxy,Object,name,user_data):
    print("Decodebin child added:", name, "\n")
    if(name.find("decodebin") != -1):
        Object.connect("child-added",decodebin_child_added,user_data)

    if "source" in name:
        source_element = child_proxy.get_by_name("source")
        if source_element.find_property('drop-on-latency') != None:
            Object.set_property("drop-on-latency", True)


def create_source_bin(index, uri):
    print("Creating source bin")
    bin_name="source-bin-%02d" %index
    print(bin_name)
    nbin=Gst.Bin.new(bin_name)
    if not nbin:
        sys.stderr.write(" Unable to create source bin \n")

    uri_decode_bin=Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")
    if not uri_decode_bin:
        sys.stderr.write(" Unable to create uri decode bin \n")
    uri_decode_bin.set_property("uri",uri)
    uri_decode_bin.connect("pad-added",cb_newpad,nbin)
    uri_decode_bin.connect("child-added",decodebin_child_added,nbin)

    Gst.Bin.add(nbin,uri_decode_bin)
    bin_pad = nbin.add_pad(Gst.GhostPad.new_no_target("src",Gst.PadDirection.SRC))
    if not bin_pad:
        sys.stderr.write(" Failed to add ghost pad in source bin \n")
        return None
    return nbin


def format_location_full_callback(splitmux, fragment_id, sample, out_path):
    timestamp = int(time.time())
    return f"{out_path}/{timestamp}.ts"


def create_record_bin(cam_idx: int, output_path: str) -> Gst.Bin:
    print("Creating record bin")
    bin_name="record-bin-%02d" %cam_idx

    record_bin = Gst.Bin.new(bin_name)
    if not record_bin:
        sys.stderr.write(" Unable to create record bin \n")

    queue = Gst.ElementFactory.make("queue", None)
#    queue.set_property("max-size-buffers", 1)
#    queue.set_property("leaky", 2)

    conv = Gst.ElementFactory.make("nvvideoconvert", None)
    
    nvosd = Gst.ElementFactory.make("nvdsosd", None)
    nvosd.set_property('process-mode', 1)
    nvosd.set_property('display-text', 1)

    capsfilter = Gst.ElementFactory.make("capsfilter", None)
    caps = Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12")
    capsfilter.set_property("caps", caps)

    enc = Gst.ElementFactory.make("nvv4l2h264enc", None)

    parser = Gst.ElementFactory.make("h264parse", None)

    sink = Gst.ElementFactory.make("splitmuxsink", None)
    sink.set_property("max-size-time", 5 * Gst.SECOND)
    sink.set_property("muxer-factory", "mpegtsmux")
    sink.set_property("async-finalize", True)
    sink.connect("format-location-full", format_location_full_callback, output_path)
    
    for elem in [queue, conv, nvosd, capsfilter, enc, parser, sink]:
        if not elem:
            raise RuntimeError("Failed to create GStreamer element")
        record_bin.add(elem)
#        elem.sync_state_with_parent()

    Gst.Element.link_many(queue, conv, nvosd, capsfilter, enc, parser)
    video_pad = sink.request_pad_simple("video")
    parser.get_static_pad("src").link(video_pad)

    bin_pad = queue.get_static_pad("sink")
    ghost_pad = Gst.GhostPad.new("sink", bin_pad)
    record_bin.add_pad(ghost_pad)

    return record_bin


def start_event(ctx: PipelineContext, cam_idx, event_id, object_id):

    cur_time = datetime.now(timezone.utc).isoformat()
    print(f"[{cur_time}][START] cam{cam_idx} cam{cam_idx} event{event_id}")

    ctx.zmq_socket.send_json({
        "objectId": str(object_id),
        # "cameraId": str(cam_idx), # Integration Test
        "cameraId": '1', # Unit Test
        "timestamp": cur_time,
        "action": "START",
        "eventType": EVENT_CLASS[event_id],
    })


def stop_event(ctx: PipelineContext, cam_idx, event_id, object_id):
    
    cur_time = datetime.now(timezone.utc).isoformat()
    
    print(f"[{cur_time}][STOP] cam{cam_idx} cam{cam_idx} event{event_id}")

    ctx.zmq_socket.send_json({
        "objectId": str(object_id),
        # "cameraId": str(cam_idx), # Integration Test
        "cameraId": '1', # Unit Test
        "timestamp": cur_time,
        "action": "END",
        "eventType": EVENT_CLASS[event_id],
    })


def is_point_in_polygon(point, polygon):
    """
    point: (x, y)
    polygon: [(x1, y1), (x2, y2), ..., (xn, yn)]  (꼭짓점 순서대로)
    """
    x, y = point
    n = len(polygon)
    inside = False

    px1, py1 = polygon[0]
    for i in range(1, n + 1):
        px2, py2 = polygon[i % n]  # 다각형은 마지막 꼭짓점에서 첫 꼭짓점으로 닫힘
        if y > min(py1, py2):
            if y <= max(py1, py2):
                if x <= max(px1, px2):
                    if py1 != py2:
                        xinters = (y - py1) * (px2 - px1) / (py2 - py1) + px1
                    if px1 == px2 or x <= xinters:
                        inside = not inside
        px1, py1 = px2, py2

    return inside


def invasion(point, zone):
    return zone.contains(point)


def is_center_inside(inner, outer):
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def conv_src_pad_buffer_probe(pad, info, ctx):
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        return Gst.PadRobeReturn.OK

    current_time = time.time()
    seen_keys = set()

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        cam_idx = frame_meta.source_id
        l_obj = frame_meta.obj_meta_list

        person_bboxes = []
        safe_hat_bboxes = []

        while l_obj:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break

            class_id = obj_meta.class_id
            object_id = obj_meta.object_id

            left = obj_meta.rect_params.left
            top = obj_meta.rect_params.top
            width = obj_meta.rect_params.width
            height = obj_meta.rect_params.height
            bbox = (left, top, left + width, top + height)
            # print(cam_idx, class_id, object_id)

            if class_id == 0: # safe_hat
                safe_hat_bboxes.append(bbox)

            if class_id in [0, 1, 2, 3] and object_id != -1:
                if class_id == 2:  # Person
                    # Depending on the event type, consider saving the person bbox or not.
                    person_bboxes.append((object_id, bbox))

                    # if shapely is heavy, have to make algorithm
                    zone = zone_info.get(cam_idx)
                    if zone is not None:
                        if invasion(Point(left + width/2, top + height), zone):
                            class_id = 4
                        else:
                            try:
                                l_obj = l_obj.next
                            except StopIteration:
                                break
                            continue

                key = (cam_idx, class_id, object_id)
                seen_keys.add(key)
                tracker = ctx.object_tracker[key]

                if tracker["first_seen"] is None:
                    tracker["first_seen"] = current_time
                tracker["last_seen"] = current_time

                if not tracker["is_recording"] and current_time - tracker["first_seen"] >= RECORD_START_THRESHOLD:
                    start_event(ctx, cam_idx, class_id, object_id)
                    tracker["is_recording"] = True
                    tracker["event_id"] = class_id
                    tracker["object_id"] = object_id

            try:
                l_obj = l_obj.next
            except StopIteration:
                break
        # safe hat process
        class_id = 2
        if len(safe_hat_bboxes) != 0:
            for obj_id, person_bbox in person_bboxes:
                for safe_hat_bbox in safe_hat_bboxes:
                    if not is_center_inside(safe_hat_bbox, person_bbox):
                        key = (cam_idx, class_id, obj_id)
                        seen_keys.add(key)
                        tracker = ctx.object_tracker[key]

                        if tracker["first_seen"] is None:
                            tracker["first_seen"] = current_time
                        tracker["last_seen"] = current_time

                        if not tracker["is_recording"] and current_time - tracker["first_seen"] >= RECORD_START_THRESHOLD:
                            start_event(ctx, cam_idx, class_id, obj_id)
                            tracker["is_recording"] = True
                            tracker["event_id"] = class_id
                            tracker["object_id"] = obj_id
        else:
            for obj_id, person_bbox in person_bboxes:
                key = (cam_idx, class_id, obj_id)
                seen_keys.add(key)
                tracker = ctx.object_tracker[key]

                if tracker["first_seen"] is None:
                    tracker["first_seen"] = current_time
                tracker["last_seen"] = current_time

                if not tracker["is_recording"] and current_time - tracker["first_seen"] >= RECORD_START_THRESHOLD:
                    start_event(ctx, cam_idx, class_id, obj_id)
                    tracker["is_recording"] = True
                    tracker["event_id"] = class_id
                    tracker["object_id"] = obj_id

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    for key, tracker in list(ctx.object_tracker.items()):
        if tracker["is_recording"]:
            cam_idx, class_id, object_id = key
            if key not in seen_keys:
                if tracker["last_seen"] and current_time - tracker["last_seen"] >= RECORD_STOP_THRESHOLD:
                    stop_event(ctx, cam_idx, class_id, object_id)
                    ctx.object_tracker.pop(key)
        else:
            if tracker["last_seen"] and current_time - tracker["last_seen"] > 1.5:
                ctx.object_tracker.pop(key)

    return Gst.PadProbeReturn.OK


def main():
    context = zmq.Context()
    zmq_socket = context.socket(zmq.PUB)
    zmq_socket.bind(f"tcp://*:{ZM_PORT}")

    number_sources = len(input_file_list)

    Gst.init(None)

    print("Creating Pipeline \n ")
    pipeline = Gst.Pipeline.new("deepstream-event-pipeline")
    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")

    ctx = PipelineContext(zmq_socket)

    print("Creating streammux \n ")
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux \n")
    streammux.set_property('width', 1920)
    streammux.set_property('height', 1080)
    streammux.set_property('batch-size', number_sources)
    streammux.set_property('batched-push-timeout', 4000000)
    pipeline.add(streammux)

    for i in range(number_sources):
        print("Creating source_bin ",i," \n ")
        uri_name = input_file_list[i]
        if uri_name.find("rtsp://") == 0 :
            is_live = True
        source_bin = create_source_bin(i, uri_name)
        if not source_bin:
            sys.stderr.write("Unable to create source bin \n")
        pipeline.add(source_bin)
        padname="sink_%u" %i
        sinkpad= streammux.request_pad_simple(padname)
        if not sinkpad:
            sys.stderr.write("Unable to create sink pad bin \n")
        srcpad=source_bin.get_static_pad("src")
        if not srcpad:
            sys.stderr.write("Unable to create src pad bin \n")
        srcpad.link(sinkpad)
        
    print("Creating nvinfer (PGIE) \n ")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    if not pgie:
        sys.stderr.write(" Unable to create pgie \n")
    pgie.set_property('config-file-path', "dstest1_pgie_config.txt")
    pgie_batch_size = pgie.get_property("batch-size")
    if(pgie_batch_size != number_sources):
        print("WARNING: Overriding infer-config batch-size", pgie_batch_size, " with number of sources ", number_sources," \n")
        pgie.set_property("batch-size", number_sources)
    pipeline.add(pgie)

    print("Creating nvtracker\n ")
    tracker = Gst.ElementFactory.make("nvtracker", "tracker")
    if not tracker:
        sys.stderr.write(" Unable to create tracker \n")

    config = configparser.ConfigParser()
    config.read("dstest2_tracker_config.txt")
    config.sections()

    for key in config['tracker']:
        if key == 'tracker-width' :
            tracker_width = config.getint('tracker', key)
            tracker.set_property('tracker-width', tracker_width)
        if key == 'tracker-height' :
            tracker_height = config.getint('tracker', key)
            tracker.set_property('tracker-height', tracker_height)
        if key == 'gpu-id' :
            tracker_gpu_id = config.getint('tracker', key)
            tracker.set_property('gpu_id', tracker_gpu_id)
        if key == 'll-lib-file' :
            tracker_ll_lib_file = config.get('tracker', key)
            tracker.set_property('ll-lib-file', tracker_ll_lib_file)
        if key == 'll-config-file' :
            tracker_ll_config_file = config.get('tracker', key)
            tracker.set_property('ll-config-file', tracker_ll_config_file)
    pipeline.add(tracker)

    print("Creating nvvidconv \n ")
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    if not nvvidconv:
        sys.stderr.write(" Unable to create nvvidconv \n")
    pipeline.add(nvvidconv)

    streammux.link(pgie)
    pgie.link(tracker)
    tracker.link(nvvidconv)

    print("Creating streamdemux \n ")
    streamdemux = Gst.ElementFactory.make("nvstreamdemux", "Stream-demux")
    if not streamdemux:
        sys.stderr.write(" Unable to create tee \n") 
    pipeline.add(streamdemux)

    nvvidconv.link(streamdemux)

    conv_src_pad = nvvidconv.get_static_pad("src")
    if not conv_src_pad:
        sys.stderr.write(" Unable to get conv src pad \n")
    else:
        conv_src_pad.add_probe(Gst.PadProbeType.BUFFER, conv_src_pad_buffer_probe, ctx)

    for i in range(number_sources):
        padname = f"src_{i}"
        demuxsrcpad = streamdemux.request_pad_simple(padname)
        if not demuxsrcpad:
            sys.stderr.write("Unable to create demux src pad \n")

        output_path = f"{FILE_OUTPUT}/{i}"
        os.makedirs(output_path, exist_ok=True) # change camera name

        print("Creating record_bin ", i," \n")
        record_bin = create_record_bin(i, output_path)
        if not record_bin:
            sys.stderr.write("Unable to create record bin \n")

        pipeline.add(record_bin)
        record_bin.sync_state_with_parent()

        recordsinkpad = record_bin.get_static_pad("sink")
        if not recordsinkpad:
            sys.stderr.write("Unable to create record sink pad \n")
        demuxsrcpad.link(recordsinkpad)

    loop = GLib.MainLoop() # Create a mainloop
    bus = pipeline.get_bus() # Retrieve the bus from the pipeline
    bus.add_signal_watch() # Add a watch for new messages on the bus
    bus.connect ("message", bus_call, loop) # Connect the loop to the callback function

    print("Starting pipeline \n")
    pipeline.set_state(Gst.State.PLAYING)

    # Gst.debug_bin_to_dot_file(pipeline, Gst.DebugGraphDetails.ALL, "my_pipeline") # visualization

    try:
        loop.run()
    except:
        pass
    finally:
        print("[INFO] Stopping pipeline...")
        pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    main()
