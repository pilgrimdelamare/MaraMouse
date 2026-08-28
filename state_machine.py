"""Macchina a stati centrale MaraMouse.

Arbitra tra i 7 gesti, gestisce:
- Clutch relativo per il movimento cursore (pen-up/pen-down, rif: Air Canvas)
- Axis-lock per lo scroll
- Debounce anti-falsi-positivi
- Rilevamento posa mantenuta nel tempo per disimpegno
- Smoothing cursore esponenziale (rif: eviacam/tracky-mouse)
"""

from collections import deque

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

        # --- Click (tap verticale della punta) ---
        self.click_cooldown = 0      # frame di cooldown dopo un click
        self.frames_since_left_click = 10000  # per rilevare il doppio click
        self.index_tap_base = None   # profondita' minima recente (dito a riposo)
        self.index_armed = False     # tap "caricato": punta scesa, attende risalita
        self.middle_tap_base = None
        self.middle_armed = False

        # --- Scroll axis-lock ---
        self.scroll_axis = None      # "v" o "h", None = non ancora deciso
        self.scroll_deltas = deque(maxlen=config.SCROLL_AXIS_LOCK_FRAMES)
        self.prev_scroll_pos = None
        self.scroll_accum = 0.0      # accumulatore per non perdere i movimenti piccoli

        # --- Pinch zoom ---
        self.prev_pinch_value = None

        # --- Aggancio globale (engage/standby) ---
        self.engaged = False         # False = standby, non tocca il mouse
        self.engage_hold = 0         # frame di pugno tenuto per agganciare
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
            dict con le azioni da eseguire:
            {
                "move": (dx, dy) | None,
                "left_click": bool,
                "right_click": bool,
                "scroll": (sx, sy) | None,
                "zoom": float | None,  # delta zoom
                "dictation_toggle": bool,
            }
        """
        actions = {
            "move": None,
            "left_click": False,
            "double_click": False,
            "right_click": False,
            "scroll": None,
            "zoom": None,
            "dictation_toggle": False,
            "engage_event": None,   # "on" | "off" quando cambia l'aggancio
        }

        s = self.state

        # Tick cooldown
        if s.click_cooldown > 0:
            s.click_cooldown -= 1
        if s.dictation_cooldown > 0:
            s.dictation_cooldown -= 1
        # Tempo dall'ultimo click sinistro (per il doppio click)
        if s.frames_since_left_click < 10000:
            s.frames_since_left_click += 1

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
        # In standby il sistema ignora tutto tranne il gesto "telefono" tenuto.
        if not s.engaged:
            if gesture == Gesture.PHONE:
                s.engage_hold += 1
                if s.engage_hold >= config.ENGAGE_HOLD_FRAMES:
                    s.engaged = True
                    s.engage_hold = 0
                    s.inactivity = 0
                    actions["engage_event"] = "on"
            else:
                s.engage_hold = 0
            return actions

        # --- Gestione per gesto ---
        # Centro del palmo: media di polso e nocche, piu' stabile del solo polso.
        if landmarks is not None:
            palm = np.mean(landmarks[[0, 5, 9, 13, 17]], axis=0)[:2]
        else:
            palm = None
        wrist = palm

        if gesture == Gesture.MOVE:
            actions["move"] = self._handle_move(wrist)

        elif gesture == Gesture.LEFT_CLICK:
            if self._handle_left_click(extra):
                # Secondo tap entro la finestra -> doppio click, altrimenti singolo
                if s.frames_since_left_click <= config.DOUBLE_CLICK_WINDOW_FRAMES:
                    actions["double_click"] = True
                else:
                    actions["left_click"] = True
                s.frames_since_left_click = 0

        elif gesture == Gesture.RIGHT_CLICK:
            actions["right_click"] = self._handle_right_click(extra)

        elif gesture == Gesture.SCROLL:
            if landmarks is not None:
                scroll_pos = np.mean(landmarks[[8, 12]], axis=0)[:2]
                actions["scroll"] = self._handle_scroll(scroll_pos)

        elif gesture == Gesture.PINCH_ZOOM:
            actions["zoom"] = self._handle_zoom(extra)

        elif gesture == Gesture.DISENGAGE:
            pass  # pugno = pausa/clutch: nessuna azione (il movimento e' sospeso)

        elif gesture == Gesture.PHONE:
            # Gesto "telefono" tenuto -> torna in standby
            s.engage_hold += 1
            if s.engage_hold >= config.ENGAGE_HOLD_FRAMES:
                s.engaged = False
                s.engage_hold = 0
                s.inactivity = 0
                actions["engage_event"] = "off"
                return actions

        elif gesture == Gesture.DICTATION:
            actions["dictation_toggle"] = self._handle_dictation()

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

    def _exit_state(self, old_gesture):
        """Reset stato specifico quando si esce da un gesto."""
        s = self.state
        if old_gesture == Gesture.MOVE:
            s.prev_wrist = None
            s.is_clutched = False
        elif old_gesture == Gesture.SCROLL:
            s.scroll_axis = None
            s.scroll_deltas.clear()
            s.prev_scroll_pos = None
            s.scroll_accum = 0.0
        elif old_gesture == Gesture.PINCH_ZOOM:
            s.prev_pinch_value = None
        elif old_gesture in (Gesture.LEFT_CLICK, Gesture.RIGHT_CLICK):
            s.index_tap_base = None
            s.index_armed = False
            s.middle_tap_base = None
            s.middle_armed = False
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

    def _handle_left_click(self, extra):
        """Click sinistro: tap verticale della punta dell'indice (giu-su).

        Il dito resta esteso, quindi il gesto non si perde durante lo scatto.
        Si arma quando la punta scende oltre la soglia e scatta quando risale.
        """
        return self._detect_tap(extra, "index_tap", "index_tap_base", "index_armed")

    def _handle_right_click(self, extra):
        """Click destro: tap verticale della punta del medio."""
        return self._detect_tap(extra, "middle_tap", "middle_tap_base", "middle_armed")

    def _detect_tap(self, extra, key, base_attr, armed_attr):
        s = self.state
        if s.click_cooldown > 0:
            return False

        rel = extra.get(key)
        if rel is None:
            return False

        base = getattr(s, base_attr)
        if base is None:
            setattr(s, base_attr, rel)
            return False

        # La baseline segue la posizione "alta" (a riposo) della punta.
        if rel < base:
            setattr(s, base_attr, rel)
            base = rel

        depth = rel - base  # quanto la punta e' scesa rispetto al riposo

        if not getattr(s, armed_attr):
            if depth > config.CLICK_TAP_THRESHOLD:
                setattr(s, armed_attr, True)
        else:
            if depth < config.CLICK_TAP_RELEASE:
                # Risalita completata -> click
                setattr(s, armed_attr, False)
                setattr(s, base_attr, rel)
                s.click_cooldown = config.CLICK_DEBOUNCE_FRAMES
                return True
        return False

    def _handle_scroll(self, scroll_pos):
        """Scroll con axis-lock: il delta dominante nei primi frame decide l'asse."""
        s = self.state

        if s.prev_scroll_pos is None:
            s.prev_scroll_pos = scroll_pos.copy()
            return None

        dx = scroll_pos[0] - s.prev_scroll_pos[0]
        dy = scroll_pos[1] - s.prev_scroll_pos[1]
        s.prev_scroll_pos = scroll_pos.copy()

        # Dead zone
        if abs(dx) < config.SCROLL_DEAD_ZONE and abs(dy) < config.SCROLL_DEAD_ZONE:
            return None

        # Axis-lock: decidi l'asse nei primi N frame
        if s.scroll_axis is None:
            s.scroll_deltas.append((abs(dx), abs(dy)))
            if len(s.scroll_deltas) >= config.SCROLL_AXIS_LOCK_FRAMES:
                avg_dx = np.mean([d[0] for d in s.scroll_deltas])
                avg_dy = np.mean([d[1] for d in s.scroll_deltas])
                s.scroll_axis = "h" if avg_dx > avg_dy else "v"
            return None

        # Accumulatore: i movimenti piccoli si sommano invece di essere troncati
        # a zero da int(), cosi' lo scroll parte anche con gesti lenti.
        delta = dy if s.scroll_axis == "v" else dx
        s.scroll_accum += delta * config.SCROLL_SENSITIVITY
        step = int(s.scroll_accum)
        if step == 0:
            return None
        s.scroll_accum -= step
        return (0, step) if s.scroll_axis == "v" else (step, 0)

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
