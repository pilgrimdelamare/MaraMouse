"""Gestione sorgenti video: webcam locale o IP Webcam Android via MJPEG.

Lo stream di rete viene letto in un thread dedicato che tiene sempre solo
l'ultimo frame disponibile. Senza questo, OpenCV accumula i frame MJPEG in un
buffer interno e il loop principale legge sempre il piu' vecchio, generando un
ritardo che cresce nel tempo. Il thread scarta i frame vecchi -> latenza minima.
"""

import threading
import time

import cv2


class Camera:
    def __init__(self, source=0, width=640, height=480, fps=30):
        """
        source: int per webcam locale, stringa URL per IP Webcam Android.
               Esempio URL: "http://192.168.1.100:8080/video"
        width/height: risoluzione di lavoro. I frame piu' grandi (tipico degli
               stream telefono a 1080p+) vengono ridimensionati a questa taglia
               per alleggerire MediaPipe e la finestra di preview.
        """
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps

        self._lock = threading.Lock()
        self._latest = None
        self._running = False
        self._thread = None

        self.cap = self._open(source)
        self._start_reader()

    def _open(self, source):
        if isinstance(source, int):
            # Windows: prova DSHOW, MSMF, poi default
            for backend in [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]:
                cap = cv2.VideoCapture(source, backend)
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(cv2.CAP_PROP_FPS, self.fps)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    return cap
                cap.release()
            raise RuntimeError(f"Nessuna webcam trovata al device {source}")
        else:
            # URL MJPEG per IP Webcam Android
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise RuntimeError(f"Impossibile aprire lo stream: {source}")
            # Riduce il buffer interno (spesso ignorato su MJPEG, ma il thread
            # di lettura sotto e' la vera difesa contro l'accumulo di ritardo).
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap

    def _start_reader(self):
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self):
        """Legge in continuo scartando i frame vecchi: tiene solo l'ultimo."""
        while self._running:
            ret = self.cap.grab()
            if not ret:
                time.sleep(0.005)
                continue
            ret, frame = self.cap.retrieve()
            if not ret or frame is None:
                continue
            frame = self._resize_to_work(frame)
            with self._lock:
                self._latest = frame

    def _resize_to_work(self, frame):
        h, w = frame.shape[:2]
        if w != self.width or h != self.height:
            frame = cv2.resize(frame, (self.width, self.height),
                               interpolation=cv2.INTER_AREA)
        return frame

    def read(self):
        with self._lock:
            frame = self._latest
            self._latest = None  # evita di riprocessare lo stesso frame
        return frame

    def _stop_reader(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def release(self):
        self._stop_reader()
        self.cap.release()

    def switch(self, new_source):
        self._stop_reader()
        self.cap.release()
        with self._lock:
            self._latest = None
        self.source = new_source
        self.cap = self._open(new_source)
        self._start_reader()
