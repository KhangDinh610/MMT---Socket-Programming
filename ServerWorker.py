import socket
import sys
import threading
import traceback
from random import randint

import RequestParser
from RequestParser import RequestParser
from RtpPacket import RtpPacket
from VideoStream import VideoStream

WAIT_TIME = 0.03


class ServerWorker:
    SETUP = "SETUP"
    PLAY = "PLAY"
    PAUSE = "PAUSE"
    TEARDOWN = "TEARDOWN"

    INIT = 0
    READY = 1
    PLAYING = 2

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2

    # No class-level clientInfo – keep everything inside self.

    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        self.state = self.INIT  # Ensure state is instance-specific!

    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()

    def recvRtspRequest(self):
        """Receive RTSP request from the client."""
        connSocket = self.clientInfo["rtspSocket"][0]
        while True:
            data = connSocket.recv(1024)
            if data:
                print("Data received:\n" + data.decode("utf-8"))
                self.processRtspRequest(data)

    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        # Split request into lines and parse headers
        parser = RequestParser(data)

        if not parser:
            return

        seq = parser.seq
        session = parser.session
        rtp_port = parser.rtp_port
        requestType = parser.requestType
        filename = parser.filename

        # Process SETUP request
        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP\n")
                try:
                    self.clientInfo["videoStream"] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq)
                    return

                # Generate a randomized RTSP session ID
                self.clientInfo["session"] = randint(100000, 999999)

                # Keep RTP port for later
                if rtp_port:
                    self.clientInfo["rtpPort"] = int(rtp_port)

                # Send RTSP reply
                self.replyRtsp(self.OK_200, seq)

        # Process PLAY request
        elif requestType == self.PLAY:
            if (
                self.state == self.READY
                and session
                and int(session) == self.clientInfo["session"]
            ):
                print("processing PLAY\n")
                self.state = self.PLAYING

                # Create RTP socket
                self.clientInfo["rtpSocket"] = socket.socket(
                    socket.AF_INET, socket.SOCK_DGRAM
                )

                self.replyRtsp(self.OK_200, seq)

                # Thread for sending RTP
                self.clientInfo["event"] = threading.Event()
                self.clientInfo["worker"] = threading.Thread(target=self.sendRtp)
                self.clientInfo["worker"].start()

        # Process PAUSE request
        elif requestType == self.PAUSE:
            if (
                self.state == self.PLAYING
                and session
                and int(session) == self.clientInfo["session"]
            ):
                print("processing PAUSE\n")
                self.state = self.READY

                self.clientInfo["event"].set()
                self.replyRtsp(self.OK_200, seq)

        # Process TEARDOWN request
        elif requestType == self.TEARDOWN:
            if session and int(session) == self.clientInfo["session"]:
                print("processing TEARDOWN\n")
                if "event" in self.clientInfo:
                    self.clientInfo["event"].set()
                self.replyRtsp(self.OK_200, seq)
                if "rtpSocket" in self.clientInfo:
                    self.clientInfo["rtpSocket"].close()

    def sendRtp(self):
        """Send RTP packets over UDP."""
        while True:
            event = self.clientInfo.get("event")
            if event is None:
                break
            event.wait(WAIT_TIME)
            # Stop sending if PAUSE/TEARDOWN is set
            if event.is_set():
                break

            videoStream = self.clientInfo.get("videoStream")
            if videoStream is None:
                break

            data = videoStream.nextFrame()
            if data:
                frameNumber = videoStream.frameNbr()
                print(f"FRAME NUM: {frameNumber}")
                try:
                    address = self.clientInfo["rtspSocket"][1][0]
                    port = int(self.clientInfo["rtpPort"])
                    packet = self.makeRtp(data, frameNumber)
                    self.clientInfo["rtpSocket"].sendto(packet, (address, port))
                except Exception as e:
                    print("Connection Error:", e)
            else:
                print("ENDED")
                break  # End of stream

    def makeRtp(self, payload, frameNbr):
        """RTP-packetize the video data."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        marker = 0
        pt = 26  # MJPEG type
        seqnum = frameNbr
        ssrc = 0

        rtpPacket = RtpPacket()
        rtpPacket.encode(
            version, padding, extension, cc, seqnum, marker, pt, ssrc, payload
        )
        return rtpPacket.getPacket()

    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        connSocket = self.clientInfo["rtspSocket"][0]
        if code == self.OK_200:
            reply = (
                "RTSP/1.0 200 OK\r\n"
                "CSeq: " + str(seq) + "\r\n"
                "Session: " + str(self.clientInfo["session"]) + "\r\n"
            )
            connSocket.send(reply.encode())
        elif code == self.FILE_NOT_FOUND_404:
            reply = "RTSP/1.0 404 NOT FOUND\r\n" "CSeq: " + str(seq) + "\r\n"
            connSocket.send(reply.encode())
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            reply = "RTSP/1.0 500 CONNECTION ERROR\r\n" "CSeq: " + str(seq) + "\r\n"
            connSocket.send(reply.encode())
            print("500 CONNECTION ERROR")
