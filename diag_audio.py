"""Diagnostica audio MaraMouse: verifica microfono, wake word e speaker verification.

Uso: python diag_audio.py

Mostra in tempo reale il riconoscimento Vosk (o openWakeWord se presente),
la similarita' speaker verification e il livello audio. Serve per calibrare
le soglie sull'ambiente reale.
"""

import json
import os
import sys
import time

import numpy as np

import config

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


def main():
    try:
        import sounddevice as sd
    except ImportError:
        print("Installa: pip install sounddevice")
        sys.exit(1)

    # --- Scegli backend ---
    oww_model_path = os.path.join(_MODELS_DIR, config.WAKE_WORD_MODEL)
    use_vosk = not os.path.exists(oww_model_path)

    oww = None
    vosk_rec = None

    if use_vosk:
        try:
            import vosk
        except ImportError:
            print("Installa: pip install vosk")
            sys.exit(1)
        vosk.SetLogLevel(-1)
        print("[1] Caricamento modello Vosk...")
        model = vosk.Model(lang="en-us")
        grammar = json.dumps(config.WAKE_WORD_GRAMMAR)
        vosk_rec = vosk.KaldiRecognizer(model, 16000, grammar)
        vosk_rec.SetWords(False)
        print(f"[1] Wake word: Vosk, grammatica: {config.WAKE_WORD_GRAMMAR[:-1]}")
    else:
        try:
            from openwakeword.model import Model as OWWModel
            oww = OWWModel(wakeword_models=[oww_model_path])
            print(f"[1] Wake word model: {config.WAKE_WORD_MODEL}")
        except Exception as e:
            print(f"[1] Errore caricamento modello: {e}")
            sys.exit(1)

    # --- Carica speaker verification ---
    speaker_model = None
    speaker_ref = None
    emb_path = os.path.join(_MODELS_DIR, config.SPEAKER_EMBEDDING)
    if os.path.exists(emb_path):
        try:
            import torch  # noqa: F401
            try:
                from speechbrain.inference.speaker import EncoderClassifier
            except ImportError:
                from speechbrain.pretrained import EncoderClassifier

            speaker_model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.path.join(_MODELS_DIR, "spkrec-ecapa"),
            )
            speaker_ref = np.load(emb_path)
            print("[2] Speaker verification: OK")
        except Exception as e:
            print(f"[2] Speaker verification non disponibile: {e}")
    else:
        print("[2] Speaker verification: DISABILITATA (esegui enroll_voice.py)")

    # --- Audio ---
    sr = 16000
    chunk = 4096 if use_vosk else 1280

    print()
    if use_vosk:
        print("Di' 'mara mouse' al microfono. Premi Ctrl+C per uscire.")
    else:
        print(f"Soglie: wake_word={config.WAKE_WORD_THRESHOLD}, "
              f"speaker={config.SPEAKER_THRESHOLD}")
        print("Parla al microfono. Premi Ctrl+C per uscire.")
    print("-" * 70)

    ring_buf = np.zeros(sr * 3, dtype=np.int16)
    ring_pos = 0
    last_print = 0

    try:
        stream = sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                                blocksize=chunk)
        stream.start()
    except Exception as e:
        print(f"Errore apertura microfono: {e}")
        sys.exit(1)

    try:
        while True:
            data, _ = stream.read(chunk)
            audio = data.flatten()

            # Buffer circolare
            end = ring_pos + len(audio)
            if end <= len(ring_buf):
                ring_buf[ring_pos:end] = audio
            else:
                first = len(ring_buf) - ring_pos
                ring_buf[ring_pos:] = audio[:first]
                ring_buf[:len(audio) - first] = audio[first:]
            ring_pos = end % len(ring_buf)

            # RMS
            rms = np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2))

            triggered = False
            trigger_text = ""

            if use_vosk:
                raw = audio.tobytes()
                if vosk_rec.AcceptWaveform(raw):
                    result = json.loads(vosk_rec.Result())
                    text = result.get("text", "").strip().lower()
                else:
                    partial = json.loads(vosk_rec.PartialResult())
                    text = partial.get("partial", "").strip().lower()

                now = time.time()
                if now - last_print >= 0.2:
                    rms_bar = int(min(rms * 20, 30))
                    display = text if text and text != "[unk]" else "..."
                    line = (f"  RMS: {'|' * rms_bar:<30s}  "
                            f"Vosk: {display:<20s}")
                    print(f"\r{line}", end="", flush=True)
                    last_print = now

                if text and text != "[unk]":
                    triggered = True
                    trigger_text = text
            else:
                preds = oww.predict(audio)
                wake_score = max(preds.values()) if preds else 0.0
                wake_name = max(preds, key=preds.get) if preds else "?"

                now = time.time()
                if now - last_print >= 0.2:
                    bar_len = int(min(wake_score, 1.0) * 30)
                    rms_bar = int(min(rms * 20, 30))
                    line = (f"  RMS: {'|' * rms_bar:<30s}  "
                            f"Wake({wake_name[:12]:>12s}): {wake_score:.3f} "
                            f"[{'#' * bar_len:<30s}]")
                    t = " <<< TRIGGER" if wake_score >= config.WAKE_WORD_THRESHOLD else ""
                    print(f"\r{line}{t}", end="", flush=True)
                    last_print = now

                if wake_score >= config.WAKE_WORD_THRESHOLD:
                    oww.reset()
                    triggered = True
                    trigger_text = wake_name

            if triggered:
                print()
                print(f"  >>> TRIGGER: '{trigger_text}'")

                if speaker_model is not None and speaker_ref is not None:
                    import torch
                    audio_f = ring_buf.astype(np.float32) / 32768.0
                    waveform = torch.from_numpy(audio_f).unsqueeze(0)
                    emb = speaker_model.encode_batch(waveform).squeeze().cpu().numpy()
                    cos_sim = np.dot(emb, speaker_ref) / (
                        np.linalg.norm(emb) * np.linalg.norm(speaker_ref) + 1e-8
                    )
                    status = "OK" if cos_sim >= config.SPEAKER_THRESHOLD else "RIFIUTATO"
                    print(f"  >>> Speaker: {cos_sim:.3f} (soglia {config.SPEAKER_THRESHOLD}) "
                          f"-- {status}")
                else:
                    print("  >>> Speaker verification non configurata")

    except KeyboardInterrupt:
        print()
        print("Diagnostica terminata.")
    finally:
        stream.stop()
        stream.close()


if __name__ == "__main__":
    main()
