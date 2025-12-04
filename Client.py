import os
import socket
import sys
import threading
from time import sleep
from tkinter import *
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from Cache import MAX_CACHE_FRAME, Cache
from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

# Progress bar time window settings
PROGRESS_BAR_WINDOW_SECONDS = 10  # How many seconds the bar represents
FRAMES_PER_SECOND = 25  # video framerate
PROGRESS_BAR_WINDOW_FRAMES = (
    PROGRESS_BAR_WINDOW_SECONDS * FRAMES_PER_SECOND
)  # = 250 frames


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

        # Set minimum window size for better progress bar visibility
        self.master.minsize(900, 650)

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
        self.cache = Cache()
        self.cache_lock = threading.Lock()

        self.consumer_after_id = None  # handle for scheduled playback timer
        self.playback_interval_ms = 40  # ~25 fps (adjust to match your video)
        self.paused_by_buffer = False
        self.playing_in_buffer = False
        self.manual_pause = False

    def createWidgets(self):
        """Build GUI."""
        # Video display label
        self.label = Label(self.master, height=19)
        self.label.grid(
            row=0, column=0, columnspan=4, sticky=W + E + N + S, padx=5, pady=5
        )

        # Control buttons
        self.setup = Button(
            self.master, width=20, padx=3, pady=3, text="Setup", command=self.setupMovie
        )
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(
            self.master, width=20, padx=3, pady=3, text="Play", command=self.playMovie
        )
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(
            self.master, width=20, padx=3, pady=3, text="Pause", command=self.pauseMovie
        )
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(
            self.master,
            width=20,
            padx=3,
            pady=3,
            text="Teardown",
            command=self.exitClient,
        )
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # YouTube-style progress bar frame
        self.progress_frame = Frame(self.master)
        self.progress_frame.grid(
            row=2, column=0, columnspan=4, sticky=W + E, padx=10, pady=5
        )

        # Canvas for custom progress bar (10-second sliding window)
        self.progress_canvas = Canvas(
            self.progress_frame,
            height=25,
            width=800,
            bg="#e0e0e0",
            highlightthickness=1,
            highlightbackground="#999999",
        )
        self.progress_canvas.pack(fill=X, expand=True)

        # Bind click event for seeking
        self.progress_canvas.bind("<Button-1>", self.onProgressBarClick)

        # Status bar frame (below progress bar)
        self.status_frame = Frame(self.master, relief=SUNKEN, borderwidth=1)
        self.status_frame.grid(
            row=3, column=0, columnspan=4, sticky=W + E, padx=5, pady=5
        )

        # Status text label
        self.status_label = Label(
            self.status_frame,
            text="Status: Ready | Frame: -- | Cache: --/-- | Behind: -- | Ahead: --",
            anchor=W,
            font=("Arial", 9),
        )
        self.status_label.pack(side=LEFT, fill=X, expand=True, padx=5, pady=2)

    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)
            self.updateStatusBar()

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
        self.playing_in_buffer = False
        self.manual_pause = True
        self.updateStatusBar()

    def playMovie(self):
        """Play button handler."""
        if self.state == self.READY or self.state == self.PLAYING:
            # Start everything if not already started
            if not hasattr(self, "playEvent") or self.playEvent is None:
                self.playEvent = threading.Event()
                self.playEvent.clear()
                threading.Thread(target=self.listenRtp, daemon=True).start()

                self.sendRtspRequest(self.PLAY)
                # Start consumer (playback from cache) on main thread
                if self.consumer_after_id is None:
                    self.consumer_after_id = self.master.after(
                        self.playback_interval_ms, self._playback_tick
                    )

            self.playing_in_buffer = True
            self.manual_pause = False
            self.updateStatusBar()

    def listenRtp(self):
        """Listen for RTP packets."""
        while True:
            try:
                with self.cache_lock:
                    if (
                        not self.cache.isFull()
                        and self.paused_by_buffer
                        and self.state == self.READY
                        and not self.manual_pause
                    ):
                        print("Cache has room — requesting server PLAY")
                        self.paused_by_buffer = False
                        self.sendRtspRequest(self.PLAY)

                data = self.rtpSocket.recv(20480)

                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)
                    currFrameNbr = rtpPacket.seqNum()
                    print("Current Seq Num: " + str(currFrameNbr))
                    if currFrameNbr > self.frameNbr:
                        self.frameNbr = currFrameNbr
                        self.cache.addFrame(rtpPacket.getPayload())

                    # Check if cache is now full (pause server)
                    with self.cache_lock:
                        if (
                            (self.cache.isFull())
                            and (not self.paused_by_buffer)
                            and (self.state == self.PLAYING)
                        ):
                            print("Cache full — requesting server PAUSE")
                            self.paused_by_buffer = True
                            self.sendRtspRequest(self.PAUSE)
            except socket.timeout:
                # Normal timeout - continue loop
                continue
            except Exception as e:
                print(f"RTP receive error: {e}")
                # Check exit conditions
                if hasattr(self, "playEvent") and self.playEvent.is_set():
                    break
                if self.teardownAcked == 1:
                    try:
                        self.rtpSocket.shutdown(socket.SHUT_RDWR)
                        self.rtpSocket.close()
                    except:
                        pass
                    break

    def _playback_tick(self):
        """Consumer: reads one frame from cache and displays it (runs on main thread)."""
        try:
            # Only play when playing_in_buffer is True
            if self.playing_in_buffer:
                if self.cache.hasValue():
                    # Get current frame from cache
                    frame = self.cache.getCurrentFrame()

                    if frame:
                        # Display the frame
                        self.updateMovie(self.writeFrame(frame))
                        # Advance to next frame in cache
                        self.cache.increaseFrame()
                else:
                    print("Cache is now empty, buffering...")

            # Update status bar and progress bar every tick
            self.updateStatusBar()

            # Schedule next tick
            self.consumer_after_id = self.master.after(
                self.playback_interval_ms, self._playback_tick
            )

        except Exception as e:
            print(f"Playback error: {e}")
            # Retry scheduling to keep playback alive
            self.consumer_after_id = self.master.after(
                self.playback_interval_ms, self._playback_tick
            )

    def updateStatusBar(self):
        """Update status bar with current playback/cache info (call from main thread only)."""
        try:
            # Determine current state text
            if self.state == self.INIT:
                state_text = "Not Connected"
            elif self.state == self.READY:
                state_text = "Ready"
            elif self.state == self.PLAYING:
                if self.playing_in_buffer:
                    if self.cache.hasValue():
                        state_text = "Playing"
                    else:
                        state_text = "Buffering..."
                else:
                    state_text = "Paused"
            else:
                state_text = "Unknown"

            # Get cache stats
            with self.cache_lock:
                ahead = self.cache.aheadFrames()
                behind = self.cache.behindFrames()
                current_size = self.cache.current_size
                capacity = MAX_CACHE_FRAME

            # Current frame number
            frame_num = self.frameNbr if hasattr(self, "frameNbr") else 0

            # Update text label
            status_text = (
                f"Status: {state_text} | "
                f"Frame: {frame_num} | "
                f"Cache: {current_size}/{capacity} | "
                f"Behind: {behind} | "
                f"Ahead: {ahead}"
            )
            self.status_label.config(text=status_text)

            # Color-code status based on state
            if state_text == "Playing":
                self.status_label.config(fg="green")
            elif state_text == "Buffering...":
                self.status_label.config(fg="orange")
            elif state_text == "Paused":
                self.status_label.config(fg="blue")
            else:
                self.status_label.config(fg="black")

            # Update YouTube-style progress bar
            self.updateProgressBar()

        except Exception as e:
            # Silently ignore errors to avoid crashing UI
            pass

    def updateProgressBar(self):
        """Draw YouTube-style progress bar showing full 10-second window with cached portions in gray."""
        try:
            # Clear previous drawings
            self.progress_canvas.delete("all")

            # Get canvas dimensions
            canvas_width = self.progress_canvas.winfo_width()
            if canvas_width <= 1:
                canvas_width = 800
            canvas_height = 25

            with self.cache_lock:
                ahead = self.cache.aheadFrames()
                behind = self.cache.behindFrames()
                current_size = self.cache.current_size

            # ========== Full 10-second window (always visible) ==========

            window_frames = PROGRESS_BAR_WINDOW_FRAMES  # 250 frames = 10 seconds

            # Draw background (full window, uncached = light gray)
            self.progress_canvas.create_rectangle(
                0, 0, canvas_width, canvas_height, fill="#e0e0e0", outline=""
            )

            # The window represents frames from -5s to +5s relative to current position
            # Calculate pixel positions for cached portions

            # Position 0 in the window = 5 seconds before current = left edge
            # Current position = center of window
            # Position window_frames in window = 5 seconds after current = right edge

            center_position = (
                window_frames // 2
            )  # Frame position of current playhead in window

            # Behind frames occupy [center - behind, center)
            # Ahead frames occupy [center, center + ahead)

            # Calculate pixel ranges for cached portions

            # 1. Draw cached "behind" portion (played frames) in RED
            if behind > 0:
                # Start of behind portion in window coordinates
                behind_start_frame = max(0, center_position - behind)
                behind_end_frame = center_position

                behind_start_x = int(
                    (behind_start_frame / window_frames) * canvas_width
                )
                behind_end_x = int((behind_end_frame / window_frames) * canvas_width)

                # Draw darker gray for cached behind (will be overdrawn with red)
                self.progress_canvas.create_rectangle(
                    behind_start_x,
                    0,
                    behind_end_x,
                    canvas_height,
                    fill="#b0b0b0",
                    outline="",
                )

                # Draw played portion (red) on top
                self.progress_canvas.create_rectangle(
                    behind_start_x,
                    0,
                    behind_end_x,
                    canvas_height,
                    fill="#cc0000",
                    outline="",
                )

            # 2. Draw cached "ahead" portion (buffered frames) in GRAY
            if ahead > 0:
                # Start of ahead portion in window coordinates
                ahead_start_frame = center_position
                ahead_end_frame = min(window_frames, center_position + ahead)

                ahead_start_x = int((ahead_start_frame / window_frames) * canvas_width)
                ahead_end_x = int((ahead_end_frame / window_frames) * canvas_width)

                # Draw buffered portion (gray)
                self.progress_canvas.create_rectangle(
                    ahead_start_x,
                    0,
                    ahead_end_x,
                    canvas_height,
                    fill="#b0b0b0",
                    outline="",
                )

            # 3. Draw current playhead position (white line at center)
            playhead_x = int((center_position / window_frames) * canvas_width)
            self.progress_canvas.create_line(
                playhead_x, 0, playhead_x, canvas_height, fill="white", width=3
            )

            # Draw time labels at edges
            # Left edge: -5 seconds
            left_text = f"-{PROGRESS_BAR_WINDOW_SECONDS // 2}s"
            self.progress_canvas.create_text(
                5,
                canvas_height // 2,
                text=left_text,
                anchor=W,
                fill="#666666",
                font=("Arial", 8, "bold"),
            )

            # Right edge: +5 seconds
            right_text = f"+{PROGRESS_BAR_WINDOW_SECONDS // 2}s"
            self.progress_canvas.create_text(
                canvas_width - 5,
                canvas_height // 2,
                text=right_text,
                anchor=E,
                fill="#666666",
                font=("Arial", 8, "bold"),
            )

            # Center: 0s (current position)
            self.progress_canvas.create_text(
                playhead_x,
                2,
                text="0s",
                anchor=N,
                fill="white",
                font=("Arial", 7, "bold"),
            )

            # Draw tick marks every 2 seconds
            tick_interval_seconds = 2
            tick_interval_frames = tick_interval_seconds * FRAMES_PER_SECOND

            for i in range(
                -PROGRESS_BAR_WINDOW_SECONDS // 2,
                PROGRESS_BAR_WINDOW_SECONDS // 2 + 1,
                tick_interval_seconds,
            ):
                if i == 0:
                    continue  # Skip center (that's the playhead)

                frame_offset = i * FRAMES_PER_SECOND
                frame_in_window = center_position + frame_offset

                if 0 <= frame_in_window <= window_frames:
                    tick_x = int((frame_in_window / window_frames) * canvas_width)

                    # Draw tick mark
                    self.progress_canvas.create_line(
                        tick_x,
                        canvas_height - 5,
                        tick_x,
                        canvas_height,
                        fill="#999999",
                        width=1,
                    )

                    # Draw small time label
                    label_text = f"{i:+d}s" if i != 0 else "0"
                    self.progress_canvas.create_text(
                        tick_x,
                        canvas_height - 7,
                        text=label_text,
                        anchor=S,
                        fill="#666666",
                        font=("Arial", 6),
                    )

        except Exception as e:
            print(f"Progress bar update error: {e}")

    def onProgressBarClick(self, event):
        """Handle click on progress bar - only seek if clicking on cached portion."""
        try:
            canvas_width = self.progress_canvas.winfo_width()
            click_x = event.x

            with self.cache_lock:
                ahead = self.cache.aheadFrames()
                behind = self.cache.behindFrames()

            window_frames = PROGRESS_BAR_WINDOW_FRAMES
            center_position = window_frames // 2

            # Calculate which frame in the window was clicked
            click_fraction = click_x / canvas_width
            clicked_frame_in_window = int(click_fraction * window_frames)

            # Convert to offset from current position
            offset = clicked_frame_in_window - center_position

            # Check if clicked position is within cached range
            if offset < -behind or offset >= ahead:
                # Clicked on uncached portion - do nothing
                print(
                    f"Clicked on uncached portion (offset={offset}, behind={behind}, ahead={ahead})"
                )
                return

            # Clicked within cached range
            if offset == 0:
                print("Already at clicked position")
                return

            # ========== NEW: Handle paused seek behavior ==========

            # Remember if we were playing or paused
            was_playing = self.playing_in_buffer

            # If we're currently playing and user seeks, keep playing
            # If we're paused and user seeks, stay paused

            # Perform the seek
            if self.cache.seekRelative(offset):
                print(f"Seeked by {offset} frames ({offset/FRAMES_PER_SECOND:.1f}s)")

                # If we were paused (not playing), we need to:
                # 1. Ensure server is paused (don't receive new packets)
                # 2. Display the new frame
                # 3. Stay paused

                if not was_playing:
                    # Make sure server is paused (stop packet reception)
                    with self.cache_lock:
                        if self.state == self.PLAYING and not self.paused_by_buffer:
                            print(
                                "Seek while paused - requesting server PAUSE to stop packet reception"
                            )
                            self.sendRtspRequest(self.PAUSE)
                            self.paused_by_buffer = (
                                True  # Mark that we paused for seeking
                            )

                # Immediately display the new frame
                frame = self.cache.getCurrentFrame()
                if frame:
                    self.updateMovie(self.writeFrame(frame))

                self.updateProgressBar()
                self.updateStatusBar()
            else:
                print(f"Seek failed: offset={offset}")

        except Exception as e:
            print(f"Progress bar click error: {e}")

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
            messagebox.showwarning(
                "Connection Failed", f"Connection to '{self.serverAddr}' failed."
            )

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
            reply = self.rtspSocket.recv(256)
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
            messagebox.showwarning(
                "Unable to Bind", f"Unable to bind PORT={self.rtpPort}"
            )

    def handler(self):
        """Handle window close event."""
        self.pauseMovie()
        if messagebox.askokcancel("Quit? ", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            self.playMovie()
