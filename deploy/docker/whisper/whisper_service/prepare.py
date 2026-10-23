"""Explicit online preparation, never invoked by the runtime service."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

COMMUNITY_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
COMMUNITY_SHA256 = {
    "config.yaml": "5ce2bfa9a938dc132cec1172592d65173cbb8f444ea1e4133f10f9391de155be",
    "embedding/pytorch_model.bin": "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929",
    "plda/plda.npz": "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255",
    "plda/xvec_transform.npz": "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f",
    "segmentation/pytorch_model.bin": "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e",
}


# Models under a free licence only (copyright audit 2026-10-16, U2/U20): WhisperX's own default for German,
# VOXPOPULI_ASR_BASE_10K_DE, is CC BY-NC 4.0, so German aligns with this Apache 2.0 model at a fixed revision;
# English keeps WhisperX's default WAV2VEC2_ASR_BASE_960H (MIT). Silero VAD at its release v6.2.3 (MIT).
ALIGN_MODELS = {"de": ("jonatasgrosman/wav2vec2-large-xlsr-53-german", "4b8a02957378d0f2da2ef74091156b032c485a89")}
ALIGN_FILES = ["config.json", "preprocessor_config.json", "pytorch_model.bin", "special_tokens_map.json", "vocab.json",
               "README.md"]
SILERO_REVISION = "5cd7945676eb32225748052e2e6a0580e4686a08"
LICENCES = {"small": "MIT", "large-v3-turbo": "MIT", "alignment/de": "Apache-2.0", "alignment/en": "MIT",
            "vad": "MIT", "pyannote": "CC-BY-4.0 (pyannoteAI)",
            # Punkt sentence models by Jan Strunk (Kiss & Strunk, 2006); the package names no licence of its own, the
            # nltk_data repository it comes from is under Apache 2.0 (checked 2026-10-22)
            "nltk/punkt_tab": "Apache-2.0 (nltk_data repository)"}


def verify_community(folder):
    for name, expected in COMMUNITY_SHA256.items():
        with (folder / name).open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"Unexpected Community-1 asset: {name}; expected revision {COMMUNITY_REVISION}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyannote-dir", type=Path, required=True, help="Previously downloaded, authorised Community-1 files")
    parser.add_argument("--destination", type=Path, default=Path("/models"))
    args = parser.parse_args()
    verify_community(args.pyannote_dir)
    root = args.destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ[name] = "0"
    os.environ.update(HF_HUB_DISABLE_XET="1", HF_HUB_DISABLE_TELEMETRY="1", PYANNOTE_METRICS_ENABLED="0",
                      TORCH_HOME=str(root / "torch"), NLTK_DATA=str(root / "nltk"))
    import nltk
    import torch
    import whisperx
    import yaml
    from faster_whisper.utils import download_model
    from huggingface_hub import snapshot_download

    torch.set_num_threads(4)
    models = {}
    revisions = {"small": "536b0662742c02347bc0e980a01041f333bce120",
                 "large-v3-turbo": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"}
    for name, revision in revisions.items():
        download_model(name, output_dir=str(root / name), revision=revision)
        models[name] = name
    if not nltk.download("punkt_tab", download_dir=str(root / "nltk"), quiet=True, raise_on_error=True):
        raise RuntimeError("Sentence data download failed")
    revision = SILERO_REVISION
    torch.hub.load(f"snakers4/silero-vad:{revision}", "silero_vad", trust_repo=True, skip_validation=True, onnx=False)
    vad = f"torch/hub/snakers4_silero-vad_{revision}"
    alignment = {}
    for language in ("de", "en"):
        name = None
        if language in ALIGN_MODELS:
            repo, align_revision = ALIGN_MODELS[language]
            folder = root / "alignment" / language
            snapshot_download(repo, revision=align_revision, local_dir=str(folder), allow_patterns=ALIGN_FILES)
            name = alignment[language] = str(folder.relative_to(root))
        model, _ = whisperx.load_align_model(language, "cpu", model_name=str(root / name) if name else None,
                                             model_dir=str(root / "alignment"))
        del model
    for name in ("config.yaml", "embedding/pytorch_model.bin", "segmentation/pytorch_model.bin",
                 "plda/plda.npz", "plda/xvec_transform.npz"):
        source = args.pyannote_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"Missing authorised Community-1 file: {name}")
        destination = root / "pyannote" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    config = yaml.safe_load((root / "pyannote" / "config.yaml").read_text())
    if config["pipeline"]["name"] != "pyannote.audio.pipelines.SpeakerDiarization":
        raise ValueError("Expected the Community-1 diarization pipeline")
    config["pipeline"]["params"].update(segmentation_batch_size=1, embedding_batch_size=1)
    (root / "pyannote" / "config-runtime.yaml").write_text(yaml.safe_dump(config))
    # Hash material to make the downloaded assets independently reviewable before activation.
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json" and ".cache" not in path.parts:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            hashes[str(path.relative_to(root))] = digest.hexdigest()
    manifest = {"models": models, "revisions": revisions, "vad": vad, "vad_revision": revision,
                "languages": ["de", "en"], "sha256": hashes, "community_revision": COMMUNITY_REVISION,
                "alignment": alignment, "alignment_revisions": {k: v[1] for k, v in ALIGN_MODELS.items()},
                "licences": LICENCES}
    temporary = root / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2))
    temporary.replace(root / "manifest.json")
    print("Prepared local models for German and English; no Hugging Face token stored.")


if __name__ == "__main__":
    main()
