"""Configurazione centralizzata MaraMouse."""

# --- Sorgenti video ---
# 0 = webcam integrata, oppure URL MJPEG per IP Webcam Android
# Esempio: "http://192.168.1.100:8080/video"
CAMERA_SOURCE = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30

# --- MediaPipe Hand Landmarker ---
MAX_HANDS = 1
MIN_DETECTION_CONFIDENCE = 0.35
MIN_TRACKING_CONFIDENCE = 0.35

# --- Movimento cursore (clutch relativo) ---
# Moltiplicatore di sensibilita: delta landmark -> pixel cursore
CURSOR_SENSITIVITY = 4500.0
# Smoothing esponenziale (0 = nessuno, 1 = massimo). Basso = piu' reattivo
# ma piu' tremolio; alto = fluido ma con ritardo. A ~10 fps meglio tenerlo basso.
CURSOR_SMOOTHING = 0.25

# --- Click (rilevamento alzata dito) ---
# Il click scatta quando un dito passa da chiuso a esteso (alzata).
# Indice alzato = click sinistro, indice+medio insieme = doppio click,
# medio alzato da solo = click destro.
# Frame di cooldown dopo un click per evitare ri-fire da flicker.
CLICK_COOLDOWN_FRAMES = 5

# --- Scroll (inclinazione mano con 3 dita) ---
# Con 3 dita estese (indice+medio+anulare, mignolo chiuso), lo scroll e'
# controllato dall'inclinazione della mano (tipo joystick): inclina a dx -> giu,
# inclina a sx -> su. La prima posizione e' il neutro.
# Angolo morto in radianti (~8.5 gradi): sotto questa inclinazione niente scroll.
SCROLL_TILT_DEAD_ZONE = 0.15
# Moltiplicatore inclinazione -> velocita' scroll
SCROLL_TILT_SENSITIVITY = 3.0

# --- Zoom (pinch) ---
# Distanza normalizzata min/max tra pollice e indice (range per il VALORE zoom)
PINCH_MIN_DISTANCE = 0.03
PINCH_MAX_DISTANCE = 0.20
# Soglia di ATTIVAZIONE del pinch: sotto questa distanza il gesto e' pinch,
# sopra e' un normale indice esteso (evita di confondere il click con lo zoom).
PINCH_ACTIVATE_DISTANCE = 0.07

# --- Aggancio globale (engage/standby) ---
# Frame di gesto "telefono" tenuto per toggle engage/standby (~1s a 10 fps)
ENGAGE_HOLD_FRAMES = 10
# Cooldown dopo engage/disengage: il gesto telefono e' ignorato per ~1s,
# evita toggle accidentali se il segno viene tenuto troppo a lungo.
ENGAGE_COOLDOWN_FRAMES = 20
# Frame senza alcuna azione prima di tornare in standby (~45s a 10 fps)
INACTIVITY_TIMEOUT_FRAMES = 450

# --- Dettatura ---
# Frame consecutivi col gesto (corna) prima di attivare il toggle,
# per evitare toggle accidentali della dettatura.
DICTATION_HOLD_FRAMES = 14

# --- Audio gate (wake word + speaker verification) ---
# Il gate vocale richiede "Maramouse" + verifica parlante prima di armare il gesto.
AUDIO_GATE_ENABLED = True
# Nome file del modello ONNX nella cartella models/ (se presente, usa openWakeWord)
WAKE_WORD_MODEL = "maramouse.onnx"
# Grammatica Vosk: usata quando il modello ONNX non esiste.
# Il recognizer accetta solo queste frasi (tutto il resto e' [unk]).
# "mara mouse" copre la pronuncia naturale; varianti fonetiche aiutano.
WAKE_WORD_GRAMMAR = ["mara mouse", "mara maus", "[unk]"]
# Nome file dell'embedding di riferimento nella cartella models/ (generato da enroll_voice.py)
SPEAKER_EMBEDDING = "speaker_embedding.npy"
# Soglia di confidenza per il rilevamento wake word openWakeWord (0-1)
WAKE_WORD_THRESHOLD = 0.5
# Soglia similarita' coseno per speaker verification (0-1)
SPEAKER_THRESHOLD = 0.25
# Frame di timeout nello stato ARMED prima di tornare a IDLE (~8s a 10fps)
ARMED_TIMEOUT_FRAMES = 80

# --- Debounce generale ---
# Frame minimi prima di cambiare stato nella macchina a stati
STATE_CHANGE_DEBOUNCE = 4
