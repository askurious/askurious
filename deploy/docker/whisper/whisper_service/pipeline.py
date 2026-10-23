"""Bounded resident ASR cache. Audio, prompts, alignment and diarization are per job."""
from dataclasses import is_dataclass, replace
import gc
import importlib.metadata
import json
import logging
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from .checkpoints import Checkpoint


class ModelCache:
    def __init__(self):
        self.key, self.model, self.unloaded = None, None, False

    def get(self, key, load):
        hit = self.model is not None and self.key == key
        if not hit:
            self.clear()
            self.model, self.key = load(), key
        return self.model, hit

    def clear(self):
        self.model, self.key, self.unloaded = None, None, False
        gc.collect()
        trim_memory()

    def load_weights(self):
        if self.unloaded:
            self.model.model.model.load_model()
            self.unloaded = False

    def unload_weights(self):
        self.model.model.model.unload_model(to_cpu=False)
        self.unloaded = True
        gc.collect()
        trim_memory()


def trim_memory():
    """Return free CPU allocator arenas after a stage, preserving only live model objects."""
    if sys.platform.startswith("linux"):
        import ctypes
        ctypes.CDLL(None).malloc_trim(0)


CACHE = ModelCache()
THREADS_READY = False


def with_prompt(options, prompt):
    return replace(options, initial_prompt=prompt or None) if is_dataclass(options) else options._replace(initial_prompt=prompt or None)


def progress(job, stage):
    if job.get("progress"):
        path = Path(job["progress"])
        temporary = path.with_suffix(".progress.tmp")
        temporary.write_text(json.dumps({"stage": stage, 'duration': job.get('duration'),
                                         'stage_started': time.time(), 'completed': job.get('completed', {})}))
        temporary.replace(path)


def split_speakers(segments):
    """Preserve speaker changes within an aligned sentence, not just its majority speaker."""
    out = []
    for segment in segments:
        words = segment.get("words") or []
        if not words:
            out.append(segment)
            continue
        current = None
        for word in words:
            speaker = word.get("speaker", segment.get("speaker"))
            start = word.get("start", segment.get("start", 0))
            end = word.get("end", segment.get("end", start))
            if current is None or current.get("speaker") != speaker:
                if current is not None:
                    out.append(current)
                current = {"start": start, "end": end, "text": "", "words": []}
                if speaker:
                    current["speaker"] = speaker
            current["end"] = end
            current["text"] = (current["text"] + " " + word.get("word", "")).strip()
            current["words"].append(word)
        if current is not None:
            out.append(current)
    return out


def safe_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    return value


def process(job, reuse=False):
    global THREADS_READY
    progress(job, "loading")
    import numpy as np
    import pandas as pd
    import torch
    import whisperx
    from pyannote.audio import Pipeline
    from whisperx.diarize import assign_word_speakers
    from whisperx.vads.silero import Silero
    from whisperx.vads.vad import Vad

    root = Path(job["models"])
    parameters = job["options"]
    threads = job["threads"]
    manifest = json.loads((root / "manifest.json").read_text())
    versions = {name: importlib.metadata.version(name) for name in ('whisperx', 'pyannote.audio', 'torch')}
    checkpoint = Checkpoint(job, manifest, versions)
    resumed = bool(checkpoint.completed)
    job['completed'] = dict(checkpoint.completed)
    logging.disable(logging.CRITICAL)
    torch.set_num_threads(threads)
    if not THREADS_READY:
        torch.set_num_interop_threads(1)
        THREADS_READY = True
    progress(job, "decoding")
    # Bound decompression before loading the model, including recordings without duration metadata. The input only
    # from a local file: no playlist or demuxer may reach another protocol (Abgleich 2026-10-15, NB-9/NB-10).
    decoded = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-protocol_whitelist", "file",
                              "-i", job["audio"],
                              "-t", str(job["max_seconds"] + 1), "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    audio = np.frombuffer(decoded.stdout, dtype="<i2").astype(np.float32) / 32768.0
    del decoded
    duration = len(audio) / 16000
    job['duration'] = duration
    if duration > job["max_seconds"]:
        raise SystemExit(3)
    if not len(audio):
        raise ValueError("empty_audio")

    class LocalSilero(Silero):
        def __init__(self):
            Vad.__init__(self, 0.5)
            self.vad_onset, self.chunk_size = 0.5, 30
            self.vad_pipeline, utilities = torch.hub.load(str(root / manifest["vad"]), "silero_vad",
                                                        source="local", onnx=False)
            self.get_speech_timestamps = utilities[0]

    result = checkpoint.result
    def load():
        return whisperx.load_model(str(root / manifest["models"][parameters["model"]]), "cpu", compute_type="int8",
                               language=parameters["language"], threads=threads, local_files_only=True,
                               vad_model=LocalSilero(), vad_method="silero",
                               asr_options={"initial_prompt": None})
    hit = False
    if 'transcribing' not in checkpoint.completed:
        progress(job, "loading")
        model, hit = CACHE.get((str(root), parameters["model"], parameters["language"], threads), load)
        CACHE.load_weights()
        # Silero changes the global thread count on import. Restore it for expensive stages.
        torch.set_num_threads(threads)
        progress(job, "transcribing")
        began = time.monotonic()
        baseline = model.options
        reset_vad = getattr(model.vad_model.vad_pipeline, "reset_states", lambda: None)
        try:
            reset_vad()
            model.options = with_prompt(baseline, parameters["prompt"])
            result = model.transcribe(audio, batch_size=1, language=parameters["language"])
        finally:
            model.options = baseline
            reset_vad()
        del model
        checkpoint.save('transcribing', safe_json(result), time.monotonic() - began)
        job['completed'] = dict(checkpoint.completed)
    if not reuse or resumed:
        CACHE.clear()
    elif parameters["model"] == "large-v3-turbo" and (parameters["align"] or parameters["diarize"]):
        # Large ASR weights must not overlap alignment/diarization under the 4 GiB cgroup.
        CACHE.unload_weights()
    gc.collect()
    if parameters["align"] and 'aligning' not in checkpoint.completed:
        progress(job, "aligning")
        began = time.monotonic()
        if result['segments']:
            # the model prepare.py stored for the language (German: a free model, not WhisperX's CC BY-NC default
            # VOXPOPULI_ASR_BASE_10K_DE); only English may use WhisperX's own default (MIT). Models prepared before
            # 2026-10-16 have no German entry: stop instead of falling back to the NC model (licence audit 2026-10-23, O6)
            local = (manifest.get("alignment") or {}).get(parameters["language"])
            if not local and parameters["language"] != "en":
                raise RuntimeError("No alignment model prepared for this language; run prepare.py again")
            align_model, metadata = whisperx.load_align_model(parameters["language"], "cpu",
                                                              model_name=str(root / local) if local else None,
                                                              model_dir=str(root / "alignment"))
            torch.set_num_threads(threads)
            result = whisperx.align(result["segments"], align_model, metadata, audio, "cpu", return_char_alignments=False)
            del align_model
        checkpoint.save('aligning', safe_json(result), time.monotonic() - began)
        job['completed'] = dict(checkpoint.completed)
        gc.collect()
        trim_memory()
    if parameters["diarize"] and 'diarizing' not in checkpoint.completed:
        progress(job, "diarizing")
        began = time.monotonic()
        if result['segments']:
            diarization = Pipeline.from_pretrained(str(root / "pyannote" / "config-runtime.yaml"))
            diarization.to(torch.device("cpu"))
            torch.set_num_threads(threads)
            annotation = diarization({"waveform": torch.from_numpy(audio).unsqueeze(0), "sample_rate": 16000}).speaker_diarization
            segments = [{"start": segment.start, "end": segment.end, "speaker": speaker}
                        for segment, _, speaker in annotation.itertracks(yield_label=True)]
            if segments:
                result = assign_word_speakers(pd.DataFrame(segments), result, fill_nearest=True)
                result["segments"] = split_speakers(result["segments"])
            del diarization, annotation
        checkpoint.save('diarizing', safe_json(result), time.monotonic() - began)
        job['completed'] = dict(checkpoint.completed)
        gc.collect()
        trim_memory()
    result.update(duration=duration, language=parameters["language"], model=parameters["model"],
                  cache_hit=hit,
                  text=" ".join(segment.get("text", "").strip() for segment in result["segments"]),
                  versions=versions)
    progress(job, "diarizing" if parameters['diarize'] else 'aligning' if parameters['align'] else 'transcribing')
    return safe_json(result)


def write_result(job, reuse=False):
    result = process(job, reuse=reuse)
    destination = Path(job["output"])
    temporary = destination.with_suffix(".result.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    temporary.replace(destination)


def main():
    os.umask(0o077)
    if sys.argv[1] != "--resident":
        try:
            write_result(json.loads(Path(sys.argv[1]).read_text()))
        except Exception as error:
            if memory_failure(error):
                raise SystemExit(4) from None
            raise
        return
    for line in sys.stdin:
        job = json.loads(Path(line.strip()).read_text())
        code = 0
        try:
            write_result(job, reuse=True)
        except SystemExit as error:
            code = 3 if error.code == 3 else 1
        except Exception as error:
            code = 4 if memory_failure(error) else 1
        if code:
            CACHE.clear()
        path = Path(job["complete"])
        temporary = path.with_suffix(".complete.tmp")
        temporary.write_text(json.dumps({"code": code}))
        temporary.replace(path)
        del job
        gc.collect()
        trim_memory()


def memory_failure(error):
    return isinstance(error, MemoryError) or any(text in str(error).lower() for text in
                                                ('out of memory', 'cannot allocate memory', "can't allocate memory"))


if __name__ == "__main__":
    main()
