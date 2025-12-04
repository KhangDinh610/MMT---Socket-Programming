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
            # print("Malformed RTSP request line.")
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
