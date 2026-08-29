"""Gate vocale MaraMouse: wake word + speaker verification.

Pipeline: microfono -> Vosk (riconoscimento vincolato a grammatica) ->
          speaker verification -> segnale ARMED.

Vosk con grammatica ["mara mouse", "[unk]"] funziona come un keyword spotter:
riconosce solo la wake word o rumore, con CPU bassissima e senza training.
Se e' presente un modello openWakeWord custom (maramouse.onnx), usa quello.

Gira in un thread separato, non blocca il loop video.
"""

import json
import os
import threading

import numpy as np

import config

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


class AudioGate:
    """Ascolta il microfono in background per la wake word + verifica parlante."""

    def __init__(self):
        self._armed_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._ready = False
        # Punteggi correnti per diagnostica (diag_audio.py)
        self._scores = {"wake": 0.0, "speaker": 0.0}
        self._lock = threading.Lock()

    @property
    def ready(self):
        return self._ready

    @property
    def scores(self):
        with self._lock:
            return dict(self._scores)

    def start(self):
        """Avvia il thread di ascolto."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def poll_armed(self):
        """True una sola volta quando wake word + speaker sono confermati."""
        if self._armed_event.is_set():
            self._armed_event.clear()
            return True
        return False

    def stop(self):
        """Ferma il thread di ascolto."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        try:
            import sounddevice as sd
        except ImportError:
            print("[AudioGate] Installa: pip install sounddevice")
            return

        # --- Scegli backend: openWakeWord custom se presente, altrimenti Vosk ---
        oww_model_path = os.path.join(_MODELS_DIR, config.WAKE_WORD_MODEL)
        if os.path.exists(oww_model_path):
            self._run_openwakeword(sd, oww_model_path)
        else:
            self._run_vosk(sd)

    def _run_vosk(self, sd):
        """Wake word detection via Vosk (grammatica vincolata)."""
        try:
            import vosk
        except ImportError:
            print("[AudioGate] Installa: pip install vosk")
            return

        vosk.SetLogLevel(-1)

        # Carica modello Vosk (scarica automaticamente se mancante)
        print("[AudioGate] Caricamento modello Vosk...")
        try:
            model = vosk.Model(lang="en-us")
        except Exception as e:
            print(f"[AudioGate] Errore caricamento modello Vosk: {e}")
            return

        # Grammatica vincolata: solo la wake word o rumore
        grammar = json.dumps(config.WAKE_WORD_GRAMMAR)
        rec = vosk.KaldiRecognizer(model, 16000, grammar)
        rec.SetWords(False)
        print(f"[AudioGate] Vosk pronto, grammatica: {config.WAKE_WORD_GRAMMAR[:-1]}")

        # Speaker verification (opzionale)
        speaker_model, speaker_ref = self._load_speaker_model()

        # Audio capture
        sr = 16000
        chunk = 4096  # ~256ms @ 16kHz, buon bilanciamento latenza/CPU
        buf_seconds = 3
        ring_buf = np.zeros(sr * buf_seconds, dtype=np.int16)
        ring_pos = 0

        try:
            stream = sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                                    blocksize=chunk)
            stream.start()
        except Exception as e:
            print(f"[AudioGate] Errore apertura microfono: {e}")
            return

        self._ready = True
        print("[AudioGate] In ascolto (Vosk)...")

        try:
            while not self._stop_event.is_set():
                data, overflowed = stream.read(chunk)
                if overflowed:
                    continue
                audio = data.flatten()

                # Buffer circolare per speaker verification
                end = ring_pos + len(audio)
                if end <= len(ring_buf):
                    ring_buf[ring_pos:end] = audio
                else:
                    first = len(ring_buf) - ring_pos
                    ring_buf[ring_pos:] = audio[:first]
                    ring_buf[:len(audio) - first] = audio[first:]
                ring_pos = end % len(ring_buf)

                raw = audio.tobytes()
                if rec.AcceptWaveform(raw):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip().lower()
                else:
                    partial = json.loads(rec.PartialResult())
                    text = partial.get("partial", "").strip().lower()

                if not text or text == "[unk]":
                    with self._lock:
                        self._scores["wake"] = 0.0
                    continue

                # Match: la wake word e' stata riconosciuta
                with self._lock:
                    self._scores["wake"] = 1.0
                print(f"[AudioGate] Wake word Vosk: '{text}'")

                # Speaker verification
                if speaker_model is not None and speaker_ref is not None:
                    cos_sim = self._verify_speaker(speaker_model,
                                                   speaker_ref,
                                                   ring_buf)
                    with self._lock:
                        self._scores["speaker"] = float(cos_sim)
                    if cos_sim < config.SPEAKER_THRESHOLD:
                        print(f"[AudioGate] Speaker rifiutato ({cos_sim:.3f})")
                        continue
                    print(f"[AudioGate] Speaker OK ({cos_sim:.3f}) -> ARMED")
                else:
                    print("[AudioGate] Speaker skip (non configurato) -> ARMED")

                self._armed_event.set()
        finally:
            stream.stop()
            stream.close()

    def _run_openwakeword(self, sd, model_path):
        """Wake word detection via openWakeWord (modello ONNX custom)."""
        try:
            from openwakeword.model import Model as OWWModel
        except ImportError:
            print("[AudioGate] openWakeWord non installato, uso Vosk")
            self._run_vosk(sd)
            return

        print(f"[AudioGate] Wake word model: {config.WAKE_WORD_MODEL}")
        oww = OWWModel(wakeword_models=[model_path])

        # Speaker verification (opzionale)
        speaker_model, speaker_ref = self._load_speaker_model()

        # Audio capture
        sr = 16000
        chunk = 1280  # 80ms @ 16kHz (dimensione attesa da openwakeword)
        buf_seconds = 3
        ring_buf = np.zeros(sr * buf_seconds, dtype=np.int16)
        ring_pos = 0

        try:
            stream = sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                                    blocksize=chunk)
            stream.start()
        except Exception as e:
            print(f"[AudioGate] Errore apertura microfono: {e}")
            return

        self._ready = True
        print("[AudioGate] In ascolto (openWakeWord)...")

        try:
            while not self._stop_event.is_set():
                data, overflowed = stream.read(chunk)
                if overflowed:
                    continue
                audio = data.flatten()

                # Buffer circolare per speaker verification
                end = ring_pos + len(audio)
                if end <= len(ring_buf):
                    ring_buf[ring_pos:end] = audio
                else:
                    first = len(ring_buf) - ring_pos
                    ring_buf[ring_pos:] = audio[:first]
                    ring_buf[:len(audio) - first] = audio[first:]
                ring_pos = end % len(ring_buf)

                # Wake word detection
                preds = oww.predict(audio)

                for name, score in preds.items():
                    with self._lock:
                        self._scores["wake"] = float(score)

                    if score >= config.WAKE_WORD_THRESHOLD:
                        print(f"[AudioGate] Wake word '{name}': {score:.3f}")
                        oww.reset()

                        # Speaker verification
                        if speaker_model is not None and speaker_ref is not None:
                            cos_sim = self._verify_speaker(speaker_model,
                                                           speaker_ref,
                                                           ring_buf)
                            with self._lock:
                                self._scores["speaker"] = float(cos_sim)
                            if cos_sim < config.SPEAKER_THRESHOLD:
                                print(f"[AudioGate] Speaker rifiutato ({cos_sim:.3f})")
                                continue
                            print(f"[AudioGate] Speaker OK ({cos_sim:.3f}) -> ARMED")
                        else:
                            print("[AudioGate] Speaker skip (non configurato) -> ARMED")

                        self._armed_event.set()
                        break
        finally:
            stream.stop()
            stream.close()

    @staticmethod
    def _load_speaker_model():
        """Carica il modello speaker verification e l'embedding di riferimento."""
        emb_path = os.path.join(_MODELS_DIR, config.SPEAKER_EMBEDDING)
        if not os.path.exists(emb_path):
            print(f"[AudioGate] Impronta vocale non trovata: {emb_path}")
            print("[AudioGate] Speaker verification DISABILITATA — esegui enroll_voice.py")
            return None, None

        try:
            import torch  # noqa: F401 — necessario per speechbrain
            try:
                from speechbrain.inference.speaker import EncoderClassifier
            except ImportError:
                from speechbrain.pretrained import EncoderClassifier

            model = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.path.join(_MODELS_DIR, "spkrec-ecapa"),
            )
            ref = np.load(emb_path)
            print("[AudioGate] Speaker verification OK")
            return model, ref
        except Exception as e:
            print(f"[AudioGate] Speaker verification non disponibile: {e}")
            return None, None

    @staticmethod
    def _verify_speaker(model, ref_embedding, audio_buf):
        """Cosine similarity tra l'audio corrente e l'impronta di riferimento."""
        import torch
        audio_f = audio_buf.astype(np.float32) / 32768.0
        waveform = torch.from_numpy(audio_f).unsqueeze(0)
        emb = model.encode_batch(waveform).squeeze().cpu().numpy()
        cos = np.dot(emb, ref_embedding) / (
            np.linalg.norm(emb) * np.linalg.norm(ref_embedding) + 1e-8
        )
        return float(cos)
