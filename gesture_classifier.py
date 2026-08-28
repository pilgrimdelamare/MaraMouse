"""Classificatore gesti rule-based dai 21 landmark MediaPipe.

Architettura ispirata a Kazuhito00/hand-gesture-recognition-using-mediapipe:
preprocessing landmark relativi + normalizzati. Ma qui usiamo regole geometriche
invece di una rete neurale, per funzionare subito senza training.

Distanza pinch da pratham-bhatnagar/Gesture-Volume-Control:
math.hypot tra thumb_tip e index_tip, mappata con np.interp.

Riferimenti landmark MediaPipe:
  0=WRIST, 4=THUMB_TIP, 5=INDEX_MCP, 6=INDEX_PIP, 8=INDEX_TIP,
  9=MIDDLE_MCP, 10=MIDDLE_PIP, 12=MIDDLE_TIP, 16=RING_TIP,
  17=PINKY_MCP, 20=PINKY_TIP
"""

import math
from enum import Enum, auto

import numpy as np


class Gesture(Enum):
    NONE = auto()
    MOVE = auto()            # Mano aperta in movimento -> movimento cursore
    LEFT_CLICK = auto()      # Indice esteso, scatto giu-su
    RIGHT_CLICK = auto()     # Medio esteso, scatto giu-su
    SCROLL = auto()          # Pace (V) -> scroll
    PINCH_ZOOM = auto()      # Pinch pollice-indice -> zoom
    DISENGAGE = auto()       # Pugno chiuso -> pausa / clutch up
    PHONE = auto()           # Segno del telefono (pollice+mignolo) -> toggle standby
    DICTATION = auto()       # Corna (indice+mignolo)


def _is_finger_extended(landmarks, finger):
    """Controlla se un dito e' esteso confrontando tip vs PIP (o IP per il pollice).

    Per il pollice: confronta distanza tip-wrist vs ip-wrist lateralmente (asse x).
    Per le altre dita: tip.y < pip.y (in coordinate normalizzate, y cresce verso il basso).
    """
    if finger == "thumb":
        # Pollice: tip (4) piu' lontano dal palmo rispetto a IP (3)
        # Usiamo la distanza dal centro del palmo (landmark 9 = MIDDLE_MCP)
        palm_center = landmarks[9]
        thumb_tip = landmarks[4]
        thumb_ip = landmarks[3]
        dist_tip = math.hypot(thumb_tip[0] - palm_center[0], thumb_tip[1] - palm_center[1])
        dist_ip = math.hypot(thumb_ip[0] - palm_center[0], thumb_ip[1] - palm_center[1])
        return dist_tip > dist_ip

    tip_pip = {
        "index": (8, 6),
        "middle": (12, 10),
        "ring": (16, 14),
        "pinky": (20, 18),
    }
    tip_idx, pip_idx = tip_pip[finger]
    return landmarks[tip_idx][1] < landmarks[pip_idx][1]


def _finger_states(landmarks):
    """Restituisce dict con stato esteso/chiuso per ogni dito."""
    return {
        "thumb": _is_finger_extended(landmarks, "thumb"),
        "index": _is_finger_extended(landmarks, "index"),
        "middle": _is_finger_extended(landmarks, "middle"),
        "ring": _is_finger_extended(landmarks, "ring"),
        "pinky": _is_finger_extended(landmarks, "pinky"),
    }


def _pinch_distance(landmarks):
    """Distanza normalizzata tra pollice tip (4) e indice tip (8).
    Rif: pratham-bhatnagar/Gesture-Volume-Control."""
    return math.hypot(
        landmarks[4][0] - landmarks[8][0],
        landmarks[4][1] - landmarks[8][1],
    )


def _fingers_spread(landmarks):
    """Misura quanto le dita sono unite/sparse. Basso = dita unite."""
    tips = [8, 12, 16, 20]
    total = 0.0
    for i in range(len(tips) - 1):
        total += math.hypot(
            landmarks[tips[i]][0] - landmarks[tips[i + 1]][0],
            landmarks[tips[i]][1] - landmarks[tips[i + 1]][1],
        )
    return total


def _tap_depth(landmarks, tip, mcp):
    """Spostamento verticale della punta rispetto alla nocca, normalizzato
    sulla larghezza della mano (robusto a distanza/posizione della mano).

    A dito esteso verso l'alto la punta e' sopra la nocca (valore negativo);
    quando si fa il 'tap' la punta scende e il valore cresce. La macchina a
    stati usa la variazione di questo valore per rilevare lo scatto giu-su.
    """
    hand_scale = math.hypot(
        landmarks[5][0] - landmarks[17][0],
        landmarks[5][1] - landmarks[17][1],
    ) + 1e-6
    return (landmarks[tip][1] - landmarks[mcp][1]) / hand_scale


def _index_angle(landmarks):
    """Angolo del dito indice: MCP-PIP-TIP. Usato per rilevare lo scatto click."""
    mcp = np.array(landmarks[5][:2])
    pip_ = np.array(landmarks[6][:2])
    tip = np.array(landmarks[8][:2])

    v1 = mcp - pip_
    v2 = tip - pip_

    cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
    return math.degrees(math.acos(np.clip(cos_angle, -1.0, 1.0)))


def _middle_angle(landmarks):
    """Angolo del dito medio: MCP-PIP-TIP."""
    mcp = np.array(landmarks[9][:2])
    pip_ = np.array(landmarks[10][:2])
    tip = np.array(landmarks[12][:2])

    v1 = mcp - pip_
    v2 = tip - pip_

    cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
    return math.degrees(math.acos(np.clip(cos_angle, -1.0, 1.0)))


def classify(landmarks, pinch_min=0.03, pinch_max=0.20, pinch_activate=None):
    """Classifica il gesto corrente dai 21 landmark.

    Args:
        landmarks: numpy array (21, 3) coordinate normalizzate
        pinch_min: soglia minima distanza pinch
        pinch_max: soglia massima distanza pinch

    Returns:
        (Gesture, extra_data) dove extra_data e' un dict con info aggiuntive
        (es. pinch_value per zoom, finger angles per click).
    """
    if pinch_activate is None:
        pinch_activate = pinch_max
    fingers = _finger_states(landmarks)
    extended_count = sum(fingers.values())
    extra = {"fingers": fingers}

    # --- Pinch zoom: pollice e indice DAVVERO vicini (pinzare) ---
    # Soglia di attivazione stretta per non confonderlo con l'indice esteso.
    pinch_dist = _pinch_distance(landmarks)
    extra["pinch_distance"] = pinch_dist
    if pinch_dist < pinch_activate and fingers["index"]:
        if not fingers["middle"] and not fingers["ring"]:
            pinch_value = np.interp(pinch_dist, [pinch_min, pinch_max], [0.0, 1.0])
            extra["pinch_value"] = float(np.clip(pinch_value, 0.0, 1.0))
            return Gesture.PINCH_ZOOM, extra

    # --- Telefono: pollice + mignolo estesi, indice + medio + anulare chiusi ---
    # Toggle engage/standby. Controllato PRIMA delle corna perche' le corna
    # richiedono indice esteso, mentre telefono richiede indice chiuso.
    if fingers["thumb"] and fingers["pinky"] and not fingers["index"] \
       and not fingers["middle"] and not fingers["ring"]:
        return Gesture.PHONE, extra

    # --- Corna: indice + mignolo estesi, medio + anulare chiusi -> dettatura ---
    # (il pollice e' ignorato: il suo stato e' troppo ambiguo da rilevare)
    # Il mignolo deve essere CHIARAMENTE esteso, non solo un po' sollevato,
    # altrimenti la dettatura scatta involontariamente.
    if fingers["index"] and not fingers["middle"] and not fingers["ring"]:
        hand_scale = math.hypot(
            landmarks[5][0] - landmarks[17][0],
            landmarks[5][1] - landmarks[17][1],
        ) + 1e-6
        pinky_clearly_up = (landmarks[18][1] - landmarks[20][1]) > 0.30 * hand_scale
        if pinky_clearly_up:
            return Gesture.DICTATION, extra

    # --- 3 dita (indice+medio+anulare, mignolo chiuso) -> scroll tilt ---
    # Controllato PRIMA del peace sign (2 dita) per priorita'.
    if fingers["index"] and fingers["middle"] and fingers["ring"] \
       and not fingers["pinky"]:
        return Gesture.SCROLL, extra

    # --- Indice esteso solo -> click sinistro (alzata rilevata in state_machine) ---
    if fingers["index"] and not fingers["middle"] and not fingers["ring"] \
       and not fingers["pinky"]:
        return Gesture.LEFT_CLICK, extra

    # --- Medio esteso -> click destro (alzata rilevata in state_machine) ---
    # L'anulare e' tollerato (segue naturalmente il medio): conta che indice e
    # mignolo siano chiusi, cosi' la posa e' meno scomoda da mantenere nel tap.
    if fingers["middle"] and not fingers["index"] and not fingers["pinky"]:
        return Gesture.RIGHT_CLICK, extra

    # --- Mano aperta (>=4 dita estese) -> movimento cursore ---
    # Gesto stabile per MediaPipe. Soglia tollerante: se durante il movimento
    # si perde un dito per un istante, il debounce mantiene comunque il gesto.
    if extended_count >= 4:
        return Gesture.MOVE, extra

    # --- Pugno chiuso -> pausa / clutch up (riposiziona la mano) ---
    if extended_count <= 1 and not fingers["index"] and not fingers["middle"]:
        return Gesture.DISENGAGE, extra

    return Gesture.NONE, extra
