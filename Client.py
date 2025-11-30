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
        """Initialize the client."""
        self.master = master
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        
        # RTSP variables
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        
        # Video variables
        self.frameNbr = 0
        # frameBuffer không cần thiết nữa với scheme mới
        
        # Control flags
        self.rtspThreadRunning = True
        self.rtpThreadRunning = False
        
        # Statistics
        self.stats = {
            'packets_received': 0,
            'packets_lost': 0,
            'bytes_received': 0,
            'start_time': time.time()
        }
        self.lastSeqNum = -1
    
        # Setup GUI and connect
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.connectToServer()

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
        print(f"setupMovie called, current state: {self.state}, INIT state: {self.INIT}")
        if self.state == self.INIT:
            print("Sending SETUP request...")
            self.sendRtspRequest(self.SETUP)
        else:
            print(f"Cannot setup: state is {self.state}, not INIT ({self.INIT})")

    def exitClient(self):
        """Teardown button handler."""
        # Dừng RTP thread
        self.rtpThreadRunning = False
        
        if hasattr(self, 'playEvent'):
            self.playEvent.set()
    
        # Gửi TEARDOWN request
        self.sendRtspRequest(self.TEARDOWN)
    
        # Đợi một chút để server xử lý
        time.sleep(0.3)
        
        # Dừng RTSP thread
        self.rtspThreadRunning = False
    
        # Đóng sockets
        try:
            if hasattr(self, 'rtpSocket'):
                self.rtpSocket.close()
        except:
            pass
            
        try:
            if hasattr(self, 'rtspSocket'):
                self.rtspSocket.close()
        except:
            pass
    
        # Đóng GUI
        self.master.destroy()
    
        # Xóa cache file
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except:
            pass

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        """Play button handler."""
        print(f"playMovie called, current state: {self.state}, READY state: {self.READY}")
        if self.state == self.READY:
            print("Starting RTP listening thread...")
            self.rtpThreadRunning = True
            self.playEvent = threading.Event()
            self.playEvent.clear()
            
            # Tạo và start thread
            rtp_thread = threading.Thread(target=self.listenRtp)
            rtp_thread.daemon = True  # Thread sẽ tự dừng khi main thread kết thúc
            rtp_thread.start()
            
            # Gửi PLAY request
            self.sendRtspRequest(self.PLAY)
            print("PLAY request sent")
        else:
            print(f"Cannot play: state is {self.state}, not READY ({self.READY})")

    def listenRtp(self):
        """Listen for RTP packets."""
        print("RTP listening thread started")
        timeout_count = 0
        max_timeouts = 10
        last_displayed_frame = -1
        packet_count = 0
        
        # Buffer cho frame hiện tại (không cần frameNum nữa vì dựa vào marker)
        current_frame_fragments = []
        current_frame_start_seq = None
        
        while self.rtpThreadRunning:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    timeout_count = 0
                    packet_count += 1
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    
                    seqNum = rtpPacket.seqNum()
                    marker = rtpPacket.marker()
                    payload = rtpPacket.getPayload()

                    # Update statistics
                    self.updateStats(seqNum, len(data))

                    # Bắt đầu frame mới nếu buffer rỗng
                    if len(current_frame_fragments) == 0:
                        current_frame_start_seq = seqNum
                        if packet_count <= 5 or packet_count % 100 == 0:
                            print(f"Packet #{packet_count}: Starting new frame at seqNum={seqNum}")
                    
                    # Thêm fragment vào buffer
                    current_frame_fragments.append(payload)
                    
                    # Khi nhận marker bit = 1, frame hoàn thành
                    if marker == 1:
                        # Reassemble frame
                        complete_frame = b''.join(current_frame_fragments)
                        
                        # Tính frame number từ sequence number đầu tiên
                        self.frameNbr += 1
                        frame_num = self.frameNbr
                        
                        print(f"  Frame {frame_num} COMPLETE: {len(complete_frame)} bytes from {len(current_frame_fragments)} fragments (seq {current_frame_start_seq}-{seqNum})")
                        
                        # Hiển thị frame
                        cachename = self.writeFrame(complete_frame)
                        if cachename:
                            self.master.after(0, lambda cn=cachename: self.updateMovie(cn))
                        
                        # Reset buffer cho frame tiếp theo
                        current_frame_fragments = []
                        current_frame_start_seq = None
                            
            except socket.timeout:
                timeout_count += 1
                if timeout_count >= max_timeouts:
                    print(f"No data received after {max_timeouts} timeouts, stopping playback")
                    break
                continue
            except OSError:
                if not self.rtpThreadRunning:
                    break
                print("OSError in listenRtp")
                break
            except Exception as e:
                if not self.rtpThreadRunning:
                    break
                print(f"Error in listenRtp: {e}")
                import traceback
                traceback.print_exc()
                break
        
        print("RTP listening thread stopped")

    def writeFrame(self, data):
        """Write received frame to a temp image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        try:
            with open(cachename, "wb") as file:
                file.write(data)
            return cachename
        except Exception as e:
            print(f"Error writing frame: {e}")
            return None

    def updateMovie(self, imageFile):
        """Update GUI label with a video frame."""
        if imageFile:
            try:
                # Đọc và resize ảnh để tránh lag
                img = Image.open(imageFile)
                # Giới hạn kích thước để tránh GUI lag
                img.thumbnail((640, 480), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.label.configure(image=photo, height=288)
                self.label.image = photo
            except Exception as e:
                print(f"Error updating movie: {e}")

    def connectToServer(self):
        """Connect to the Server and start RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except Exception as e:
            tkMessageBox.showwarning("Connection Failed", f"Connection to '{self.serverAddr}' failed.")
            print(f"Connection error: {e}")

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

        try:
            self.rtspSocket.send(request.encode("utf-8"))
            print("\nData sent:\n" + request)
        except Exception as e:
            print(f"Error sending RTSP request: {e}")

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        print("RTSP reply receiving thread started")
        while self.rtspThreadRunning:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply:
                    print("RTSP reply received")
                    self.parseRtspReply(reply.decode("utf-8"))
                else:
                    print("Empty RTSP reply received, connection closed")
                    break
                    
                if self.requestSent == self.TEARDOWN:
                    print("TEARDOWN processed, stopping RTSP thread")
                    break
            except Exception as e:
                if not self.rtspThreadRunning:
                    break
                print(f"Error in recvRtspReply: {e}")
                import traceback
                traceback.print_exc()
                break
        print("RTSP reply receiving thread stopped")

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        try:
            print(f"Parsing RTSP reply:\n{data}")
            lines = data.split("\n")
            seqNum = int(lines[1].split(" ")[1])
            print(f"Reply CSeq: {seqNum}, Expected: {self.rtspSeq}")
            
            if seqNum == self.rtspSeq:
                session = int(lines[2].split(" ")[1])
                print(f"Session ID: {session}")
                
                if self.sessionId == 0:
                    self.sessionId = session
                    print(f"Session ID set to: {self.sessionId}")
                    
                if self.sessionId == session:
                    status_code = int(lines[0].split(" ")[1])
                    print(f"Status code: {status_code}")
                    
                    if status_code == 200:
                        if self.requestSent == self.SETUP:
                            print("SETUP successful, changing state to READY")
                            self.state = self.READY
                            self.openRtpPort()
                            print(f"State is now: {self.state} (READY={self.READY})")
                            
                        elif self.requestSent == self.PLAY:
                            print("PLAY successful, changing state to PLAYING")
                            self.state = self.PLAYING
                            print(f"State is now: {self.state} (PLAYING={self.PLAYING})")
                            
                        elif self.requestSent == self.PAUSE:
                            print("PAUSE successful, changing state to READY")
                            self.state = self.READY
                            self.rtpThreadRunning = False
                            if hasattr(self, 'playEvent'):
                                self.playEvent.set()
                                
                        elif self.requestSent == self.TEARDOWN:
                            print("TEARDOWN successful")
                            self.state = self.INIT
                            self.teardownAcked = 1
                    else:
                        print(f"Server returned error status: {status_code}")
        except Exception as e:
            print(f"Error parsing RTSP reply: {e}")
            import traceback
            traceback.print_exc()

    def openRtpPort(self):
        """Open RTP socket bound to a specified port."""
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(2.0)  # Tăng timeout lên 2 giây
        try:
            self.rtpSocket.bind(("", self.rtpPort))
            print(f"RTP socket bound to port {self.rtpPort}")
        except Exception as e:
            tkMessageBox.showwarning("Unable to Bind", f"Unable to bind PORT={self.rtpPort}")
            print(f"Bind error: {e}")

    def handler(self):
        """Handle window close event."""
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()
            
    def updateStats(self, seqNum, dataSize):
        """Track network statistics."""
        self.stats['packets_received'] += 1
        self.stats['bytes_received'] += dataSize
        
        # Detect packet loss
        if self.lastSeqNum >= 0:
            expected_seq = self.lastSeqNum + 1
            # Handle sequence number wrap around (16-bit)
            if expected_seq > 65535:
                expected_seq = 0
            if seqNum > expected_seq:
                self.stats['packets_lost'] += (seqNum - expected_seq)
        
        self.lastSeqNum = seqNum
        
    def getStatistics(self):
        """Calculate streaming statistics."""
        elapsed = time.time() - self.stats['start_time']
        if elapsed > 0:
            bitrate = (self.stats['bytes_received'] * 8) / elapsed / 1000  # kbps
        else:
            bitrate = 0
            
        total_packets = self.stats['packets_received'] + self.stats['packets_lost']
        if total_packets > 0:
            loss_rate = self.stats['packets_lost'] / total_packets * 100
        else:
            loss_rate = 0
        
        return {
            'bitrate_kbps': bitrate,
            'packet_loss_percent': loss_rate,
            'total_packets': self.stats['packets_received'],
            'lost_packets': self.stats['packets_lost']
        }