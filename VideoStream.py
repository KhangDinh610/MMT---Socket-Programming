class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		
	def nextFrame(self):
		"""Get next frame from MJPEG file."""
		# MJPEG format: mỗi frame là một JPEG image hoàn chỉnh
		# JPEG bắt đầu với 0xFF 0xD8 và kết thúc với 0xFF 0xD9
		
		# Đọc header để xác định định dạng
		current_pos = self.file.tell()
		header = self.file.read(5)
		
		if not header or len(header) < 5:
			return None
			
		# Kiểm tra nếu đây là custom format (5 bytes ASCII length)
		try:
			# Thử parse như ASCII number
			self.file.seek(current_pos)
			data = self.file.read(5)
			framelength = int(data)
			
			# Nếu thành công, đọc frame theo length
			frameData = self.file.read(framelength)
			self.frameNum += 1
			return frameData
			
		except (ValueError, UnicodeDecodeError):
			# Không phải custom format, parse như MJPEG chuẩn
			self.file.seek(current_pos)
			return self._readJPEGFrame()
	
	def _readJPEGFrame(self):
		"""Read a single JPEG frame from MJPEG stream."""
		# Tìm JPEG start marker (0xFF 0xD8)
		frameData = bytearray()
		
		# Đọc cho đến khi tìm thấy JPEG start marker
		while True:
			byte = self.file.read(1)
			if not byte:
				return None
				
			if byte == b'\xff':
				nextByte = self.file.read(1)
				if not nextByte:
					return None
					
				if nextByte == b'\xd8':  # JPEG Start Of Image (SOI)
					frameData.extend(b'\xff\xd8')
					break
		
		# Đọc cho đến khi tìm thấy JPEG end marker (0xFF 0xD9)
		while True:
			byte = self.file.read(1)
			if not byte:
				break
				
			frameData.append(byte[0])
			
			if byte == b'\xff':
				nextByte = self.file.read(1)
				if not nextByte:
					break
					
				frameData.append(nextByte[0])
				
				if nextByte == b'\xd9':  # JPEG End Of Image (EOI)
					self.frameNum += 1
					return bytes(frameData)
		
		# Nếu không tìm thấy end marker, trả về None
		return None
		
	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum