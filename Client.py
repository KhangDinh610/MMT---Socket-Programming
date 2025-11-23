import os
import socket
import sys
import threading
import time
import tkinter.messagebox as tkMessageBox
from tkinter import *

from PIL import Image, ImageTk

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"


class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.frameNbr = 0
        self.frameBuffer = {}	# Buffer để reassemble fragments
        # Kiểm soát các gói tin
        self.stats = {
            'packets_received': 0,
            'packets_lost': 0,
            'bytes_received': 0,
            'start_time': time.time()
        }

    def createWidgets(self):
        """Build GUI."""
        self.setup = Button(self.master, width=20, padx=3, pady=3, text="Setup", command=self.setupMovie)
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(self.master, width=20, padx=3, pady=3, text="Play", command=self.playMovie)
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(self.master, width=20, padx=3, pady=3, text="Pause", command=self.pauseMovie)
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(self.master, width=20, padx=3, pady=3, text="Teardown", command=self.exitClient)
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W + E + N + S, padx=5, pady=5)

    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Teardown button handler."""
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except Exception:
            pass

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        """Play button handler."""
        if self.state == self.READY:
            threading.Thread(target=self.listenRtp).start()
            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

    def listenRtp(self):
        """Listen for RTP packets."""
        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    currFrameNbr = rtpPacket.seqNum()
                    marker = rtpPacket.marker()
                    payload = rtpPacket.getPayload()
                    
                # Tính frame number và fragment index
                frameNum = currFrameNbr // 1000
                fragmentIdx = currFrameNbr % 1000
                
                # Khởi tạo buffer cho frame mới
                if frameNum not in self.frameBuffer:
                    self.frameBuffer[frameNum] = {}
                
                # Lưu fragment
                self.frameBuffer[frameNum][fragmentIdx] = payload
                
                # Nếu là fragment cuối cùng (marker = 1)
                if marker == 1:
                    # Reassemble toàn bộ frame
                    complete_frame = self.reassembleFrame(frameNum)
                    if complete_frame and frameNum > self.frameNbr:
                        self.frameNbr = frameNum
                        self.updateMovie(self.writeFrame(complete_frame))
                        
                        # Xóa frame cũ khỏi buffer
                        self.cleanupBuffer(frameNum)
            except:
                if self.playEvent.is_set(): # Method isSet đã bị loại bỏ từ py 3.10, dùng is_set thay thế
                    break
                if self.teardownAcked == 1:
                    try:
                        self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                    self.rtpSocket.close()
                    break

    def writeFrame(self, data):
        """Write received frame to a temp image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(cachename, "wb") as file:
            file.write(data)
        return cachename

    def updateMovie(self, imageFile):
        """Update GUI label with a video frame."""
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo  # type: ignore

    def connectToServer(self):
        """Connect to the Server and start RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except Exception:
            tkMessageBox.showwarning("Connection Failed", f"Connection to '{self.serverAddr}' failed.")

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""
        requestStandardTemplate = (
            "{requestCode} {filePath} RTSP/1.0\r\n"
            "CSeq: {sequenceNumber}\r\n"
            "Session: {session}\r\n"
        )
        requestSetupTemplate = (
            "SETUP {filePath} RTSP/1.0\r\n"
            "CSeq: {sequenceNumber}\r\n"
            "Transport: RTP/UDP;client_port={port}\r\n"
        )
        request = ""
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            self.rtspSeq = 1
            request = requestSetupTemplate.format(
                filePath=self.fileName, sequenceNumber=self.rtspSeq, port=self.rtpPort
            )
            self.requestSent = self.SETUP
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = requestStandardTemplate.format(
                requestCode="PLAY",
                filePath=self.fileName,
                sequenceNumber=self.rtspSeq,
                session=self.sessionId,
            )
            self.requestSent = self.PLAY
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = requestStandardTemplate.format(
                requestCode="PAUSE",
                filePath=self.fileName,
                sequenceNumber=self.rtspSeq,
                session=self.sessionId,
            )
            self.requestSent = self.PAUSE
        elif requestCode == self.TEARDOWN and self.state != self.INIT:
            self.rtspSeq += 1
            request = requestStandardTemplate.format(
                requestCode="TEARDOWN",
                filePath=self.fileName,
                sequenceNumber=self.rtspSeq,
                session=self.sessionId,
            )
            self.requestSent = self.TEARDOWN
        else:
            return

        self.rtspSocket.send(request.encode("utf-8"))
        print("\nData sent:\n" + request)

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            reply = self.rtspSocket.recv(1024)
            if reply:
                self.parseRtspReply(reply.decode("utf-8"))
            if self.requestSent == self.TEARDOWN:
                try:
                    self.rtspSocket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                self.rtspSocket.close()
                break

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        lines = data.split("\n")
        seqNum = int(lines[1].split(" ")[1])
        if seqNum == self.rtspSeq:
            session = int(lines[2].split(" ")[1])
            if self.sessionId == 0:
                self.sessionId = session
            if self.sessionId == session:
                if int(lines[0].split(" ")[1]) == 200:
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        self.openRtpPort()
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY
                        self.playEvent.set()
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1

    def openRtpPort(self):
        """Open RTP socket bound to a specified port."""
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(("0.0.0.0", self.rtpPort))
        except Exception:
            tkMessageBox.showwarning("Unable to Bind", f"Unable to bind PORT={self.rtpPort}")

    def handler(self):
        """Handle window close event."""
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()

    def reassembleFrame(self, frameNum):
        """Reassemble fragments thành complete frame."""
        if frameNum not in self.frameBuffer:
            return None
		
        fragments = self.frameBuffer[frameNum]
		# Sắp xếp theo fragment index
        sorted_fragments = [fragments[i] for i in sorted(fragments.keys())]
		
        return b''.join(sorted_fragments)

    def cleanupBuffer(self, currentFrame):
        """Xóa old frames khỏi buffer."""
        frames_to_delete = [f for f in self.frameBuffer.keys() if f < currentFrame - 2]
        for f in frames_to_delete:
            del self.frameBuffer[f]
            
    def updateStats(self, seqNum, dataSize):
        """Track network statistics."""
        self.stats['packets_received'] += 1
        self.stats['bytes_received'] += dataSize
        
        # Detect packet loss
        expected_seq = self.lastSeqNum + 1 if hasattr(self, 'lastSeqNum') else seqNum
        if seqNum > expected_seq:
            self.stats['packets_lost'] += (seqNum - expected_seq)
        
        self.lastSeqNum = seqNum
        
    def getStatistics(self):
        """Calculate streaming statistics."""
        elapsed = time.time() - self.stats['start_time']
        bitrate = (self.stats['bytes_received'] * 8) / elapsed / 1000  # kbps
        loss_rate = self.stats['packets_lost'] / (self.stats['packets_received'] + self.stats['packets_lost']) * 100
        
        return {
            'bitrate_kbps': bitrate,
            'packet_loss_percent': loss_rate,
            'total_packets': self.stats['packets_received'],
            'lost_packets': self.stats['packets_lost']
        }