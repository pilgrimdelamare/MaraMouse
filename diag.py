"""Diagnostica MaraMouse: verifica camera, modello e tracking mano.

Uso sul ThinkPad:
    python diag.py            # webcam device 0
    python diag.py --source 1 # altra webcam

Mostra: apertura camera, presenza/scarico modello, quanti frame contengono
una mano in 6 secondi, e la luminosita' media dell'immagine.
"""
import argparse
import os
import sys
import time

import cv2

import config
from camera import Camera
from hand_tracker import HandTracker

parser = argparse.ArgumentParser()
parser.add_argument("--source", default="0")
args = parser.parse_args()
try:
    source = int(args.source)
except ValueError:
    source = args.source

model_path = os.path.join(os.path.dirname(__file__), "hand_landmarker.task")
print(f"[1] Modello presente prima dell'avvio: {os.path.exists(model_path)}")

print(f"[2] Apro camera source={source} ...")
try:
    cam = Camera(source, config.CAMERA_WIDTH, config.CAMERA_HEIGHT, config.CAMERA_FPS)
except Exception as e:
    print("    ERRORE apertura camera:", repr(e))
    sys.exit(1)
print("    Camera aperta.")

print("[3] Inizializzo tracker (scarica il modello se assente) ...")
try:
    tracker = HandTracker(1, 0.5, 0.5)  # confidenze piu' basse per il test
except Exception as e:
    print("    ERRORE init tracker:", repr(e))
    cam.release()
    sys.exit(1)
print(f"    Tracker OK. Modello presente ora: {os.path.exists(model_path)}")

print("[4] Colleziono 10 secondi: MOSTRA LA MANO alla webcam ...")
outdir = os.path.join(os.path.dirname(__file__), "diag_frames")
os.makedirs(outdir, exist_ok=True)
detected = frames = 0
last = None
saved = 0
hand_sizes = []  # dimensione mano come % della diagonale frame
t0 = time.time()
while time.time() - t0 < 10:
    frame = cam.read()
    if frame is None:
        time.sleep(0.005)
        continue
    frames += 1
    last = frame
    h, w = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    lm = tracker.process(rgb)
    if lm is not None:
        detected += 1
        xs = lm[:, 0]
        ys = lm[:, 1]
        bw = (xs.max() - xs.min())
        bh = (ys.max() - ys.min())
        hand_sizes.append(max(bw, bh) * 100)  # % del lato frame
        # salva un frame annotato con la mano rilevata
        if saved < 3:
            annotated = frame.copy()
            for x, y in zip((xs * w).astype(int), (ys * h).astype(int)):
                cv2.circle(annotated, (int(x), int(y)), 4, (0, 255, 255), -1)
            cv2.imwrite(os.path.join(outdir, f"hand_{saved}.jpg"), annotated)
            saved += 1
    else:
        # salva anche un frame senza rilevamento (per vedere cosa vede la webcam)
        if frames % 30 == 0 and saved < 6:
            cv2.imwrite(os.path.join(outdir, f"nohand_{frames}.jpg"), frame)

print("--- RISULTATO ---")
print(f"Frame elaborati: {frames}")
print(f"Frame con mano rilevata: {detected}  ({100*detected/max(frames,1):.0f}%)")
if hand_sizes:
    import statistics
    print(f"Dimensione mano nel frame: {statistics.mean(hand_sizes):.0f}% del lato "
          f"(min {min(hand_sizes):.0f}%, max {max(hand_sizes):.0f}%)")
    print("  (sotto ~15% = mano troppo piccola/lontana per un tracking stabile)")
if last is not None:
    print(f"Risoluzione frame: {last.shape[1]}x{last.shape[0]}")
    print(f"Luminosita media (0=nero, 255=bianco): {last.mean():.1f}")
else:
    print("NESSUN frame ricevuto dalla camera.")
print(f"Frame di esempio salvati in: {outdir}")
cam.release()
tracker.release()
