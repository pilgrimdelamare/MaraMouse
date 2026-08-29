"""MaraMouse - Controllo PC a gesti della mano.

Pipeline: Camera -> MediaPipe Hand Landmarker -> Classificatore gesti ->
          Macchina a stati -> Azioni OS (pynput)

Sorgenti video supportate:
  - Webcam locale (default: device 0)
  - IP Webcam Android via MJPEG (es: http://192.168.1.100:8080/video)

Uso:
  python main.py                          # webcam locale
  python main.py --source http://IP:8080/video  # IP Webcam Android
  python main.py --source 1              # seconda webcam
"""

import argparse
import os
import sys
import warnings

# Silenzia log TensorFlow/MediaPipe/absl PRIMA di qualsiasi import
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"          # TF: solo errori fatali
os.environ["GLOG_minloglevel"] = "3"               # glog (MediaPipe C++)
os.environ["ABSL_MIN_LOG_LEVEL"] = "3"             # absl
# Silenzia tutti i warnings Python non critici (soundcard, protobuf, ecc.)
warnings.filterwarnings("ignore")

import cv2

import config
import settings
from camera import Camera
from hand_tracker import HandTracker
from gesture_classifier import classify, Gesture, _finger_states
from state_machine import StateMachine
import actions

# Audio gate: basta sounddevice + vosk (o openwakeword se c'e' il modello ONNX)
try:
    import sounddevice  # noqa: F401
    from audio_gate import AudioGate
    _AUDIO_AVAILABLE = True
except ImportError:
    _AUDIO_AVAILABLE = False


def parse_args():
    parser = argparse.ArgumentParser(description="MaraMouse - Hand gesture mouse control")
    parser.add_argument("--source", default=str(config.CAMERA_SOURCE),
                        help="Camera source: device index (0,1,...) or MJPEG URL")
    parser.add_argument("--sensitivity", type=float, default=config.CURSOR_SENSITIVITY,
                        help="Cursor movement sensitivity")
    parser.add_argument("--no-preview", action="store_true",
                        help="Disable camera preview window")
    parser.add_argument("--debug", action="store_true",
                        help="Mostra scheletro mano e stato dita, NON muove il mouse")
    parser.add_argument("--no-audio", action="store_true",
                        help="Disabilita gate vocale (usa solo gesto telefono)")
    parser.add_argument("--list-devices", action="store_true",
                        help="Mostra dispositivi audio e esci (per configurare loopback)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Parse source: int per webcam, stringa per URL
    try:
        source = int(args.source)
    except ValueError:
        source = args.source

    if args.list_devices:
        from audio_gate import list_devices
        list_devices()
        return

    # Carica impostazioni salvate (sovrascrive config.py)
    settings.load_and_apply()

    config.CURSOR_SENSITIVITY = args.sensitivity

    # Inizializza componenti
    print("Apertura camera...", flush=True)
    cam = Camera(source, config.CAMERA_WIDTH, config.CAMERA_HEIGHT, config.CAMERA_FPS)
    print("Camera OK. Caricamento tracker...", flush=True)
    tracker = HandTracker(config.MAX_HANDS, config.MIN_DETECTION_CONFIDENCE,
                          config.MIN_TRACKING_CONFIDENCE)
    print("Tracker OK.", flush=True)

    # --- Audio gate ---
    use_audio = (config.AUDIO_GATE_ENABLED and _AUDIO_AVAILABLE
                 and not args.no_audio)
    audio_gate = None
    if use_audio:
        print("Avvio gate vocale...", flush=True)
        audio_gate = AudioGate()
        audio_gate.start()
    elif not args.no_audio and config.AUDIO_GATE_ENABLED and not _AUDIO_AVAILABLE:
        print("WARN: dipendenze audio mancanti, gate vocale disabilitato.")
        print("      Installa: pip install sounddevice vosk")

    sm = StateMachine(use_audio_gate=use_audio)

    debug = args.debug

    print("MaraMouse avviato. 'q'=esci, 's'=impostazioni, '1/2/3'=camera.")
    print(f"Sorgente: {source} | Sensibilita: {config.CURSOR_SENSITIVITY}")
    if use_audio:
        print("Gate vocale: ATTIVO (di' 'Maramouse' per armare)")
    else:
        print("Gate vocale: DISATTIVATO (segno telefono diretto)")
    if debug:
        print("MODALITA DEBUG: mouse NON controllato, mostro landmark e stato dita.")

    show_preview = not args.no_preview
    window_placed = False
    event_text = ""
    event_timer = 0
    # La finestra viene creata da cv2.imshow al primo frame (AUTOSIZE): il frame
    # e' gia' ridimensionato a 640x480 in Camera, quindi resta piccola. Non usiamo
    # namedWindow+resizeWindow prima del primo frame perche' su Windows la finestra
    # partirebbe minimizzata nella taskbar.

    while True:
        frame = cam.read()
        if frame is None:
            # Nessun frame nuovo dal lettore threaded: lascia respirare la CPU
            # e ricontrolla, senza spammare ne' bloccare la finestra.
            cv2.waitKey(1)
            continue

        # Mirror per feedback naturale
        frame = cv2.flip(frame, 1)

        # BGR -> RGB per MediaPipe
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_rgb.flags.writeable = False

        # Tracking mano
        landmarks = tracker.process(frame_rgb)

        # Classificazione (anche senza mano: gesto NONE) -> update unificato,
        # cosi' i bip e il timeout funzionano pure quando la mano non e' presente.
        if landmarks is not None:
            gesture, extra = classify(landmarks, config.PINCH_MIN_DISTANCE,
                                      config.PINCH_MAX_DISTANCE,
                                      config.PINCH_ACTIVATE_DISTANCE)
        else:
            gesture, extra = Gesture.NONE, {}

        # Poll gate vocale
        audio_armed = audio_gate.poll_armed() if audio_gate else False

        result = sm.update(gesture, extra, landmarks, audio_armed=audio_armed)

        # In debug non tocchiamo il mouse: solo osservazione.
        if not debug:
            if result["move"]:
                actions.move_cursor(*result["move"])
            if result["left_click"]:
                actions.left_click()
            if result["double_click"]:
                actions.double_click()
            if result["right_click"]:
                actions.right_click()
            if result["scroll"]:
                actions.scroll(*result["scroll"])
            if result["zoom"] is not None:
                actions.zoom(result["zoom"])
            if result["dictation_toggle"]:
                actions.toggle_dictation()

        # Bip di stato (sempre, anche in debug e anche senza mano nel frame)
        if result["engage_event"] == "armed":
            actions.beep_armed()
        elif result["engage_event"] == "hand_seen":
            actions.beep_hand()
        elif result["engage_event"] == "on":
            actions.beep_engage()
        elif result["engage_event"] == "off":
            actions.beep_disengage()
        elif result["engage_event"] == "armed_timeout":
            actions.beep_disengage()

        # Evento da mostrare in overlay
        ev = None
        if result["double_click"]:
            ev = "DOUBLE CLICK"
        elif result["left_click"]:
            ev = "LEFT CLICK"
        elif result["right_click"]:
            ev = "RIGHT CLICK"
        elif result["scroll"]:
            ev = f"SCROLL {result['scroll']}"
        elif result["zoom"] is not None:
            ev = "ZOOM"
        elif result["dictation_toggle"]:
            ev = "DICTATION"
        elif result["engage_event"] == "armed":
            ev = "VOICE OK"
        elif result["engage_event"] == "hand_seen":
            ev = "HAND OK"
        elif result["engage_event"] == "armed_timeout":
            ev = "TIMEOUT"
        if ev:
            event_text = ev
            event_timer = 15

        # HUD su preview
        if show_preview:
            if landmarks is not None:
                _draw_landmarks(frame, landmarks)
                if debug:
                    _draw_finger_states(frame, landmarks)
            _draw_hud(frame, gesture, sm.state, extra, use_audio)

        # Overlay evento (lampeggia per qualche frame)
        if show_preview and event_timer > 0:
            cv2.putText(frame, event_text, (frame.shape[1] // 2 - 80, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 140, 255), 3)
            event_timer -= 1

        # Preview
        if show_preview:
            cv2.imshow("MaraMouse", frame)
            if not window_placed:
                # Porta la finestra in primo piano in un punto visibile al primo
                # frame (evita che resti nascosta/minimizzata).
                cv2.moveWindow("MaraMouse", 80, 80)
                cv2.setWindowProperty("MaraMouse", cv2.WND_PROP_TOPMOST, 1)
                window_placed = True

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            settings.open_dialog()
        elif key == ord('1'):
            cam.switch(0)
            print("Camera: webcam locale")
        elif key == ord('2'):
            cam.switch(1)
            print("Camera: device 1")
        elif key == ord('3'):
            # Placeholder per IP webcam - l'utente puo' configurare in config.py
            print("Camera 3: configura URL in config.py")

    if audio_gate:
        audio_gate.stop()
    cam.release()
    tracker.release()
    cv2.destroyAllWindows()
    print("MaraMouse chiuso.")


_HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),           # pollice
    (0, 5), (5, 6), (6, 7), (7, 8),           # indice
    (9, 10), (10, 11), (11, 12),              # medio
    (13, 14), (14, 15), (15, 16),             # anulare
    (0, 17), (17, 18), (18, 19), (19, 20),    # mignolo
    (5, 9), (9, 13), (13, 17),                # palmo
]


def _draw_landmarks(frame, landmarks):
    """Disegna scheletro e punti della mano dai landmark normalizzati."""
    h, w = frame.shape[:2]
    pts = [(int(lm[0] * w), int(lm[1] * h)) for lm in landmarks]
    for a, b in _HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 180, 0), 2)
    for x, y in pts:
        cv2.circle(frame, (x, y), 4, (0, 255, 255), -1)


def _draw_finger_states(frame, landmarks):
    """Mostra quali dita sono considerate estese (T I M R P)."""
    fingers = _finger_states(landmarks)
    labels = [("T", "thumb"), ("I", "index"), ("M", "middle"),
              ("R", "ring"), ("P", "pinky")]
    x = 10
    y = 90
    for lab, key in labels:
        up = fingers[key]
        color = (0, 255, 0) if up else (0, 0, 255)
        cv2.putText(frame, f"{lab}:{'1' if up else '0'}", (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        x += 60


def _draw_hud(frame, gesture, state, extra, use_audio_gate=True):
    """Disegna overlay informativo sul frame di preview."""
    h, w = frame.shape[:2]

    # --- IDLE ---
    if state.engage_state == "idle":
        cv2.putText(frame, "STANDBY", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        if use_audio_gate:
            cv2.putText(frame, "di' 'Maramouse' per iniziare", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        else:
            # Flusso senza audio: progresso telefono
            if gesture == Gesture.PHONE and state.engage_hold > 0:
                progress = state.engage_hold / config.ENGAGE_HOLD_FRAMES
                bar_w = int(min(progress, 1.0) * 200)
                cv2.rectangle(frame, (10, h - 30), (10 + bar_w, h - 10),
                              (0, 200, 0), -1)
                cv2.rectangle(frame, (10, h - 30), (210, h - 10),
                              (255, 255, 255), 1)
                cv2.putText(frame, "AGGANCIO... (telefono)", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)
            else:
                cv2.putText(frame, "segno telefono per agganciare", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        return

    # --- ARMED (attende gesto telefono con timeout) ---
    if state.engage_state == "armed":
        cv2.putText(frame, "ARMED", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
        # Countdown timeout
        secs_left = max(0, state.armed_timer / 10.0)
        cv2.putText(frame, f"segno telefono! ({secs_left:.0f}s)", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
        # Barra timeout
        progress = state.armed_timer / config.ARMED_TIMEOUT_FRAMES
        bar_w = int(min(progress, 1.0) * 200)
        cv2.rectangle(frame, (10, h - 30), (10 + bar_w, h - 10),
                      (0, 200, 255), -1)
        cv2.rectangle(frame, (10, h - 30), (210, h - 10), (255, 255, 255), 1)
        # Progresso gesto telefono
        if gesture == Gesture.PHONE and state.engage_hold > 0:
            hold_p = state.engage_hold / config.ENGAGE_HOLD_FRAMES
            hold_w = int(min(hold_p, 1.0) * 200)
            cv2.rectangle(frame, (10, h - 55), (10 + hold_w, h - 35),
                          (0, 255, 0), -1)
            cv2.rectangle(frame, (10, h - 55), (210, h - 35),
                          (255, 255, 255), 1)
        return

    # --- ACTIVE ---
    cv2.putText(frame, f"ACTIVE  {gesture.name}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    # Pinch value
    pinch = extra.get("pinch_value")
    if pinch is not None:
        bar_w = int(pinch * 200)
        cv2.rectangle(frame, (10, h - 30), (10 + bar_w, h - 10), (0, 255, 255), -1)
        cv2.rectangle(frame, (10, h - 30), (210, h - 10), (255, 255, 255), 1)


if __name__ == "__main__":
    main()
