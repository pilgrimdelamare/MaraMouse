"""Macchina a stati centrale MaraMouse.

Arbitra tra i gesti, gestisce:
- Clutch relativo per il movimento cursore (pen-up/pen-down, rif: Air Canvas)
- Click per alzata dito (indice = sinistro, indice+medio = doppio, medio = destro)
- Scroll per inclinazione mano (3 dita + tilt, tipo joystick)
- Debounce anti-falsi-positivi
- Smoothing cursore esponenziale (rif: eviacam/tracky-mouse)
"""

import math

import numpy as np

from gesture_classifier import Gesture
import config


class GestureState:
    """Stato corrente della macchina a stati."""

    def __init__(self):
        # Stato attivo
        self.current_gesture = Gesture.NONE
        self.gesture_frames = 0  # frame consecutivi nello stesso gesto

        # --- Clutch cursore (movimento relativo) ---
        self.prev_wrist = None       # posizione wrist frame precedente
        self.smooth_dx = 0.0         # delta smoothed X
        self.smooth_dy = 0.0         # delta smoothed Y
        self.is_clutched = False     # True = pugno agganciato, cursore si muove

        # --- Click (alzata dito) ---
        self.click_cooldown = 0      # frame di cooldown dopo un click
        self.prev_fingers = None     # stato dita frame precedente

        # --- Scroll (inclinazione mano) ---
        self.scroll_base_angle = None  # angolo neutro quando si entra in scroll
        self.scroll_accum = 0.0        # accumulatore per movimenti frazionali

        # --- Pinch zoom ---
        self.prev_pinch_value = None

        # --- Aggancio globale (engage/standby) ---
        self.engaged = False         # False = standby, non tocca il mouse
        self.engage_hold = 0         # frame di gesto telefono tenuto
        self.engage_cooldown = 0     # cooldown dopo engage/disengage
        self.inactivity = 0          # frame senza azioni (per auto-standby)
        self.hand_present = False    # mano nell'inquadratura nel frame precedente

        # --- Dettatura ---
        self.dictation_active = False
        self.dictation_cooldown = 0
        self.dictation_frames = 0    # frame consecutivi col gesto tenuto

        # --- Debounce generale ---
        self.pending_gesture = Gesture.NONE
        self.pending_frames = 0


class StateMachine:
    def __init__(self):
        self.state = GestureState()

    def update(self, gesture, extra, landmarks):
        """Aggiorna la macchina a stati con il gesto classificato.

        Returns:
            dict con le azioni da eseguire.
        """
        actions = {
            "move": None,
            "left_click": False,
            "double_click": False,
            "right_click": False,
            "scroll": None,
            "zoom": None,
            "dictation_toggle": False,
            "engage_event": None,   # "on" | "off" | "hand"
        }

        s = self.state

        # Tick cooldown
        if s.click_cooldown > 0:
            s.click_cooldown -= 1
        if s.dictation_cooldown > 0:
            s.dictation_cooldown -= 1
        if s.engage_cooldown > 0:
            s.engage_cooldown -= 1

        # Stato dita corrente (per rilevare alzate)
        current_fingers = extra.get("fingers")

        # Bip quando la mano compare nell'inquadratura mentre si e' in standby
        hand_now = landmarks is not None
        if not s.engaged and hand_now and not s.hand_present:
            actions["engage_event"] = "hand"
        s.hand_present = hand_now

        # --- Debounce: richiedi N frame consecutivi prima di cambiare stato ---
        if gesture != s.current_gesture:
            if gesture == s.pending_gesture:
                s.pending_frames += 1
            else:
                s.pending_gesture = gesture
                s.pending_frames = 1

            if s.pending_frames >= config.STATE_CHANGE_DEBOUNCE:
                self._exit_state(s.current_gesture)
                s.current_gesture = gesture
                s.gesture_frames = 0
                s.pending_gesture = Gesture.NONE
                s.pending_frames = 0
            else:
                # Mantieni stato corrente durante debounce
                gesture = s.current_gesture
        else:
            s.pending_gesture = Gesture.NONE
            s.pending_frames = 0

        s.gesture_frames += 1

        # --- Aggancio globale (engage/standby) ---
        if not s.engaged:
            if gesture == Gesture.PHONE and s.engage_cooldown <= 0:
                s.engage_hold += 1
                if s.engage_hold >= config.ENGAGE_HOLD_FRAMES:
                    s.engaged = True
                    s.engage_hold = 0
                    s.inactivity = 0
                    s.engage_cooldown = config.ENGAGE_COOLDOWN_FRAMES
                    actions["engage_event"] = "on"
            else:
                if gesture != Gesture.PHONE:
                    s.engage_hold = 0
            s.prev_fingers = current_fingers
            return actions

        # --- Click per alzata dito (indipendente dal gesto debounced) ---
        self._detect_finger_rise(s, current_fingers, actions)
        s.prev_fingers = current_fingers

        # --- Gestione per gesto ---
        # Centro del palmo: media di polso e nocche, piu' stabile del solo polso.
        if landmarks is not None:
            palm = np.mean(landmarks[[0, 5, 9, 13, 17]], axis=0)[:2]
        else:
            palm = None
        wrist = palm

        if gesture == Gesture.MOVE:
            actions["move"] = self._handle_move(wrist)

        elif gesture == Gesture.SCROLL:
            if landmarks is not None:
                actions["scroll"] = self._handle_scroll_tilt(landmarks)

        elif gesture == Gesture.PINCH_ZOOM:
            actions["zoom"] = self._handle_zoom(extra)

        elif gesture == Gesture.DISENGAGE:
            pass  # pugno = pausa/clutch: nessuna azione

        elif gesture == Gesture.PHONE:
            if s.engage_cooldown <= 0:
                s.engage_hold += 1
                if s.engage_hold >= config.ENGAGE_HOLD_FRAMES:
                    s.engaged = False
                    s.engage_hold = 0
                    s.inactivity = 0
                    s.engage_cooldown = config.ENGAGE_COOLDOWN_FRAMES
                    actions["engage_event"] = "off"
                    return actions

        elif gesture == Gesture.DICTATION:
            actions["dictation_toggle"] = self._handle_dictation()

        # LEFT_CLICK, RIGHT_CLICK: il click e' gia' gestito da _detect_finger_rise,
        # qui non serve fare nulla.

        # --- Auto-standby dopo inattivita' ---
        active = (actions["move"] or actions["left_click"] or actions["double_click"]
                  or actions["right_click"] or actions["scroll"]
                  or actions["zoom"] is not None or actions["dictation_toggle"])
        if active:
            s.inactivity = 0
        else:
            s.inactivity += 1
            if s.inactivity >= config.INACTIVITY_TIMEOUT_FRAMES:
                s.engaged = False
                s.inactivity = 0
                actions["engage_event"] = "off"

        return actions

    def _detect_finger_rise(self, s, current_fingers, actions):
        """Rileva quando un dito passa da chiuso a esteso (alzata).

        - Solo indice alza -> click sinistro
        - Indice + medio alzano insieme -> doppio click
        - Solo medio alza -> click destro

        Le altre dita (anulare, mignolo) devono restare chiuse per evitare
        falsi positivi durante la transizione pugno -> mano aperta (MOVE).
        """
        if s.click_cooldown > 0 or current_fingers is None or s.prev_fingers is None:
            return

        idx_rose = current_fingers["index"] and not s.prev_fingers["index"]
        mid_rose = current_fingers["middle"] and not s.prev_fingers["middle"]
        ring_down = not current_fingers["ring"]
        pinky_down = not current_fingers["pinky"]

        if not (ring_down and pinky_down):
            return

        if idx_rose and mid_rose:
            actions["double_click"] = True
            s.click_cooldown = config.CLICK_COOLDOWN_FRAMES
        elif idx_rose and not current_fingers["middle"]:
            actions["left_click"] = True
            s.click_cooldown = config.CLICK_COOLDOWN_FRAMES
        elif mid_rose and not current_fingers["index"]:
            actions["right_click"] = True
            s.click_cooldown = config.CLICK_COOLDOWN_FRAMES

    def _exit_state(self, old_gesture):
        """Reset stato specifico quando si esce da un gesto."""
        s = self.state
        if old_gesture == Gesture.MOVE:
            s.prev_wrist = None
            s.is_clutched = False
        elif old_gesture == Gesture.SCROLL:
            s.scroll_base_angle = None
            s.scroll_accum = 0.0
        elif old_gesture == Gesture.PINCH_ZOOM:
            s.prev_pinch_value = None
        elif old_gesture == Gesture.PHONE:
            s.engage_hold = 0
        elif old_gesture == Gesture.DICTATION:
            s.dictation_frames = 0

    def _handle_move(self, wrist):
        """Movimento cursore relativo con clutch.

        Meccanica pen-up/pen-down ispirata a Air Canvas: il primo frame a mano
        aperta "aggancia" la posizione, i frame successivi calcolano il delta
        dal precedente. Chiudere il pugno stacca (clutch up) e resetta l'aggancio,
        cosi' si puo' riposizionare la mano senza muovere il cursore.
        """
        s = self.state

        if wrist is None:
            s.prev_wrist = None
            return None

        if s.prev_wrist is None:
            # Primo frame: aggancio, nessun movimento
            s.prev_wrist = wrist.copy()
            s.is_clutched = True
            return None

        # Delta grezzo
        raw_dx = (wrist[0] - s.prev_wrist[0]) * config.CURSOR_SENSITIVITY
        raw_dy = (wrist[1] - s.prev_wrist[1]) * config.CURSOR_SENSITIVITY

        # Smoothing esponenziale (rif: eviacam anti-tremolio)
        alpha = 1.0 - config.CURSOR_SMOOTHING
        s.smooth_dx = alpha * raw_dx + config.CURSOR_SMOOTHING * s.smooth_dx
        s.smooth_dy = alpha * raw_dy + config.CURSOR_SMOOTHING * s.smooth_dy

        s.prev_wrist = wrist.copy()

        dx = int(round(s.smooth_dx))
        dy = int(round(s.smooth_dy))

        if dx == 0 and dy == 0:
            return None
        return (dx, dy)

    def _handle_scroll_tilt(self, landmarks):
        """Scroll per inclinazione mano (joystick): inclina dx -> giu, sx -> su.

        Misura l'angolo del vettore polso->nocca media rispetto alla verticale.
        Il primo frame registra l'angolo neutro; le deviazioni guidano lo scroll.
        """
        s = self.state

        dx = landmarks[9][0] - landmarks[0][0]
        dy = landmarks[9][1] - landmarks[0][1]
        angle = math.atan2(dx, -dy)

        if s.scroll_base_angle is None:
            s.scroll_base_angle = angle
            return None

        tilt = angle - s.scroll_base_angle

        if abs(tilt) < config.SCROLL_TILT_DEAD_ZONE:
            return None

        s.scroll_accum += tilt * config.SCROLL_TILT_SENSITIVITY
        step = int(s.scroll_accum)
        if step == 0:
            return None
        s.scroll_accum -= step
        return (0, step)

    def _handle_zoom(self, extra):
        """Zoom da distanza pinch: delta rispetto al frame precedente."""
        s = self.state
        pinch_value = extra.get("pinch_value")
        if pinch_value is None:
            return None

        if s.prev_pinch_value is None:
            s.prev_pinch_value = pinch_value
            return None

        delta = pinch_value - s.prev_pinch_value
        s.prev_pinch_value = pinch_value

        if abs(delta) < 0.01:
            return None
        return delta

    def _handle_dictation(self):
        """Toggle dettatura: richiede il gesto tenuto per N frame + cooldown,
        per evitare toggle accidentali."""
        s = self.state
        if s.dictation_cooldown > 0:
            return False
        s.dictation_frames += 1
        if s.dictation_frames >= config.DICTATION_HOLD_FRAMES:
            s.dictation_frames = 0
            s.dictation_cooldown = 30  # ~1 secondo prima di poter ri-attivare
            return True
        return False
