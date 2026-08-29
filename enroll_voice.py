"""Registrazione impronta vocale per MaraMouse speaker verification.

Uso: python enroll_voice.py

Registra alcune ripetizioni della wake word nelle condizioni acustiche reali
(~1.5m dal PC, ventola e TV accese), estrae l'embedding medio e lo salva
in models/speaker_embedding.npy.
"""

import os
import sys
import time

import numpy as np

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
_EMB_PATH = os.path.join(_MODELS_DIR, "speaker_embedding.npy")
_SR = 16000
_RECORD_SECONDS = 3
_NUM_SAMPLES = 5


def main():
    try:
        import sounddevice as sd
    except ImportError:
        print("Installa sounddevice: pip install sounddevice")
        sys.exit(1)

    try:
        import torch  # noqa: F401
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:
            from speechbrain.pretrained import EncoderClassifier
    except ImportError:
        print("Installa speechbrain: pip install speechbrain")
        sys.exit(1)

    os.makedirs(_MODELS_DIR, exist_ok=True)

    print("=== Registrazione impronta vocale MaraMouse ===")
    print()
    print("IMPORTANTE: registra nelle condizioni reali di utilizzo:")
    print("  - Stai a ~1.5m dal PC (la distanza normale)")
    print("  - Ventola accesa, TV accesa se di solito lo e'")
    print("  - Parla con il tono e il volume che userai normalmente")
    print()
    print(f"Verranno registrate {_NUM_SAMPLES} ripetizioni di {_RECORD_SECONDS}s ciascuna.")
    print()

    # Carica il modello
    print("Caricamento modello speaker verification...")
    model = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=os.path.join(_MODELS_DIR, "spkrec-ecapa"),
    )
    print("Modello caricato.")
    print()

    embeddings = []

    for i in range(_NUM_SAMPLES):
        input(f"[{i+1}/{_NUM_SAMPLES}] Premi INVIO, poi di' 'Maramouse' "
              f"(hai {_RECORD_SECONDS}s)... ")
        print("  Registrazione...", end="", flush=True)

        audio = sd.rec(int(_SR * _RECORD_SECONDS), samplerate=_SR,
                       channels=1, dtype="float32")
        sd.wait()
        print(" fatto.")

        # Calcola livello audio per feedback
        rms = np.sqrt(np.mean(audio ** 2))
        print(f"  Livello RMS: {rms:.4f}", end="")
        if rms < 0.005:
            print(" (BASSO — parla piu' forte o avvicinati)")
        elif rms > 0.3:
            print(" (ALTO — il microfono potrebbe saturare)")
        else:
            print(" (OK)")

        # Estrai embedding
        import torch
        waveform = torch.from_numpy(audio.T)  # (1, samples)
        emb = model.encode_batch(waveform).squeeze().cpu().numpy()
        embeddings.append(emb)
        print(f"  Embedding estratto (dim={emb.shape[0]})")

    # Media degli embedding
    avg_embedding = np.mean(embeddings, axis=0)
    # Normalizza
    avg_embedding = avg_embedding / (np.linalg.norm(avg_embedding) + 1e-8)

    np.save(_EMB_PATH, avg_embedding)
    print()
    print(f"Impronta vocale salvata in: {_EMB_PATH}")
    print(f"Dimensione embedding: {avg_embedding.shape[0]}")

    # Verifica: calcola similarita' tra i campioni e la media
    print()
    print("Verifica coerenza tra i campioni:")
    for i, emb in enumerate(embeddings):
        cos = np.dot(emb, avg_embedding) / (
            np.linalg.norm(emb) * np.linalg.norm(avg_embedding) + 1e-8
        )
        print(f"  Campione {i+1}: similarita' {cos:.3f}")

    print()
    print("Registrazione completata. Ora puoi usare MaraMouse con il gate vocale.")


if __name__ == "__main__":
    main()
