import sys
from time import time
HEADER_SIZE = 12
MAX_PAYLOAD_SIZE = 1400  # Để lại space cho headers

class RtpPacket:	
	header = bytearray(HEADER_SIZE)
	
	def __init__(self):
		pass
		
	def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload):
		"""Encode RTP packet with marker bit."""
		timestamp = int(time())
		header = bytearray(HEADER_SIZE)

		#--------------
		# TO COMPLETE
		#--------------
		# Fill the header bytearray with RTP header fields
		
		# Byte 0: V(2), P(1), X(1), CC(4)
		header[0] = (version << 6) | (padding << 5) | (extension << 4) | cc
		
		# Byte 1: M(1), PT(7)
		header[1] = (marker << 7) | pt
		
		# Bytes 2-3: Sequence number
		header[2] = (seqnum >> 8) & 0xFF
		header[3] = seqnum & 0xFF
		
		# Bytes 4-7: Timestamp
		header[4] = (timestamp >> 24) & 0xFF
		header[5] = (timestamp >> 16) & 0xFF
		header[6] = (timestamp >> 8) & 0xFF
		header[7] = timestamp & 0xFF
		
		# Bytes 8-11: SSRC
		header[8] = (ssrc >> 24) & 0xFF
		header[9] = (ssrc >> 16) & 0xFF
		header[10] = (ssrc >> 8) & 0xFF
		header[11] = ssrc & 0xFF
		
		# Get the payload from the argument
		self.header = header
		self.payload = payload
		
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
	
	def marker(self):
		"""Return marker bit."""
		return int((self.header[1] >> 7) & 1)