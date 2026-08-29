"""Azioni OS-level via pynput: mouse e tastiera, piu' i suoni di stato."""

import os

from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key

try:
    import winsound
except ImportError:
    winsound = None


mouse = MouseController()
keyboard = KeyboardController()

_SOUND_DIR = os.path.join(os.path.dirname(__file__), "assets", "sounds")


def _play(filename):
    """Riproduce un .wav in modo asincrono (non blocca il loop di tracking)."""
    if winsound is None:
        return
    path = os.path.join(_SOUND_DIR, filename)
    if os.path.exists(path):
        winsound.PlaySound(
            path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )


def beep_hand():
    """Mano rilevata in standby."""
    _play("hand.wav")


def beep_armed():
    """Voce riconosciuta — stato ARMED (doppio bip ascendente)."""
    if winsound is not None:
        winsound.Beep(600, 100)
        winsound.Beep(800, 100)


def beep_engage():
    """Tracking agganciato — bip sintetico breve."""
    if winsound is not None:
        winsound.Beep(900, 150)


def beep_disengage():
    """Tornato in standby."""
    _play("disengage.wav")


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
