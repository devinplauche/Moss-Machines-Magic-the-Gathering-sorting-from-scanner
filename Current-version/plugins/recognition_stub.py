from .base import RecognitionPluginBase


class Plugin(RecognitionPluginBase):
    name = 'recognition'

    def __init__(self):
        pass

    def recognize(self, frame, card_approx, scanner):
        """Return None to indicate fallback to built-in recognition."""
        return None
