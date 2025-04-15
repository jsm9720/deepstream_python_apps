import sys
sys.path.append("../")
from common.platform_info import PlatformInfo
import pyds
import gi
gi.require_version("Gst","1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import Gst


def ch_newpad(decodebin, decoder_src_pad, data):
    print(" In ch_newpad\n")
    caps = decoder_src_pad.get_current_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    source_bin = data
    features = caps.get_features(0)

    print("gstname=", gstname)
    if gstname.find("video") != -1:
        print("features=", features)
        if features.contains("memory:NVMM"):
            bin_ghost_pad = source_bin.get_static_pad("src")
            if not bin_ghost_pad.set_target(decoder_src_pad):
                sys.stderr.write(
                    "Failed to link decoder src pad to source bin ghost pad\n"
                )
        else:
            sys.stderr.write(
                "Error: Decodebin did not pick nvidia decoder plugin.\n"
            )


def decodebin_child_added(child_proxy, Object, name, user_data):
    print("Creating child added:", name, "\n")
    if name.find("decodebin") != -1:
        Object.connect("child-added", decodebin_child_added, user_data)


def create_source_bin(index, uri):
    print("Creating souce bin")
    bin_name = "source-bin-%02d" % index
    print(bin_name)

    nbin = Gst.bin.new(bin_name)
    if not nbin:
        sys.stderr.write(" Unable to create source bin \n")

    uri_decode_bin = Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")
    if not nbin:
        sys.stderr.write(" Unable to create uri decode bin \n")
    
    uri_decode_bin.set("uri", uri)

    uri_decode_bin.connect("pad-added", ch_newpad, nbin)
    uri_decode_bin.connect("child-added",decodebin_child_added, nbin)

    Gst.Bin.add(nbin, uri_decode_bin)
    bin_pad = nbin.add_pad(Gst.GhostPad.new_no_target("src", Gst.PadDirection.SRC))
    if not bin_pad:
        sys.stderr.wirte(" Failed to add ghost pad in source bin \n")
        return None
    return nbin


def main(args):
    number_sources = len(args)

    platform_info = PlatformInfo()
    Gst.init(None)

    print("Creating Pipeline \n")
    pipeline = Gst.Pipeline()
    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")
    
    print("Creating streamux \n")
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux")
    pipeline.add(streammux)

    for i in range(number_sources):
        print("Creating source_bin ", i, " \n ")
        uri_name = args[i]
        source_bin = create_source_bin(i, uri_name)
        if source_bin_bin:
            sys.stderr.write("Unable to create source bin")
        pipline.add(source_bin)

        padname = "sink_%u" % i
        sinkpad = streammux.request_pad_simple(padname)
        if not sinkpad:
            sys.stderr.write("Unable to create sink pad bin \n")

        srcpad = source_bin.get_static_pad("src")
        if not srcpad:
            sys.stderr.write("Unable to create src pad bin \n")
        srcpad.link(sinkpad)

    print("Creating Pgie \n")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    if not pgie:
        sys.stderr.write(" Unable to create pgie \n")
    
    print("Creating nvtracker \n")
    tracker = Gst.ElementFactory.make("nvtracker", "tracker")
    if not tracker:
        sys.stderr.write(" Unable to create tracker \n")