"""Wrapper per MediaPipe Hand Landmarker: restituisce i 21 landmark normalizzati.

Supporta sia mediapipe <1.0 (mp.solutions.hands) che >=1.0 (mp.tasks).
"""

import numpy as np

try:
    # MediaPipe <1.0 legacy API. In 0.10.x le solutions sono a caricamento
    # pigro: hasattr(mp.solutions,'hands') puo' dare False anche se disponibili.
    # Proviamo un import esplicito per rilevarle in modo affidabile.
    import mediapipe as mp
    from mediapipe.python.solutions import hands as _mp_hands
    _legacy = True
except Exception:
    _mp_hands = None
    _legacy = False

print(f"[HandTracker] API: {'legacy mp.solutions.hands' if _legacy else 'Tasks (>=1.0)'}")

if _legacy:
    class HandTracker:
        def __init__(self, max_hands=1, min_detection=0.7, min_tracking=0.6):
            self.hands = _mp_hands.Hands(
                static_image_mode=False,
                max_num_hands=max_hands,
                min_detection_confidence=min_detection,
                min_tracking_confidence=min_tracking,
            )

        def process(self, frame_rgb):
            result = self.hands.process(frame_rgb)
            if not result.multi_hand_landmarks:
                return None
            hand = result.multi_hand_landmarks[0]
            return np.array([(lm.x, lm.y, lm.z) for lm in hand.landmark])

        def release(self):
            self.hands.close()
else:
    # MediaPipe >=1.0 Tasks API
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    import urllib.request
    import os

    _MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
    _MODEL_PATH = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")

    def _ensure_model():
        if not os.path.exists(_MODEL_PATH):
            print(f"Scarico modello hand_landmarker (~12MB)...")
            urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
            print("Modello scaricato.")

    class HandTracker:
        def __init__(self, max_hands=1, min_detection=0.7, min_tracking=0.6):
            _ensure_model()
            # RunningMode.IMAGE (non VIDEO): su mediapipe >=1.0 la modalita' VIDEO
            # con immagini non quadrate fallisce silenziosamente (warning
            # "NORM_RECT without IMAGE_DIMENSIONS ... square ROI" e 0 rilevamenti).
            # IMAGE elabora ogni frame in modo indipendente ed e' affidabile su CPU.
            options = vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=_MODEL_PATH),
                running_mode=vision.RunningMode.IMAGE,
                num_hands=max_hands,
                min_hand_detection_confidence=min_detection,
                min_tracking_confidence=min_tracking,
            )
            self.landmarker = vision.HandLandmarker.create_from_options(options)

        def process(self, frame_rgb):
            h, w = frame_rgb.shape[:2]
            # Padding a immagine quadrata: su mediapipe >=1.0 la proiezione dei
            # landmark e' supportata solo su ROI quadrata (warning "NORM_RECT
            # without IMAGE_DIMENSIONS ... square ROI") -> con 640x480 il palm
            # detector trova la mano ma i landmark falliscono. Il padding
            # preserva l'aspect ratio, quindi i gesti restano corretti.
            size = max(h, w)
            top = (size - h) // 2
            left = (size - w) // 2
            if h != w:
                square = np.zeros((size, size, 3), dtype=frame_rgb.dtype)
                square[top:top + h, left:left + w] = frame_rgb
                square = np.ascontiguousarray(square)
            else:
                square = frame_rgb

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=square)
            result = self.landmarker.detect(mp_image)
            if not result.hand_landmarks:
                return None
            hand = result.hand_landmarks[0]

            # Riconverte i landmark dalle coordinate del quadrato a quelle
            # normalizzate rispetto al frame originale w x h.
            out = []
            for lm in hand:
                x = (lm.x * size - left) / w
                y = (lm.y * size - top) / h
                out.append((x, y, lm.z))
            return np.array(out)

        def release(self):
            self.landmarker.close()
