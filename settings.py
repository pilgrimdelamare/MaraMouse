"""Pannello impostazioni MaraMouse.

Mostra un dialog tkinter per configurare webcam, microfono e uscita TV.
Le scelte vengono salvate in settings.json e caricate all'avvio.
"""

import json
import os
import tkinter as tk
from tkinter import ttk

import config

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

# Chiavi nel JSON -> attributi di config
_KEY_MAP = {
    "camera_source": "CAMERA_SOURCE",
    "mic_device": "MIC_DEVICE",
    "loopback_device": "LOOPBACK_DEVICE",
}


def load_and_apply():
    """Carica settings.json e sovrascrive i valori in config."""
    if not os.path.exists(_SETTINGS_FILE):
        return
    try:
        with open(_SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    for json_key, config_attr in _KEY_MAP.items():
        if json_key in data:
            setattr(config, config_attr, data[json_key])


def _save(data):
    """Salva il dict in settings.json."""
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _enumerate_cameras():
    """Prova ad aprire webcam 0-9, ritorna lista di indici funzionanti."""
    import cv2
    cameras = []
    for i in range(10):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            cap.release()
            if ret:
                cameras.append(i)
        else:
            cap.release()
    return cameras


def _enumerate_audio():
    """Ritorna (input_devices, output_devices) da sounddevice.

    Filtra duplicati mostrando solo i dispositivi WASAPI (su Windows)
    o tutti se WASAPI non e' disponibile.
    """
    try:
        import sounddevice as sd
    except ImportError:
        return [], []

    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    # Trova indice WASAPI
    wasapi_idx = None
    for i, api in enumerate(hostapis):
        if "wasapi" in api["name"].lower():
            wasapi_idx = i

    inputs = []
    outputs = []
    for i, dev in enumerate(devices):
        # Preferisci WASAPI se disponibile, altrimenti mostra tutto
        if wasapi_idx is not None and dev["hostapi"] != wasapi_idx:
            continue
        name = dev["name"].strip()
        if dev["max_input_channels"] > 0:
            inputs.append((i, name))
        if dev["max_output_channels"] > 0:
            outputs.append((i, name))

    # Se WASAPI non ha trovato nulla, mostra tutti
    if not inputs and not outputs:
        for i, dev in enumerate(devices):
            name = dev["name"].strip()
            api = hostapis[dev["hostapi"]]["name"]
            label = f"{name} [{api}]"
            if dev["max_input_channels"] > 0:
                inputs.append((i, label))
            if dev["max_output_channels"] > 0:
                outputs.append((i, label))

    return inputs, outputs


def open_dialog():
    """Apre il pannello impostazioni. Blocca finche' l'utente non chiude."""
    # Carica impostazioni correnti
    current = {}
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            pass

    # Enumera dispositivi
    cameras = _enumerate_cameras()
    mic_devices, out_devices = _enumerate_audio()

    # --- Tkinter UI ---
    root = tk.Tk()
    root.title("MaraMouse — Impostazioni")
    root.resizable(False, False)

    # Stile
    style = ttk.Style()
    style.configure("TLabel", padding=5)
    style.configure("TButton", padding=5)

    main_frame = ttk.Frame(root, padding=15)
    main_frame.grid(row=0, column=0, sticky="nsew")

    row = 0

    # === WEBCAM ===
    ttk.Label(main_frame, text="Webcam", font=("", 10, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 5))
    row += 1

    cam_labels = [f"Camera {i}" for i in cameras] if cameras else ["Nessuna trovata"]
    cam_values = cameras if cameras else [0]
    cam_var = tk.StringVar()
    cam_combo = ttk.Combobox(main_frame, textvariable=cam_var, state="readonly",
                             values=cam_labels, width=45)
    cam_combo.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
    # Seleziona corrente
    cur_cam = current.get("camera_source", config.CAMERA_SOURCE)
    if cur_cam in cam_values:
        cam_combo.current(cam_values.index(cur_cam))
    elif cam_values:
        cam_combo.current(0)
    row += 1

    # === MICROFONO ===
    ttk.Separator(main_frame, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=10)
    row += 1
    ttk.Label(main_frame, text="Microfono", font=("", 10, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 5))
    row += 1

    mic_labels = ["Predefinito di sistema"] + [f"[{i}] {n}" for i, n in mic_devices]
    mic_indices = [None] + [i for i, _ in mic_devices]
    mic_var = tk.StringVar()
    mic_combo = ttk.Combobox(main_frame, textvariable=mic_var, state="readonly",
                             values=mic_labels, width=45)
    mic_combo.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
    cur_mic = current.get("mic_device", config.MIC_DEVICE)
    if cur_mic in mic_indices:
        mic_combo.current(mic_indices.index(cur_mic))
    else:
        mic_combo.current(0)
    row += 1

    # === USCITA TV (loopback echo cancellation) ===
    ttk.Separator(main_frame, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=10)
    row += 1
    ttk.Label(main_frame, text="Uscita TV / HDMI (echo cancellation)",
              font=("", 10, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 5))
    row += 1

    # Per il loopback: enumera speaker via soundcard (supporta WASAPI loopback)
    lb_labels = ["Disabilitato"]
    lb_names = [None]  # salva il nome dello speaker (stringa) per soundcard
    try:
        import soundcard as sc
        for spk in sc.all_speakers():
            lb_labels.append(spk.name)
            lb_names.append(spk.name)
    except ImportError:
        pass  # soundcard non installato, dropdown vuoto

    lb_var = tk.StringVar()
    lb_combo = ttk.Combobox(main_frame, textvariable=lb_var, state="readonly",
                            values=lb_labels, width=45)
    lb_combo.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
    cur_lb = current.get("loopback_device", config.LOOPBACK_DEVICE)
    if cur_lb in lb_names:
        lb_combo.current(lb_names.index(cur_lb))
    else:
        lb_combo.current(0)
    row += 1

    ttk.Label(main_frame, text="Seleziona l'uscita HDMI della TV per rimuovere\n"
              "l'audio della TV dal microfono (riduce falsi positivi).",
              foreground="gray").grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(2, 5))
    row += 1

    # === IMPRONTA VOCALE (opzionale) ===
    ttk.Separator(main_frame, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=10)
    row += 1

    _emb_path = os.path.join(os.path.dirname(__file__), "models",
                             config.SPEAKER_EMBEDDING)
    _has_emb = os.path.exists(_emb_path)
    voice_status = "Registrata" if _has_emb else "Non registrata"
    voice_color = "green" if _has_emb else "gray"

    ttk.Label(main_frame, text="Impronta vocale (facoltativo)",
              font=("", 10, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 5))
    row += 1

    voice_lbl = ttk.Label(main_frame, text=f"Stato: {voice_status}",
                          foreground=voice_color)
    voice_lbl.grid(row=row, column=0, sticky="w", pady=2)
    row += 1

    ttk.Label(main_frame, text="Registra la tua voce per riconoscere chi parla.\n"
              "Richiede speechbrain+torch (~2GB). Facoltativo.",
              foreground="gray").grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 5))
    row += 1

    enroll_progress = ttk.Label(main_frame, text="", foreground="blue")
    enroll_progress.grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    def _do_enroll():
        """Registra impronta vocale in background (5 campioni da 3s)."""
        import threading

        def _run():
            try:
                import sounddevice as sd
            except ImportError:
                enroll_progress.config(text="Errore: pip install sounddevice",
                                       foreground="red")
                return
            try:
                import torch  # noqa: F401
                try:
                    from speechbrain.inference.speaker import EncoderClassifier
                except ImportError:
                    from speechbrain.pretrained import EncoderClassifier
            except ImportError:
                enroll_progress.config(
                    text="Errore: pip install speechbrain torch",
                    foreground="red")
                return

            import numpy as np
            models_dir = os.path.join(os.path.dirname(__file__), "models")
            os.makedirs(models_dir, exist_ok=True)

            enroll_progress.config(text="Caricamento modello...",
                                   foreground="blue")
            try:
                model = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir=os.path.join(models_dir, "spkrec-ecapa"),
                )
            except Exception as e:
                enroll_progress.config(text=f"Errore modello: {e}",
                                       foreground="red")
                return

            sr = 16000
            rec_secs = 3
            n_samples = 5
            embeddings = []

            for i in range(n_samples):
                enroll_progress.config(
                    text=f"[{i+1}/{n_samples}] Di' 'Maramouse' ora! "
                         f"({rec_secs}s)",
                    foreground="blue")
                # Piccola pausa per leggere
                import time
                time.sleep(0.5)

                audio = sd.rec(int(sr * rec_secs), samplerate=sr,
                               channels=1, dtype="float32")
                sd.wait()

                rms = np.sqrt(np.mean(audio ** 2))
                if rms < 0.005:
                    enroll_progress.config(
                        text=f"[{i+1}] Troppo basso (RMS={rms:.4f}), "
                             "riprova piu' forte",
                        foreground="orange")
                    time.sleep(1.5)
                    continue

                waveform = torch.from_numpy(audio.T)
                emb = model.encode_batch(waveform).squeeze().cpu().numpy()
                embeddings.append(emb)

            if len(embeddings) < 2:
                enroll_progress.config(
                    text="Troppi pochi campioni validi, riprova.",
                    foreground="red")
                return

            avg = np.mean(embeddings, axis=0)
            avg = avg / (np.linalg.norm(avg) + 1e-8)
            emb_path = os.path.join(models_dir, config.SPEAKER_EMBEDDING)
            np.save(emb_path, avg)

            enroll_progress.config(
                text=f"Fatto! {len(embeddings)} campioni salvati. "
                     "Riavvia MaraMouse.",
                foreground="green")
            voice_lbl.config(text="Stato: Registrata", foreground="green")

        threading.Thread(target=_run, daemon=True).start()

    ttk.Button(main_frame, text="Registra voce",
               command=_do_enroll).grid(
        row=row, column=0, sticky="w", pady=2)
    row += 1

    # === PULSANTI ===
    ttk.Separator(main_frame, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=10)
    row += 1

    saved_label = ttk.Label(main_frame, text="", foreground="green")
    saved_label.grid(row=row + 1, column=0, columnspan=2, sticky="w")

    def on_save():
        cam_idx = cam_combo.current()
        mic_idx = mic_combo.current()
        lb_idx = lb_combo.current()

        new_settings = {
            "camera_source": cam_values[cam_idx] if cam_idx >= 0 else 0,
            "mic_device": mic_indices[mic_idx] if mic_idx >= 0 else None,
            "loopback_device": lb_names[lb_idx] if lb_idx >= 0 else None,
        }
        _save(new_settings)

        # Applica subito al config in memoria
        for json_key, config_attr in _KEY_MAP.items():
            if json_key in new_settings:
                setattr(config, config_attr, new_settings[json_key])

        saved_label.config(text="Salvato! Riavvia MaraMouse per applicare.")

    btn_frame = ttk.Frame(main_frame)
    btn_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
    ttk.Button(btn_frame, text="Salva", command=on_save).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="Chiudi", command=root.destroy).pack(side="right", padx=5)

    # Centra la finestra
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"+{x}+{y}")

    root.lift()
    root.attributes("-topmost", True)
    root.mainloop()
