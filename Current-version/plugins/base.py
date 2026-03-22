class CameraPluginBase:
    """Base class for camera plugins."""
    name = "camera"

    def open(self, device=0):
        """Open camera device. Return truthy on success."""
        raise NotImplementedError()

    def read(self):
        """Read a frame. Return (ret, frame)."""
        raise NotImplementedError()

    def close(self):
        """Close device and cleanup."""
        raise NotImplementedError()


class RecognitionPluginBase:
    """Base class for recognition plugins."""
    name = "recognition"

    def recognize(self, frame, card_approx, scanner):
        """Attempt to recognize a card. Return card_info dict or None to fallback."""
        raise NotImplementedError()


class ArduinoPluginBase:
    """Base class for Arduino/serial plugins."""
    name = "arduino"

    def connect(self, port, baud):
        """Connect to device. Return True on success."""
        raise NotImplementedError()

    def send(self, cmd):
        """Send a command and return a response (or None)."""
        raise NotImplementedError()

    def close(self):
        """Close connection."""
        raise NotImplementedError()

    def is_connected(self):
        """Return True if connected."""
        raise NotImplementedError()
