from random import randint
import sys, traceback, threading, socket

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class RequestParser:
	def __init__(self, request):
		data = request.decode("utf-8")
		lines = [line.strip() for line in data.split("\r\n") if line.strip()]

		self.requestType = None
		self.filename = None
		self.seq = None
		self.transportLine = None
		self.session = None
		self.rtp_port = None
		self.exist = True   # this instance exists

		if not lines:
			self.exist = False
			return

		# Parse request line (e.g. SETUP movie.Mjpeg RTSP/1.0)
		line1 = lines[0].split()
		if len(line1) >= 2:
			self.requestType = line1[0]
			self.filename = line1[1]
		else:
			self.exist = False
			return

		for line in lines[1:]:
			if line.startswith("CSeq:"):
				self.seq = line.split(":", 1)[1].strip()
			elif line.startswith("Transport:"):
				self.transportLine = line
				# Extract RTP port if SETUP
				if "client_port=" in line:
					self.rtp_port = line.split("client_port=")[1]
					self.rtp_port = (
						self.rtp_port.split(";")[0]
						if ";" in self.rtp_port
						else self.rtp_port
					).strip()
			elif line.startswith("Session:"):
				self.session = line.split(":", 1)[1].strip()

	def __bool__(self):
		return self.exist

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
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data)
	
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		# SỬ DỤNG REQUEST PARSER Ở ĐÂY
		parser = RequestParser(data)
		
		if not parser:
			return
		
		# Lấy thông tin từ parser cho gọn
		requestType = parser.requestType
		filename = parser.filename
		seq = parser.seq
		
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				print("processing SETUP\n")
				
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq)
				
				# Generate a randomized RTSP session ID
				self.clientInfo['session'] = randint(100000, 999999)
				
				# Send RTSP reply
				self.replyRtsp(self.OK_200, seq)
				
				# LẤY CỔNG RTP TỪ PARSER (KHÔNG BỊ LỖI SPLIT NỮA)
				self.clientInfo['rtpPort'] = parser.rtp_port
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				# Create a new socket for RTP/UDP
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				
				self.replyRtsp(self.OK_200, seq)
				
				# Create a new thread and start sending RTP packets
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				
				self.clientInfo['event'].set()
			
				self.replyRtsp(self.OK_200, seq)
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")

			self.clientInfo['event'].set()
			
			self.replyRtsp(self.OK_200, seq)
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()
			
	def sendRtp(self):
		"""Send RTP packets over UDP."""
		while True:
			self.clientInfo['event'].wait(0.05) 
			
			# Stop sending if request is PAUSE or TEARDOWN
			if self.clientInfo['event'].isSet(): 
				break 
				
			data = self.clientInfo['videoStream'].nextFrame()
			if data: 
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				try:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])
					self.clientInfo['rtpSocket'].sendto(self.makeRtp(data, frameNumber),(address,port))
				except:
					print("Connection Error")
					#print('-'*60)
					#traceback.print_exc(file=sys.stdout)
					#print('-'*60)

	def makeRtp(self, payload, frameNbr):
		"""RTP-packetize the video data."""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		marker = 0
		pt = 26 # MJPEG type
		seqnum = frameNbr
		ssrc = 0 
		
		rtpPacket = RtpPacket()
		
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		
		return rtpPacket.getPacket()
		
	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			#print("200 OK")
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")
