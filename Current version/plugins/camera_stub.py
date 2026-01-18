from .base import CameraPluginBase
import cv2


class Plugin(CameraPluginBase):
    name = 'camera'

    def __init__(self):
        self.cap = None

    def open(self, device=0):
        self.cap = cv2.VideoCapture(device)
        return bool(self.cap and self.cap.isOpened())

    def read(self):
        if not self.cap:
            return False, None
        return self.cap.read()

    def close(self):
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
