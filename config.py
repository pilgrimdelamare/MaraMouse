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
MIN_DETECTION_CONFIDENCE = 0.5
MIN_TRACKING_CONFIDENCE = 0.5

# --- Movimento cursore (clutch relativo) ---
# Moltiplicatore di sensibilita: delta landmark -> pixel cursore
CURSOR_SENSITIVITY = 1500.0
# Smoothing esponenziale (0 = nessuno, 1 = massimo). Basso = piu' reattivo
# ma piu' tremolio; alto = fluido ma con ritardo. A ~10 fps meglio tenerlo basso.
CURSOR_SMOOTHING = 0.25

# --- Click (tap verticale della punta del dito) ---
# Il click e' un "tap": la punta del dito (che resta esteso) scende e risale.
# Misuriamo lo spostamento verticale della punta rispetto alla nocca (MCP),
# normalizzato, cosi' e' robusto al movimento globale della mano.
# Profondita' minima del tap per "armare" il click.
CLICK_TAP_THRESHOLD = 0.06
# Sotto questa profondita' il dito e' "risalito" -> scatta il click.
CLICK_TAP_RELEASE = 0.03
# Frame minimi tra due click consecutivi. Basso: il meccanismo arma/rilascia
# gia' impedisce che un singolo tap conti due volte, quindi non serve un
# cooldown lungo, che anzi impedirebbe il secondo tap del doppio click.
CLICK_DEBOUNCE_FRAMES = 3
# Se un secondo tap dell'indice arriva entro questi frame dal primo click,
# viene interpretato come doppio click (~2s a 10 fps).
DOUBLE_CLICK_WINDOW_FRAMES = 20

# --- Scroll ---
# Pixel di delta landmark prima che lo scroll si attivi
SCROLL_DEAD_ZONE = 0.008
# Moltiplicatore scroll (l'accumulatore evita di perdere i movimenti piccoli,
# quindi si puo' tenere alto senza troncamenti)
SCROLL_SENSITIVITY = 40.0
# Frame iniziali per decidere axis-lock
SCROLL_AXIS_LOCK_FRAMES = 3

# --- Zoom (pinch) ---
# Distanza normalizzata min/max tra pollice e indice (range per il VALORE zoom)
PINCH_MIN_DISTANCE = 0.03
PINCH_MAX_DISTANCE = 0.20
# Soglia di ATTIVAZIONE del pinch: sotto questa distanza il gesto e' pinch,
# sopra e' un normale indice esteso (evita di confondere il click con lo zoom).
PINCH_ACTIVATE_DISTANCE = 0.07

# --- Aggancio globale (engage/standby) ---
# Frame di pugno tenuto per agganciare il tracking dallo standby (~1s a 10 fps)
ENGAGE_HOLD_FRAMES = 10
# Frame senza alcuna azione prima di tornare in standby (~45s a 10 fps)
INACTIVITY_TIMEOUT_FRAMES = 450

# --- Dettatura ---
# Frame consecutivi col gesto (corna) prima di attivare il toggle,
# per evitare toggle accidentali della dettatura.
DICTATION_HOLD_FRAMES = 14

# --- Debounce generale ---
# Frame minimi prima di cambiare stato nella macchina a stati
STATE_CHANGE_DEBOUNCE = 4
