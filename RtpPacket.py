import sys
from time import time
HEADER_SIZE = 12
MAX_PAYLOAD_SIZE = 1400  # Để lại space cho headers

class RtpPacket:	
	header = bytearray(HEADER_SIZE)
	
	def __init__(self):
		pass
		
	def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload):
		"""Encode the RTP packet with header fields and payload."""
		timestamp = int(time())
		header = bytearray(HEADER_SIZE)
		#--------------
		# TO COMPLETE
		#--------------
		# Fill the header bytearray with RTP header fields
		
		# header[0] = ...
		# ...
		
		# Get the payload from the argument
		# self.payload = ...
		
	def decode(self, byteStream):
		"""Decode the RTP packet."""
		self.header = bytearray(byteStream[:HEADER_SIZE])
		self.payload = byteStream[HEADER_SIZE:]
	
	def version(self):
		"""Return RTP version."""
		return int(self.header[0] >> 6)
	
	def seqNum(self):
		"""Return sequence (frame) number."""
		seqNum = self.header[2] << 8 | self.header[3]
		return int(seqNum)
	
	def timestamp(self):
		"""Return timestamp."""
		timestamp = self.header[4] << 24 | self.header[5] << 16 | self.header[6] << 8 | self.header[7]
		return int(timestamp)
	
	def payloadType(self):
		"""Return payload type."""
		pt = self.header[1] & 127
		return int(pt)
	
	def getPayload(self):
		"""Return payload."""
		return self.payload
		
	def getPacket(self):
		"""Return RTP packet."""
		return self.header + self.payload
	
	def fragmentFrame(self, frame_data, frame_number):
		"""Chia frame lớn thành nhiều RTP packets"""
		fragments = []
		total_fragments = (len(frame_data) + MAX_PAYLOAD_SIZE - 1) // MAX_PAYLOAD_SIZE
    
		for i in range(total_fragments):
			start = i * MAX_PAYLOAD_SIZE
			end = min(start + MAX_PAYLOAD_SIZE, len(frame_data))
			fragment = frame_data[start:end]
        
        	# Marker bit = 1 cho fragment cuối cùng
			marker = 1 if i == total_fragments - 1 else 0
        
        	# Sequence number: frame_number * 1000 + fragment_index
			seq_num = frame_number * 1000 + i
        
			fragments.append((fragment, seq_num, marker))
    
		return fragments