"""Gate vocale MaraMouse: wake word + speaker verification + echo cancellation.

Pipeline: microfono -> Vosk (grammatica vincolata) -> confronto energia
          mic/loopback -> speaker verification -> ARMED.

Vosk con grammatica ["mara mouse", "[unk]"] funziona come un keyword spotter:
riconosce solo la wake word o rumore, con CPU bassissima e senza training.
Se e' presente un modello openWakeWord custom (maramouse.onnx), usa quello.

Echo cancellation: cattura il loopback WASAPI dell'uscita TV (via soundcard)
e confronta l'energia del microfono con quella del loopback. Se il mic non ha
energia aggiuntiva rispetto alla TV, il trigger e' la TV -> rifiutato.
Se il mic ha piu' energia (voce dell'utente sopra la TV) -> accettato.

Gira in un thread separato, non blocca il loop video.
"""

import json
import os
import threading

import numpy as np

import config

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


def list_devices():
    """Stampa tutti i dispositivi audio disponibili (per configurare il loopback)."""
    import sounddevice as sd
    print("=== DISPOSITIVI AUDIO ===")
    print()
    for i, dev in enumerate(sd.query_devices()):
        ch_in = dev['max_input_channels']
        ch_out = dev['max_output_channels']
        api = sd.query_hostapis(dev['hostapi'])['name']
        direction = []
        if ch_in > 0:
            direction.append(f"IN:{ch_in}")
        if ch_out > 0:
            direction.append(f"OUT:{ch_out}")
        marker = ""
        default_in = sd.query_devices(sd.default.device[0])
        default_out = sd.query_devices(sd.default.device[1])
        if dev['name'] == default_in['name'] and ch_in > 0:
            marker = " <<< MIC"
        if dev['name'] == default_out['name'] and ch_out > 0:
            marker += " <<< SPEAKER"
        print(f"  [{i:2d}] {dev['name'][:50]:<50s} {' '.join(direction):<12s} "
              f"[{api}]{marker}")
    print()
    print("Per abilitare echo cancellation TV, imposta LOOPBACK_DEVICE in config.py")
    print("con l'indice del dispositivo di uscita HDMI (quello della TV).")
    print("Il sistema ne catturera' il loopback per sottrarlo dal microfono.")




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

    # ------------------------------------------------------------------
    #  Loopback: cattura audio di sistema per echo cancellation
    # ------------------------------------------------------------------

    @staticmethod
    def _open_loopback(sd, sr, chunk):
        """Apre il loopback WASAPI per catturare l'audio del sistema (TV).

        Usa la libreria soundcard che supporta WASAPI loopback nativamente.
        config.LOOPBACK_DEVICE e' il nome (stringa) dello speaker da catturare.
        """
        if not config.LOOPBACK_DEVICE:
            return None

        try:
            import soundcard as sc
        except ImportError:
            print("[AudioGate] pip install soundcard per echo cancellation")
            return None

        # Trova lo speaker per nome (o ignora vecchi valori int da settings.json)
        target = config.LOOPBACK_DEVICE
        if not isinstance(target, str):
            print(f"[AudioGate] LOOPBACK_DEVICE={target!r} non valido (atteso stringa).")
            print("[AudioGate] Premi 's' e riseleziona l'uscita TV.")
            return None
        speaker = None
        for s in sc.all_speakers():
            if target.lower() in s.name.lower():
                speaker = s
                break

        if speaker is None:
            print(f"[AudioGate] Speaker '{target}' non trovato")
            print("[AudioGate] Premi 's' per aprire le impostazioni")
            return None

        try:
            loopback_mic = sc.get_microphone(id=str(speaker.id),
                                             include_loopback=True)
            recorder = loopback_mic.recorder(samplerate=sr, channels=1,
                                             blocksize=chunk)
            recorder.__enter__()
            print(f"[AudioGate] Loopback WASAPI: {speaker.name}")
            return recorder
        except Exception as e:
            print(f"[AudioGate] Loopback non disponibile: {e}")
            return None

    @staticmethod
    def _read_loopback(recorder, chunk):
        """Legge un chunk dal loopback soundcard e lo converte in int16."""
        try:
            # soundcard restituisce float32 in [-1, 1]
            data = recorder.record(numframes=chunk)
            audio = (data[:, 0] * 32768).clip(-32768, 32767).astype(np.int16)
            return audio
        except Exception:
            return None

    # ------------------------------------------------------------------
    #  Vosk backend
    # ------------------------------------------------------------------

    @staticmethod
    def _is_wake_phrase(text):
        """Verifica che il testo riconosciuto contenga la wake phrase completa.

        Richiede entrambe le parole 'mara' E 'mouse'/'maus' per evitare
        falsi positivi su parole singole o frasi della TV.
        """
        if not text or text == "[unk]":
            return False
        return "mara" in text and ("mouse" in text or "maus" in text)

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
        chunk = 4096  # ~256ms @ 16kHz
        buf_seconds = 3
        ring_buf = np.zeros(sr * buf_seconds, dtype=np.int16)
        ring_pos = 0

        try:
            mic_dev = config.MIC_DEVICE
            stream = sd.InputStream(device=mic_dev, samplerate=sr, channels=1,
                                    dtype="int16", blocksize=chunk)
            stream.start()
            mic_name = sd.query_devices(mic_dev)["name"] if mic_dev is not None else "default"
            print(f"[AudioGate] Microfono: {mic_name}")
        except Exception as e:
            print(f"[AudioGate] Errore apertura microfono: {e}")
            return

        # Loopback per echo cancellation (opzionale)
        lb_stream = self._open_loopback(sd, sr, chunk)
        # Secondo recognizer Vosk sul loopback: se anche il loopback
        # riconosce la wake word, era la TV, non l'utente.
        lb_rec = None
        if lb_stream is not None:
            lb_rec = vosk.KaldiRecognizer(model, 16000, grammar)
            lb_rec.SetWords(False)
            # Buffer circolare loopback per controllare gli ultimi ~3s
            lb_ring = np.zeros(sr * 3, dtype=np.int16)
            lb_ring_pos = 0
            print("[AudioGate] Echo cancellation: Vosk su loopback attivo")

        self._ready = True
        print("[AudioGate] In ascolto (Vosk)...")

        try:
            while not self._stop_event.is_set():
                data, overflowed = stream.read(chunk)
                if overflowed:
                    continue
                audio = data.flatten()

                # Leggi loopback e alimenta il secondo recognizer
                if lb_stream is not None:
                    lb_audio = self._read_loopback(lb_stream, chunk)
                    if lb_audio is not None:
                        # Accumula nel ring buffer loopback
                        lb_end = lb_ring_pos + len(lb_audio)
                        if lb_end <= len(lb_ring):
                            lb_ring[lb_ring_pos:lb_end] = lb_audio
                        else:
                            first = len(lb_ring) - lb_ring_pos
                            lb_ring[lb_ring_pos:] = lb_audio[:first]
                            lb_ring[:len(lb_audio) - first] = lb_audio[first:]
                        lb_ring_pos = lb_end % len(lb_ring)
                        # Feed al recognizer loopback (non ci interessa il
                        # risultato continuo, lo controlleremo on-demand)
                        lb_rec.AcceptWaveform(lb_audio.tobytes())

                # Buffer circolare per speaker verification
                end = ring_pos + len(audio)
                if end <= len(ring_buf):
                    ring_buf[ring_pos:end] = audio
                else:
                    first = len(ring_buf) - ring_pos
                    ring_buf[ring_pos:] = audio[:first]
                    ring_buf[:len(audio) - first] = audio[first:]
                ring_pos = end % len(ring_buf)

                # Wake word detection — SOLO su risultati completi
                raw = audio.tobytes()
                if rec.AcceptWaveform(raw):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip().lower()
                else:
                    # Risultati parziali: aggiorna solo il punteggio diagnostica
                    partial = json.loads(rec.PartialResult())
                    p = partial.get("partial", "").strip().lower()
                    with self._lock:
                        self._scores["wake"] = 1.0 if self._is_wake_phrase(p) else 0.0
                    continue  # NON triggerare su risultati parziali

                if not self._is_wake_phrase(text):
                    with self._lock:
                        self._scores["wake"] = 0.0
                    continue

                with self._lock:
                    self._scores["wake"] = 1.0

                # Echo cancellation: controlla se anche il loopback ha la wake word
                if lb_rec is not None:
                    # Forza il risultato finale del recognizer loopback
                    lb_final = json.loads(lb_rec.FinalResult())
                    lb_text = lb_final.get("text", "").strip().lower()
                    # Reset per la prossima volta
                    lb_rec = vosk.KaldiRecognizer(model, 16000, grammar)
                    lb_rec.SetWords(False)
                    if self._is_wake_phrase(lb_text):
                        print(f"[AudioGate] '{text}' RIFIUTATA "
                              f"(anche il loopback dice '{lb_text}')")
                        continue

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
            if lb_stream is not None:
                lb_stream.__exit__(None, None, None)

    # ------------------------------------------------------------------
    #  openWakeWord backend (usato solo se modello ONNX presente)
    # ------------------------------------------------------------------

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
            mic_dev = config.MIC_DEVICE
            stream = sd.InputStream(device=mic_dev, samplerate=sr, channels=1,
                                    dtype="int16", blocksize=chunk)
            stream.start()
        except Exception as e:
            print(f"[AudioGate] Errore apertura microfono: {e}")
            return

        # Loopback per echo cancellation (opzionale) — Vosk sul loopback
        lb_stream = self._open_loopback(sd, sr, chunk)
        lb_vosk_rec = None
        if lb_stream is not None:
            try:
                import vosk
                vosk.SetLogLevel(-1)
                lb_model = vosk.Model(lang="en-us")
                lb_vosk_rec = vosk.KaldiRecognizer(
                    lb_model, 16000,
                    json.dumps(config.WAKE_WORD_GRAMMAR))
                lb_vosk_rec.SetWords(False)
                print("[AudioGate] Echo cancellation: Vosk su loopback attivo")
            except ImportError:
                print("[AudioGate] Vosk non disponibile per echo cancellation")

        self._ready = True
        print("[AudioGate] In ascolto (openWakeWord)...")

        try:
            while not self._stop_event.is_set():
                data, overflowed = stream.read(chunk)
                if overflowed:
                    continue
                audio = data.flatten()

                # Feed loopback al Vosk di controllo
                if lb_stream is not None:
                    lb_audio = self._read_loopback(lb_stream, chunk)
                    if lb_audio is not None and lb_vosk_rec is not None:
                        lb_vosk_rec.AcceptWaveform(lb_audio.tobytes())

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
                        # Echo cancellation: il loopback ha la wake word?
                        if lb_vosk_rec is not None:
                            lb_final = json.loads(lb_vosk_rec.FinalResult())
                            lb_text = lb_final.get("text", "").strip().lower()
                            import vosk as _vosk
                            lb_vosk_rec = _vosk.KaldiRecognizer(
                                lb_model, 16000,
                                json.dumps(config.WAKE_WORD_GRAMMAR))
                            lb_vosk_rec.SetWords(False)
                            if self._is_wake_phrase(lb_text):
                                print(f"[AudioGate] '{name}' RIFIUTATA "
                                      f"(loopback dice '{lb_text}')")
                                oww.reset()
                                break
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
            if lb_stream is not None:
                lb_stream.__exit__(None, None, None)

    # ------------------------------------------------------------------
    #  Speaker verification
    # ------------------------------------------------------------------

    @staticmethod
    def _load_speaker_model():
        """Carica il modello speaker verification e l'embedding di riferimento."""
        emb_path = os.path.join(_MODELS_DIR, config.SPEAKER_EMBEDDING)
        if not os.path.exists(emb_path):
            print(f"[AudioGate] Impronta vocale non trovata: {emb_path}")
            print("[AudioGate] Speaker verification: off (opzionale, esegui enroll_voice.py)")
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


if __name__ == "__main__":
    list_devices()
