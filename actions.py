"""Azioni OS-level via pynput: mouse e tastiera."""

import threading

from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key

try:
    import winsound
except ImportError:
    winsound = None


mouse = MouseController()
keyboard = KeyboardController()


def _beep_seq(tones):
    """Suona una sequenza di toni (freq, durata_ms) in un thread separato."""
    if winsound is None:
        return

    def play():
        for freq, dur in tones:
            winsound.Beep(freq, dur)

    threading.Thread(target=play, daemon=True).start()


def beep_hand():
    """Mano rilevata in standby: singolo tono medio."""
    _beep_seq([(800, 90)])


def beep_engage():
    """Tracking agganciato: due toni ascendenti."""
    _beep_seq([(1000, 90), (1500, 110)])


def beep_disengage():
    """Tornato in standby: due toni discendenti."""
    _beep_seq([(800, 90), (450, 150)])


def move_cursor(dx, dy):
    mouse.move(dx, dy)


def left_click():
    mouse.click(Button.left, 1)


def double_click():
    mouse.click(Button.left, 2)


def right_click():
    mouse.click(Button.right, 1)


def scroll(sx, sy):
    """Scroll: sy > 0 = su, sy < 0 = giu. sx per scroll orizzontale."""
    if sy != 0:
        mouse.scroll(0, -sy)
    if sx != 0:
        mouse.scroll(sx, 0)


def zoom(delta):
    """Zoom via Ctrl+scroll (come fanno browser e molte app)."""
    with keyboard.pressed(Key.ctrl):
        clicks = int(delta * 5)
        if clicks != 0:
            mouse.scroll(0, clicks)


def toggle_dictation():
    """Simula Win+H per attivare Voice Typing di Windows."""
    keyboard.press(Key.cmd)
    keyboard.press('h')
    keyboard.release('h')
    keyboard.release(Key.cmd)
