from .base import ArduinoPluginBase


class Plugin(ArduinoPluginBase):
    name = 'arduino'

    def __init__(self):
        self._connected = False

    def connect(self, port, baud):
        # Minimal stub: don't actually open serial port
        self.port = port
        self.baud = baud
        self._connected = False
        return False

    def send(self, cmd):
        return None

    def close(self):
        self._connected = False

    def is_connected(self):
        return self._connected
