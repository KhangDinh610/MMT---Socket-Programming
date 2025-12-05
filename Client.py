import os
import socket
import sys
import threading
import tkinter.messagebox as tkMessageBox
from tkinter import *
import tkinter.messagebox

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

		self.totalReceived = 0      # Tổng số gói thực nhận được
		self.firstSeqNum = 0        # Số thứ tự của gói đầu tiên
		self.currentSeqNum = 0      # Số thứ tự của gói hiện tại
		self.expectedPackets = 0    # Tổng số gói LẼ RA phải nhận

	def createWidgets(self):
		"""Build GUI."""
		# 1. Nút Setup (Màu cam nhạt)
		self.setup = Button(self.master, width=20, padx=3, pady=3)
		self.setup["text"] = "⚙ Setup" 
		self.setup["command"] = self.setupMovie
		self.setup["bg"] = "#FFCC80"  # Màu nền
		self.setup["font"] = ("Helvetica", 10, "bold") # Phông chữ đậm
		self.setup.grid(row=1, column=0, padx=2, pady=2)

		# 2. Nút Play (Màu xanh lá - Icon tam giác)
		self.start = Button(self.master, width=20, padx=3, pady=3)
		self.start["text"] = "▶ Play"
		self.start["command"] = self.playMovie
		self.start["bg"] = "#A5D6A7"
		self.start["font"] = ("Helvetica", 10, "bold")
		self.start.grid(row=1, column=1, padx=2, pady=2)

		# 3. Nút Pause (Màu vàng - Icon 2 gạch)
		self.pause = Button(self.master, width=20, padx=3, pady=3)
		self.pause["text"] = "⏸ Pause"
		self.pause["command"] = self.pauseMovie
		self.pause["bg"] = "#FFF59D"
		self.pause["font"] = ("Helvetica", 10, "bold")
		self.pause.grid(row=1, column=2, padx=2, pady=2)

		# 4. Nút Teardown (Màu đỏ nhạt - Icon ô vuông)
		self.teardown = Button(self.master, width=20, padx=3, pady=3)
		self.teardown["text"] = "■ Stop" 
		self.teardown["command"] = self.exitClient
		self.teardown["bg"] = "#EF9A9A"
		self.teardown["font"] = ("Helvetica", 10, "bold")
		self.teardown.grid(row=1, column=3, padx=2, pady=2)

		# 5. Màn hình hiển thị Video
		self.label = Label(self.master, height=19)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
		self.label["bg"] = "black" # Viền đen cho ngầu

		# 6. --- THÊM ĐỒNG HỒ ĐẾM GIỜ ---
		self.timerLabel = Label(self.master, text="Time: 00:00", font=("Helvetica", 12))
		self.timerLabel.grid(row=2, column=0, columnspan=4, pady=5)

	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)

	def exitClient(self):
		"""Teardown button handler."""
		# 1. Gửi lệnh TEARDOWN cho Server để nó ngừng gửi video
		self.sendRtspRequest(self.TEARDOWN)
		
		# 2. Hiện thông báo "Hết phim" (Code mới thêm)
		# Lưu ý: Phải bấm OK ở thông báo này thì cửa sổ mới tắt
		tkinter.messagebox.showinfo("Thông báo", "Hết phim rồi! Cảm ơn đã xem.\n(Code by Thùy Linh)")

		# 3. Đóng cửa sổ chương trình
		self.master.destroy()
		
		# 4. Xóa file rác 
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

					currSeqNum = rtpPacket.seqNum()
					
					# Nếu là gói tin đầu tiên nhận được
					if self.totalReceived == 0:
						self.firstSeqNum = currSeqNum
					
					self.totalReceived += 1
					self.currentSeqNum = currSeqNum
					
					# Công thức tính số gói bị mất:
					# Số gói lẽ ra phải có = (Số thứ tự hiện tại - Số thứ tự đầu tiên + 1)
					# Số gói mất = Số gói lẽ ra phải có - Số gói thực nhận
					self.expectedPackets = self.currentSeqNum - self.firstSeqNum + 1
					lostPackets = self.expectedPackets - self.totalReceived
					
					# Tránh chia cho 0
					if self.expectedPackets > 0:
						lossRate = (lostPackets / self.expectedPackets) * 100
					else:
						lossRate = 0
						
					print(f"SeqNum: {currSeqNum} | Received: {self.totalReceived} | Lost: {lostPackets} | Rate: {lossRate:.2f}%")

					currFrameNbr = rtpPacket.seqNum()
					print("Current Seq Num: " + str(currFrameNbr))
					if currFrameNbr > self.frameNbr:
						self.frameNbr = currFrameNbr
						self.updateMovie(self.writeFrame(rtpPacket.getPayload()))
			except:
				if self.playEvent.isSet():
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

		if self.currentSeqNum > 0:
			# Giả sử video chạy 20 khung hình/giây (FPS chuẩn của bài này)
			seconds = self.currentSeqNum / 20 
			m, s = divmod(seconds, 60)
			# Cập nhật text cho cái đồng hồ đã tạo ở Bước 2
			self.timerLabel.config(text=f"Time: {int(m):02d}:{int(s):02d}")

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

			print("\n" + "-" * 30)
			print("REPORT SUMMARY:")
			print(f"Total Expected Packets: {self.expectedPackets}")
			print(f"Total Received Packets: {self.totalReceived}")
			
			if self.expectedPackets > 0:
				loss_rate = float(self.expectedPackets - self.totalReceived) / self.expectedPackets * 100
			else:
				loss_rate = 0
				
			print(f"Packet Loss Rate: {loss_rate:.2f}%")
			print("-" * 30 + "\n")
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
