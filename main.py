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
import sys

import cv2

import config
from camera import Camera
from hand_tracker import HandTracker
from gesture_classifier import classify, Gesture, _finger_states
from state_machine import StateMachine
import actions


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
    return parser.parse_args()


def main():
    args = parse_args()

    # Parse source: int per webcam, stringa per URL
    try:
        source = int(args.source)
    except ValueError:
        source = args.source

    config.CURSOR_SENSITIVITY = args.sensitivity

    # Inizializza componenti
    cam = Camera(source, config.CAMERA_WIDTH, config.CAMERA_HEIGHT, config.CAMERA_FPS)
    tracker = HandTracker(config.MAX_HANDS, config.MIN_DETECTION_CONFIDENCE,
                          config.MIN_TRACKING_CONFIDENCE)
    sm = StateMachine()

    debug = args.debug

    print("MaraMouse avviato. Premi 'q' per uscire, '1/2/3' per cambiare camera.")
    print(f"Sorgente: {source} | Sensibilita: {config.CURSOR_SENSITIVITY}")
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

        result = sm.update(gesture, extra, landmarks)

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
        if result["engage_event"] == "hand":
            actions.beep_hand()
        elif result["engage_event"] == "on":
            actions.beep_engage()
        elif result["engage_event"] == "off":
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
        if ev:
            event_text = ev
            event_timer = 15

        # HUD su preview
        if show_preview:
            if landmarks is not None:
                _draw_landmarks(frame, landmarks)
                if debug:
                    _draw_finger_states(frame, landmarks)
            _draw_hud(frame, gesture, sm.state, extra)

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
        elif key == ord('1'):
            cam.switch(0)
            print("Camera: webcam locale")
        elif key == ord('2'):
            cam.switch(1)
            print("Camera: device 1")
        elif key == ord('3'):
            # Placeholder per IP webcam - l'utente puo' configurare in config.py
            print("Camera 3: configura URL in config.py")

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


def _draw_hud(frame, gesture, state, extra):
    """Disegna overlay informativo sul frame di preview."""
    h, w = frame.shape[:2]

    # Stato di aggancio globale
    if not state.engaged:
        cv2.putText(frame, "STANDBY", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        # Progresso aggancio (pugno tenuto)
        if gesture == Gesture.DISENGAGE and state.engage_hold > 0:
            progress = state.engage_hold / config.ENGAGE_HOLD_FRAMES
            bar_w = int(min(progress, 1.0) * 200)
            cv2.rectangle(frame, (10, h - 30), (10 + bar_w, h - 10), (0, 200, 0), -1)
            cv2.rectangle(frame, (10, h - 30), (210, h - 10), (255, 255, 255), 1)
            cv2.putText(frame, "AGGANCIO... (pugno)", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)
        else:
            cv2.putText(frame, "chiudi il pugno per agganciare", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        return

    # Agganciato: nome gesto corrente
    cv2.putText(frame, f"ACTIVE  {gesture.name}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    # Scroll axis
    if state.scroll_axis:
        cv2.putText(frame, f"SCROLL: {state.scroll_axis.upper()}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)

    # Pinch value
    pinch = extra.get("pinch_value")
    if pinch is not None:
        bar_w = int(pinch * 200)
        cv2.rectangle(frame, (10, h - 30), (10 + bar_w, h - 10), (0, 255, 255), -1)
        cv2.rectangle(frame, (10, h - 30), (210, h - 10), (255, 255, 255), 1)


if __name__ == "__main__":
    main()
