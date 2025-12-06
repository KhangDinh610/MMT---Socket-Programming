#!/usr/bin/env python3
"""
ClientQt.py - PyQt5 RTP/MJPEG client with original caching semantics, async decode worker,
and progress/seek UI.

Save as ClientQt.py and run with:
    python ClientQt.py <server_addr> <server_port> <rtp_port> <video_file>

Requires:
    pip install PyQt5 turbojpeg numpy opencv-python pillow

What this file includes / integrates:
- Uses your original Cache class (exact semantics) from Cache.py.
- Same RTSP/RTP/control semantics as your original client (SETUP/PLAY/PAUSE/TEARDOWN).
- Decoder + resize moved to background QThread (fast, non-blocking UI).
- Progress bar / seeking UI copied from your Tk implementation semantics.
- Constants added for configurable behavior (RTP buffer sizes, recv sizes, decode/pacing).
- Robust TurboJPEG import (works with builds without TJPF_RGB).
- Keeps sequential playback (no jump-to-newest).
- Progress bar is updated every decoded frame tick (no waiting).
"""

import os
import socket
import sys
import threading
from time import sleep

import numpy as np

# Robust TurboJPEG import (some builds don't export TJPF_RGB)
try:
    from turbojpeg import TJPF_RGB, TurboJPEG  # type: ignore

    _TURBOJPEG_AVAILABLE = True
    _TURBOJPEG_DECODER = TurboJPEG()
    _TURBO_HAVE_TJPF_RGB = True
except Exception:
    try:
        from turbojpeg import TurboJPEG  # type: ignore

        _TURBOJPEG_AVAILABLE = True
        _TURBOJPEG_DECODER = TurboJPEG()
        _TURBO_HAVE_TJPF_RGB = False
    except Exception:
        _TURBOJPEG_AVAILABLE = False
        _TURBOJPEG_DECODER = None
        _TURBO_HAVE_TJPF_RGB = False

# Optional OpenCV for fast resize
try:
    import cv2

    _CV2_AVAILABLE = True
except Exception:
    _CV2_AVAILABLE = False

# PyQt5
from PyQt5.QtCore import QRect, QSize, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow,
                             QMessageBox, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

# Project modules
from Cache import MAX_CACHE_FRAME, Cache
from RtpPacket import RtpPacket

# -----------------------
# Constants (configurable)
# -----------------------
CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

# Progress bar time window settings (same as original)
PROGRESS_BAR_WINDOW_SECONDS = 10
FRAMES_PER_SECOND = 30
PROGRESS_BAR_WINDOW_FRAMES = PROGRESS_BAR_WINDOW_SECONDS * FRAMES_PER_SECOND

# Networking constants
RTP_RECV_BUFFER = 65500  # max UDP receive
RTSP_RECV_SIZE = 256
SO_RCVBUF_CLIENT = 4 * 1024 * 1024

# Decoder / pacing
PLAYBACK_FPS = FRAMES_PER_SECOND
PLAYBACK_INTERVAL_S = 1.0 / PLAYBACK_FPS

# Misc
DEFAULT_MIN_WINDOW_WIDTH = 320
DEFAULT_MIN_WINDOW_HEIGHT = 180

# -----------------------
# UI helper: ProgressBar (mirrors the tk Canvas behavior)
# -----------------------
class ProgressBar(QWidget):
    """Custom progress bar similar to the Tk Canvas one; emits seekRequested(offset) on click."""

    seekRequested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.ahead = 0
        self.behind = 0
        self.window_frames = PROGRESS_BAR_WINDOW_FRAMES

    def setCounts(self, ahead, behind):
        self.ahead = ahead
        self.behind = behind
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        # background
        painter.fillRect(0, 0, w, h, QColor("#e0e0e0"))

        center = self.window_frames // 2

        # behind (left)
        if self.behind > 0:
            behind_start_frame = max(0, center - self.behind)
            behind_end_frame = center
            x0 = int((behind_start_frame / self.window_frames) * w)
            x1 = int((behind_end_frame / self.window_frames) * w)
            painter.fillRect(QRect(x0, 0, max(1, x1 - x0), h), QColor("#cc0000"))

        # ahead (right)
        if self.ahead > 0:
            ahead_start_frame = center
            ahead_end_frame = min(self.window_frames, center + self.ahead)
            x0 = int((ahead_start_frame / self.window_frames) * w)
            x1 = int((ahead_end_frame / self.window_frames) * w)
            painter.fillRect(QRect(x0, 0, max(1, x1 - x0), h), QColor("#b0b0b0"))

        # playhead
        play_x = int((center / self.window_frames) * w)
        painter.setPen(QColor("white"))
        painter.drawLine(play_x, 0, play_x, h)

        # time labels
        painter.setPen(QColor("#666666"))
        painter.setFont(QFont("Arial", 8))
        painter.drawText(5, h // 2 + 5, f"-{PROGRESS_BAR_WINDOW_SECONDS // 2}s")
        painter.drawText(w - 60, h // 2 + 5, f"+{PROGRESS_BAR_WINDOW_SECONDS // 2}s")

        painter.end()

    def mousePressEvent(self, event):
        x = event.x()
        w = self.width() if self.width() > 0 else 1
        click_fraction = x / w
        clicked_frame_in_window = int(click_fraction * self.window_frames)
        center = self.window_frames // 2
        offset = clicked_frame_in_window - center
        # emit offset; caller will validate against cache ahead/behind
        self.seekRequested.emit(offset)


# -----------------------
# Decoder worker thread
# -----------------------
class DecoderWorker(QThread):
    """
    Background thread: pulls frames from cache (using original cache methods),
    decodes (TurboJPEG or fallback), resizes to target, then emits QImage for GUI.
    """

    frameReady = pyqtSignal(QImage)
    statusUpdate = pyqtSignal(str)

    def __init__(self, cache: Cache, cache_lock: threading.Lock, parent=None):
        super().__init__(parent)
        self.cache = cache
        self.cache_lock = cache_lock
        self.running = False
        self.playing = False
        self.target_size = QSize(640, 360)
        self.jpeg = _TURBOJPEG_DECODER if _TURBOJPEG_AVAILABLE else None
        self.jpeg_have_tjpf_rgb = _TURBO_HAVE_TJPF_RGB
        self.mutex = threading.Lock()

    @pyqtSlot(QSize)
    def update_target_size(self, size: QSize):
        with self.mutex:
            self.target_size = QSize(max(1, size.width()), max(1, size.height()))

    def run(self):
        self.running = True
        while self.running:
            if not self.playing:
                sleep(0.01)
                continue

            frame_bytes = None
            # Acquire current frame (do not advance pointer here; advancement happens after emitting,
            # to preserve original sequential semantics)
            with self.cache_lock:
                if self.cache.hasValue():
                    frame_bytes = self.cache.getCurrentFrame()

            if frame_bytes is None:
                # buffer empty -> wait
                sleep(0.005)
                continue

            try:
                img_rgb = None

                # Try TurboJPEG first when available
                if self.jpeg:
                    try:
                        if self.jpeg_have_tjpf_rgb:
                            img_rgb = self.jpeg.decode(frame_bytes, pixel_format=TJPF_RGB)  # type: ignore
                        else:
                            img_rgb = self.jpeg.decode(frame_bytes)
                    except Exception:
                        img_rgb = None

                # Fallback decode paths
                if img_rgb is None:
                    try:
                        # Prefer OpenCV if available
                        if _CV2_AVAILABLE:
                            nparr = np.frombuffer(frame_bytes, np.uint8)
                            img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            if img_bgr is not None:
                                img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                        if img_rgb is None:
                            # PIL fallback
                            from io import BytesIO

                            from PIL import Image

                            img_pil = Image.open(BytesIO(frame_bytes)).convert("RGB")
                            img_rgb = np.asarray(img_pil)
                    except Exception as e:
                        self.statusUpdate.emit(f"Decode fallback error: {e}")
                        img_rgb = None

                if img_rgb is None:
                    # unable to decode; skip and advance frame to avoid stall
                    with self.cache_lock:
                        self.cache.increaseFrame()
                    sleep(PLAYBACK_INTERVAL_S)
                    continue

                # Ensure numpy array
                if not isinstance(img_rgb, np.ndarray):
                    img_rgb = np.asarray(img_rgb)

                h, w = img_rgb.shape[:2]

                # get target size safely
                with self.mutex:
                    tsize = self.target_size
                target_w, target_h = tsize.width(), tsize.height()

                # compute scale preserving aspect ratio
                scale = min(target_w / w, target_h / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))

                if (new_w, new_h) != (w, h):
                    if _CV2_AVAILABLE:
                        img_rgb = cv2.resize(img_rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                    else:
                        from PIL import Image

                        img_rgb = np.array(Image.fromarray(img_rgb).resize((new_w, new_h), resample=Image.BILINEAR))

                # Create QImage from a copy of the numpy buffer (thread-safety)
                height = img_rgb.shape[0]
                width = img_rgb.shape[1]
                bytes_per_line = 3 * width
                qimg = QImage(img_rgb.copy().data, width, height, bytes_per_line, QImage.Format_RGB888).copy()

                # Emit image to GUI
                self.frameReady.emit(qimg)

                # After emitting, advance the cache pointer (original sequential behavior)
                with self.cache_lock:
                    self.cache.increaseFrame()

            except Exception as e:
                # Keep running; report error
                self.statusUpdate.emit(f"Decoder error: {e}")

            # pace
            sleep(PLAYBACK_INTERVAL_S)

    def stop(self):
        self.running = False
        try:
            self.wait(2000)
        except Exception:
            pass

    def set_playing(self, playing: bool):
        self.playing = playing


# -----------------------
# Main PyQt Client
# -----------------------
class ClientQt(QMainWindow):
    INIT = 0
    READY = 1
    PLAYING = 2

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    def __init__(self, server_addr, server_port, rtp_port, filename):
        super().__init__()
        self.server_addr = server_addr
        self.server_port = int(server_port)
        self.rtp_port = int(rtp_port)
        self.filename = filename

        # Networking/session
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.state = self.INIT

        # Playback/cache
        self.frameNbr = 0
        self.cache = Cache()  # original cache semantics
        self.cache_lock = threading.Lock()
        self.paused_by_buffer = False
        self.playing_in_buffer = False
        self.manual_pause = False

        # Fragment reassembly
        self.fragment_buffer = bytearray()
        self.last_fragment_seq = -1

        # UI state
        self._last_pixmap = None

        # Build UI
        self._build_ui()

        # Decoder worker
        self.decoder = DecoderWorker(self.cache, self.cache_lock)
        self.decoder.frameReady.connect(self.on_frame_ready)
        self.decoder.statusUpdate.connect(self.on_status_update)
        self.decoder.start()

        # Networking sockets
        self.rtspSocket = None
        self.rtpSocket = None

        # RTP listening thread
        self.rtp_thread = None

        # Connect to RTSP server
        self.connectToServer()

    def _build_ui(self):
        self.setWindowTitle("RTPClient (PyQt, Original Cache)")
        self.resize(900, 600)

        central = QWidget()
        self.setCentralWidget(central)
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(6)

        # Video display
        self.video_label = QLabel()
        self.video_label.setStyleSheet("background-color: black;")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_label.setMinimumSize(DEFAULT_MIN_WINDOW_WIDTH, DEFAULT_MIN_WINDOW_HEIGHT)
        vbox.addWidget(self.video_label, stretch=1)

        # Controls
        controls = QHBoxLayout()
        vbox.addLayout(controls)

        self.setup_btn = QPushButton("Setup")
        self.setup_btn.clicked.connect(self.setupMovie)
        controls.addWidget(self.setup_btn)

        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.playMovie)
        controls.addWidget(self.play_btn)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pauseMovie)
        controls.addWidget(self.pause_btn)

        self.teardown_btn = QPushButton("Teardown")
        self.teardown_btn.clicked.connect(self.exitClient)
        controls.addWidget(self.teardown_btn)

        # Progress bar and status
        self.progress = ProgressBar()
        self.progress.seekRequested.connect(self.on_progress_seek)
        vbox.addWidget(self.progress)

        self.status_label = QLabel("Status: Not Connected | Frame: -- | Cache: --/--")
        vbox.addWidget(self.status_label)

        # update decoder target size when label resizes
        self.video_label.installEventFilter(self)

    # eventFilter to catch resize events on video_label
    def eventFilter(self, obj, event):
        if obj is self.video_label and event.type() == event.Resize:
            new_size = event.size()
            self.decoder.update_target_size(new_size)
        return super().eventFilter(obj, event)

    # -----------------------
    # RTSP / RTP functions (mirrors original semantics)
    # -----------------------
    def connectToServer(self):
        try:
            self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.rtspSocket.connect((self.server_addr, self.server_port))
        except Exception as e:
            QMessageBox.warning(self, "Connection Failed", f"Connection to '{self.server_addr}' failed: {e}")

    def sendRtspRequest(self, requestCode):
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
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq = 1
            request = requestSetupTemplate.format(filePath=self.filename, sequenceNumber=self.rtspSeq, port=self.rtp_port)
            self.requestSent = self.SETUP
        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = requestStandardTemplate.format(requestCode="PLAY", filePath=self.filename, sequenceNumber=self.rtspSeq, session=self.sessionId)
            self.requestSent = self.PLAY
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = requestStandardTemplate.format(requestCode="PAUSE", filePath=self.filename, sequenceNumber=self.rtspSeq, session=self.sessionId)
            self.requestSent = self.PAUSE
        elif requestCode == self.TEARDOWN and self.state != self.INIT:
            self.rtspSeq += 1
            request = requestStandardTemplate.format(requestCode="TEARDOWN", filePath=self.filename, sequenceNumber=self.rtspSeq, session=self.sessionId)
            self.requestSent = self.TEARDOWN
        else:
            return

        try:
            self.rtspSocket.send(request.encode("utf-8"))
            print("\nData sent:\n" + request)
        except Exception as e:
            print(f"sendRtspRequest error: {e}")

    def recvRtspReply(self):
        while True:
            try:
                reply = self.rtspSocket.recv(RTSP_RECV_SIZE)
                if reply:
                    self.parseRtspReply(reply.decode("utf-8"))
                if self.requestSent == self.TEARDOWN:
                    try:
                        self.rtspSocket.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                    self.rtspSocket.close()
                    break
            except Exception:
                break

    def parseRtspReply(self, data):
        try:
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
                            if hasattr(self, "playEvent") and self.playEvent:
                                try:
                                    self.playEvent.set()
                                except Exception:
                                    pass
                        elif self.requestSent == self.TEARDOWN:
                            self.state = self.INIT
                            self.teardownAcked = 1
        except Exception:
            pass

    def openRtpPort(self):
        try:
            self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rtpSocket.settimeout(0.5)
            try:
                self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_CLIENT)
            except Exception:
                pass
            self.rtpSocket.bind(("0.0.0.0", self.rtp_port))
        except Exception as e:
            QMessageBox.warning(self, "Unable to Bind", f"Unable to bind PORT={self.rtp_port}: {e}")

    # -----------------------
    # Controls (original semantics)
    # -----------------------
    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)
            self.updateStatus()

    def playMovie(self):
        if self.state == self.READY or self.state == self.PLAYING:
            self.playing_in_buffer = True
            self.manual_pause = False
            self.updateStatus()
            if not hasattr(self, "playEvent") or self.playEvent is None:
                self.playEvent = threading.Event()
                self.playEvent.clear()
                # start RTP listening thread
                self.rtp_thread = threading.Thread(target=self.listenRtp, daemon=True)
                self.rtp_thread.start()
            if (self.state == self.READY and not self.paused_by_buffer):
                self.sendRtspRequest(self.PLAY)
            # tell decoder to start pulling frames
            self.decoder.set_playing(True)

    def pauseMovie(self):
        # keep sequential behavior: when paused, decoder stops pulling frames
        self.playing_in_buffer = False
        self.manual_pause = True
        # also pause decoder
        self.decoder.set_playing(False)
        self.updateStatus()

    def exitClient(self):
        try:
            self.sendRtspRequest(self.TEARDOWN)
        except Exception:
            pass
        try:
            self.decoder.stop()
        except Exception:
            pass
        try:
            if getattr(self, "rtpSocket", None):
                self.rtpSocket.close()
        except Exception:
            pass
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass

    # -----------------------
    # RTP receiving / fragment reassembly
    # -----------------------
    def listenRtp(self):
        while True:
            try:
                with self.cache_lock:
                    if (
                        (not self.cache.isFull())
                        and (self.paused_by_buffer)
                        and (self.state == self.READY)
                        and (not self.manual_pause)
                    ):
                        # auto-resume
                        print("Cache has room — requesting server PLAY")
                        self.paused_by_buffer = False
                        self.sendRtspRequest(self.PLAY)

                try:
                    data = self.rtpSocket.recv(RTP_RECV_BUFFER)
                except socket.timeout:
                    continue
                except AttributeError:
                    continue
                except OSError:
                    break

                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)

                    currFragmentSeq = rtpPacket.seqNum()
                    marker = rtpPacket.marker()
                    payload = rtpPacket.getPayload()

                    if (
                        self.last_fragment_seq != -1
                        and currFragmentSeq != self.last_fragment_seq + 1
                    ):
                        print(
                            f"Warning: Packet loss detected!  Expected {self.last_fragment_seq + 1}, got {currFragmentSeq}"
                        )
                        self.fragment_buffer = bytearray()

                    self.last_fragment_seq = currFragmentSeq
                    self.fragment_buffer.extend(payload)

                    if marker == 1:
                        complete_frame = bytes(self.fragment_buffer)
                        self.frameNbr += 1
                        with self.cache_lock:
                            # original addFrame semantics
                            self.cache.addFrame(complete_frame)
                        self.fragment_buffer = bytearray()
                    else:
                        # fragment accumulated
                        pass

                    with self.cache_lock:
                        if (
                            (self.cache.isFull())
                            and (not self.paused_by_buffer)
                            and (self.state == self.PLAYING)
                        ):
                            print("Cache full — requesting server PAUSE")
                            self.paused_by_buffer = True
                            self.sendRtspRequest(self.PAUSE)

            except Exception as e:
                print(f"RTP receive error: {e}")
                import traceback

                traceback.print_exc()
                if self.teardownAcked == 1:
                    try:
                        if getattr(self, "rtpSocket", None):
                            self.rtpSocket.shutdown(socket.SHUT_RDWR)
                            self.rtpSocket.close()
                    except Exception:
                        pass
                    break

    # -----------------------
    # Frame / display handler (main thread receives QImage from decoder)
    # -----------------------
    @pyqtSlot(QImage)
    def on_frame_ready(self, qimg: QImage):
        try:
            pix = QPixmap.fromImage(qimg)
            pix = pix.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.video_label.setPixmap(pix)
            self._last_pixmap = pix
            # Update progress/status every tick (do not wait)
            self.updateStatus()
        except Exception as e:
            print("on_frame_ready error:", e)

    @pyqtSlot(str)
    def on_status_update(self, text: str):
        # show decoder or other messages
        self.status_label.setText(text)

    # -----------------------
    # Progress / seek (maintain original semantics)
    # -----------------------
    @pyqtSlot(int)
    def on_progress_seek(self, offset: int):
        # Validate with cache
        with self.cache_lock:
            ahead = self.cache.aheadFrames()
            behind = self.cache.behindFrames()

        if offset < -behind or offset >= ahead:
            print(f"Clicked on uncached portion (offset={offset}, behind={behind}, ahead={ahead})")
            return

        if offset == 0:
            print("Already at clicked position")
            return

        was_playing = self.playing_in_buffer

        # Seek while preserving original cache semantics
        with self.cache_lock:
            success = self.cache.seekRelative(offset)

        if success:
            print(f"Seeked by {offset} frames ({offset/FRAMES_PER_SECOND:.2f}s)")

            if not was_playing and self.manual_pause:
                # only send PAUSE if server currently playing
                if self.state == self.PLAYING:
                    try:
                        self.sendRtspRequest(self.PAUSE)
                        self.paused_by_buffer = True
                    except Exception as e:
                        print(f"Error sending PAUSE during seek: {e}")

            # Display the new frame immediately by letting decoder pick it up on next loop
            # But force one immediate decode/display by toggling decoder play briefly if paused
            if not self.decoder.playing:
                # quick action: request a single decode/display while paused
                # Start decoder for one frame cycle
                self.decoder.set_playing(True)
                # schedule to stop shortly after (non-blocking)
                threading.Timer(0.05, lambda: self.decoder.set_playing(False)).start()

            self.updateStatus()

    # -----------------------
    # Status updates
    # -----------------------
    def updateStatus(self):
        with self.cache_lock:
            ahead = self.cache.aheadFrames()
            behind = self.cache.behindFrames()
            size = self.cache.current_size

        if self.state == self.INIT:
            state_text = "Not Connected"
        elif self.state == self.READY:
            state_text = "Ready"
        elif self.state == self.PLAYING:
            if self.playing_in_buffer:
                state_text = "Playing" if self.cache.hasValue() else "Buffering..."
            else:
                state_text = "Paused"
        else:
            state_text = "Unknown"

        status_text = f"Status: {state_text} | Frame: {self.frameNbr} | Cache: {size}/{MAX_CACHE_FRAME} | Behind: {behind} | Ahead: {ahead}"
        self.status_label.setText(status_text)
        # Update progress bar right away
        self.progress.setCounts(ahead, behind)

    def closeEvent(self, event):
        # graceful shutdown
        self.pauseMovie()
        reply = QMessageBox.question(self, "Quit?", "Are you sure you want to quit?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                self.exitClient()
            except Exception:
                pass
            event.accept()
        else:
            event.ignore()


# -----------------------
# Entry point
# -----------------------
def main():
    if len(sys.argv) < 5:
        print("Usage: ClientQt.py Server_addr Server_port RTP_port Video_file")
        sys.exit(1)

    serverAddr = sys.argv[1]
    serverPort = sys.argv[2]
    rtpPort = sys.argv[3]
    filename = sys.argv[4]

    app = QApplication(sys.argv)
    client = ClientQt(serverAddr, serverPort, rtpPort, filename)
    client.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
