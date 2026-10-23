"""Read the participant core and its conditional modules for existing behavioural source checks."""
from pathlib import Path


def participant_js():
    folder = Path(__file__).resolve().parents[1] / "app" / "static"
    paths = [folder / "participant.js"] + sorted(folder.glob("participant-*.js"))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)
