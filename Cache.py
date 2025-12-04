MAX_CACHE_FRAME = 100
MAX_AHEAD = int(3 / 4 * MAX_CACHE_FRAME)
MIN_FRAMES = 10


class Cache:
    def __init__(self):
        self.current_frame_idx = -1
        self.last_frame_idx = -1
        self.frames = MAX_CACHE_FRAME * [None]
        self.current_size = 0
        # self.locked = False

    def addFrame(self, frame):
        if not frame:
            return False

        self.last_frame_idx = (self.last_frame_idx + 1) % MAX_CACHE_FRAME
        self.frames[self.last_frame_idx] = frame
        if self.current_frame_idx == -1:
            self.current_frame_idx = 0
        self.current_size += 1 if self.current_size < MAX_CACHE_FRAME else 0

        return True

    def clean(self):
        self.current_frame_idx = -1
        self.last_frame_idx = -1
        self.current_size = 0

    def getCurrentFrame(self):
        if self.current_frame_idx == -1:
            return None

        result = (
            self.frames[self.current_frame_idx] if self.current_frame_idx >= 0 else None
        )

        if not result:
            return None

        return result

    def increaseFrame(self):
        if self.current_frame_idx != self.last_frame_idx:
            self.current_frame_idx = (self.current_frame_idx + 1) % MAX_CACHE_FRAME
            return True
        return False

    def isFull(self):
        return self.canGetMore()

    def hasValue(self):
        result = self.current_size >= MIN_FRAMES
        return result

    def aheadFrames(self):
        if self.current_size == 0:  # empty cache
            return 0

        if self.current_frame_idx <= self.last_frame_idx:
            return self.last_frame_idx - self.current_frame_idx + 1
        res = self.current_size - self.current_frame_idx + self.last_frame_idx + 1
        return res

    def behindFrames(self):
        res = self.current_size - self.aheadFrames()
        return res if res >= 0 else 0

    def canGetMore(self):
        return not ((self.aheadFrames()) < MAX_AHEAD)

    def seekRelative(self, offset):
        """
        Jump forward (positive offset) or backward (negative offset) by offset frames.
        Returns True if successful, False if target is out of cached range or invalid.

        Args:
            offset: number of frames to jump (positive = forward, negative = backward)

        Examples:
            seekRelative(5)   # jump forward 5 frames
            seekRelative(-10) # jump backward 10 frames
        """
        if self.current_size == 0 or self.current_frame_idx == -1:
            return False

        # check if offset is within valid range
        ahead = self.aheadFrames()
        behind = self.behindFrames()

        if offset > 0:
            # jumping forward: can't jump beyond last_frame_idx
            # current position counts as frame 0, so max forward is (ahead - 1)
            if offset >= ahead:
                return False
        elif offset < 0:
            # jumping backward: can't jump more than frames behind current
            if abs(offset) > behind:
                return False
        # if offset == 0, no movement needed but valid

        # calculate target index (Python modulo handles negatives correctly)
        target_idx = (self.current_frame_idx + offset) % MAX_CACHE_FRAME

        # double-check target is valid (defensive, optional if you trust bounds check)
        if not self._isIndexValid(target_idx):
            return False

        # valid target: update playback pointer
        self.current_frame_idx = target_idx
        return True

    def seekToNewest(self):
        """
        Jump playback pointer to the most recently cached frame (last_frame_idx).
        Returns True if successful, False if cache empty.

        Useful for "jump to live" functionality.
        """
        if self.current_size == 0 or self.last_frame_idx == -1:
            return False

        self.current_frame_idx = self.last_frame_idx
        return True

    def seekToOldest(self):
        """
        Jump playback pointer back to the oldest available cached frame.
        Returns True if successful, False if cache empty.

        Note: In your current implementation, the oldest frame is not explicitly tracked
        after overwrites. This resets to the first stored frame based on current_size.
        """
        if self.current_size == 0:
            return False

        # oldest frame calculation depends on whether buffer is full and wrapped
        if self.current_size < MAX_CACHE_FRAME:
            # not full: oldest is at index 0 (assuming sequential fill from beginning)
            # Actually, with your addFrame logic, oldest is wherever current_frame_idx started
            # Since you never decrement or track oldest explicitly after wrap,
            # safest is to set pointer to the "beginning" of the valid range
            oldest_idx = (self.last_frame_idx - self.current_size + 1) % MAX_CACHE_FRAME
            self.current_frame_idx = oldest_idx
        else:
            # full buffer: in circular overwrite the oldest is right after last_frame_idx
            # but your addFrame doesn't overwrite, so oldest is original starting point
            # If you never overwrite, oldest remains at the initial insertion point
            # Without overwrite your cache keeps first N frames; oldest is index 0 or first written
            # For safety, calculate based on last and size:
            oldest_idx = (self.last_frame_idx - self.current_size + 1) % MAX_CACHE_FRAME
            self.current_frame_idx = oldest_idx

        return True

    def _isIndexValid(self, idx):
        """
        Internal helper: check if a given circular index holds a valid cached frame.

        Returns True if idx is within the stored range (from oldest to last_frame_idx).
        """
        if self.current_size == 0:
            return False

        # compute oldest frame index
        oldest_idx = (self.last_frame_idx - self.current_size + 1) % MAX_CACHE_FRAME

        if oldest_idx <= self.last_frame_idx:
            # non-wrapped case
            return oldest_idx <= idx <= self.last_frame_idx
        else:
            # wrapped case: valid range is [oldest.. MAX_CACHE_FRAME-1] or [0..last]
            return idx >= oldest_idx or idx <= self.last_frame_idx

    def getFrameAtOffset(self, offset):
        """
        Peek at a frame at offset from current pointer without moving the pointer.

        Args:
            offset: number of frames ahead (positive) or behind (negative) of current

        Returns frame bytes/object if available, None otherwise.

        Example:
            getFrameAtOffset(0)  # same as getCurrentFrame()
            getFrameAtOffset(5)  # peek 5 frames ahead
            getFrameAtOffset(-3) # peek 3 frames back
        """
        if self.current_size == 0 or self.current_frame_idx == -1:
            return None

        target_idx = (self.current_frame_idx + offset) % MAX_CACHE_FRAME

        if not self._isIndexValid(target_idx):
            return None

        return self.frames[target_idx]
