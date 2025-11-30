from random import randint
import sys, traceback, threading, socket, time

from VideoStream import VideoStream
from RtpPacket import RtpPacket
from RtpPacket import MAX_PAYLOAD_SIZE

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'
    
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2
    
    clientInfo = {}
    
    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        
    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()
    
    def recvRtspRequest(self):
        """Receive RTSP request from the client."""
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:            
            try:
                data = connSocket.recv(256)
                if data:
                    print("Data received:\n" + data.decode("utf-8"))
                    self.processRtspRequest(data.decode("utf-8"))
                else:
                    break
            except:
                break
    
    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        # Get the request type
        request = data.split('\n')
        line1 = request[0].split(' ')
        requestType = line1[0]
        
        # Get the media file name
        filename = line1[1]
        
        # Get the RTSP sequence number 
        seq = request[1].split(' ')
        
        print(f"Processing {requestType} request, CSeq: {seq[1]}")
        
        # Process SETUP request
        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP\n")
                
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename)
                    self.state = self.READY
                    print(f"Video file '{filename}' opened successfully")
                except IOError:
                    print(f"ERROR: Cannot open video file '{filename}'")
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
                    return
                
                # Generate a randomized RTSP session ID
                self.clientInfo['session'] = randint(100000, 999999)
                print(f"Generated session ID: {self.clientInfo['session']}")
                
                # Send RTSP reply
                self.replyRtsp(self.OK_200, seq[1])
                
                # Get the RTP/UDP port from the last line
                try:
                    transport_line = request[2].strip()
                    print(f"Transport line: '{transport_line}'")
                    
                    if 'client_port=' in transport_line:
                        port_part = transport_line.split('client_port=')[1]
                        self.clientInfo['rtpPort'] = port_part.strip().split()[0]
                    else:
                        parts = transport_line.split(' ')
                        if len(parts) >= 4:
                            self.clientInfo['rtpPort'] = parts[3]
                        else:
                            print("Error: Invalid Transport header format")
                            self.replyRtsp(self.CON_ERR_500, seq[1])
                            return
                            
                    print(f"Client RTP Port: {self.clientInfo['rtpPort']}")
                    
                except (IndexError, ValueError) as e:
                    print(f"Error parsing RTP port: {e}")
                    self.replyRtsp(self.CON_ERR_500, seq[1])
                    return
        
        # Process PLAY request         
        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY - Starting video stream\n")
                self.state = self.PLAYING
                
                # Create a new socket for RTP/UDP
                self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                print(f"RTP socket created for {self.clientInfo['rtspSocket'][1][0]}:{self.clientInfo['rtpPort']}")
                
                self.replyRtsp(self.OK_200, seq[1])
                
                # Create a new thread and start sending RTP packets
                self.clientInfo['event'] = threading.Event()
                self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
                self.clientInfo['worker'].start()
                print("RTP sending thread started")
            else:
                print(f"Cannot PLAY: current state is {self.state}, not READY")
        
        # Process PAUSE request
        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY
                
                self.clientInfo['event'].set()
            
                self.replyRtsp(self.OK_200, seq[1])
        
        # Process TEARDOWN request
        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")

            self.clientInfo['event'].set()
            
            self.replyRtsp(self.OK_200, seq[1])
            
            # Close the RTP socket
            if 'rtpSocket' in self.clientInfo:
                try:
                    self.clientInfo['rtpSocket'].close()
                    print("RTP socket closed")
                except:
                    pass
            
    def sendRtp(self):
        """Send RTP packets over UDP."""
        print("Starting RTP streaming")
        frame_count = 0
        
        while True:
            self.clientInfo['event'].wait(0.05)  # 50ms = 20 FPS
                
            # Stop sending if request is PAUSE or TEARDOWN
            if self.clientInfo['event'].isSet(): 
                print(f"Streaming stopped. Total frames sent: {frame_count}")
                break 
                    
            data = self.clientInfo['videoStream'].nextFrame()
            if data: 
                frameNumber = self.clientInfo['videoStream'].frameNbr()
                frame_count = frameNumber
                
                if frameNumber <= 5 or frameNumber % 50 == 0:
                    print(f"[Server] Processing frame {frameNumber}, size: {len(data)} bytes")
                    
                # Kiểm tra kích thước frame
                if len(data) > MAX_PAYLOAD_SIZE:
                    # Fragment lớn frames
                    rtpPacket = RtpPacket() 
                    fragments = rtpPacket.fragmentFrame(data, frameNumber)
                    
                    for idx, (fragment_data, seq_num, marker) in enumerate(fragments):
                        try:
                            address = self.clientInfo['rtspSocket'][1][0]
                            port = int(self.clientInfo['rtpPort'])
                            
                            packet = self.makeRtp(fragment_data, seq_num, marker)
                            self.clientInfo['rtpSocket'].sendto(packet, (address, port))
                            
                            # Giảm delay giữa fragments
                            if idx < len(fragments) - 1:
                                time.sleep(0.0005)
                        except Exception as e:
                            print(f"Connection Error: {e}")
                            return
                else:
                    # Gửi frame nhỏ như bình thường (không fragment)
                    try:
                        address = self.clientInfo['rtspSocket'][1][0]
                        port = int(self.clientInfo['rtpPort'])
                        self.clientInfo['rtpSocket'].sendto(
                            self.makeRtp(data, frameNumber, 1), 
                            (address, port)
                        )
                    except Exception as e:
                        print(f"Connection Error: {e}")
                        return
            else:
                # Hết frames
                print(f"End of video stream. Total frames sent: {frame_count}")
                break

    def makeRtp(self, payload, frameNbr, marker=1):  
        """RTP-packetize the video data."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        # marker được truyền từ parameter
        pt = 26  # MJPEG type
        seqnum = frameNbr  # ĐÃ BAO GỒM fragment index nếu fragmented
        ssrc = 0 
            
        rtpPacket = RtpPacket()
            
        rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
            
        return rtpPacket.getPacket()
            
    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
            connSocket = self.clientInfo['rtspSocket'][0]
            try:
                connSocket.send(reply.encode())
            except:
                print("Error sending RTSP reply")
            
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")