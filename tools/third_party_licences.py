"""Write app/data/THIRD_PARTY_LICENSES.txt: every Python package the lock files install, with its version and licence.

    python tools/third_party_licences.py

Reads the lock files (requirements.lock, deploy/docker/requirements-docker.lock, for building the images
deploy/docker/requirements-static.lock and, for the transcription service, deploy/docker/whisper/requirements*.txt)
and asks PyPI for the licence of each locked version (its
license_expression, else its license field, else its classifiers). Needs the network; run it again after changing a
lock file (tests/platform/test_sources_page.py, test_third_party_list_has_every_locked_package, fails while a locked
package is missing from the file). Notes on the C libraries inside wheels go into HEAD below.
The page /sources links the file (copyright audit 2026-10-16, U21). The licence texts themselves come with most
packages (HEAD names the exceptions); the platform passes none of them on (it runs as a web service), see the note at the top of the file.
"""
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "data", "THIRD_PARTY_LICENSES.txt")
GROUPS = (
    ("Web-App / web app", ("requirements.lock", "deploy/docker/requirements-docker.lock")),
    ("Transkriptionsdienst, falls eingerichtet / transcription service, if set up",
     ("deploy/docker/whisper/requirements.txt", "deploy/docker/whisper/requirements-cpu.txt")),
    ("Nur beim Bauen der Images / only when building the images", ("deploy/docker/requirements-static.lock",)),
)
# packages whose PyPI metadata names no licence: the licence file of their repository (checked 2026-10-16)
KNOWN = {"pyannote-audio": "MIT", "pyannote-core": "MIT", "pyannote-database": "MIT", "pyannote-metrics": "MIT",
         "pyannote-pipeline": "MIT", "pyannoteai-sdk": "MIT", "fsspec": "BSD-3-Clause", "torchcodec": "BSD-3-Clause"}
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.M)

HEAD = """Third-party software of Askurious
==================================

The Python packages the lock files of the repository install, with version and licence as PyPI states them
(written by tools/third_party_licences.py; do not edit by hand). Most wheels bring their licence texts;
ctranslate2 4.8.3, flatbuffers 25.12.19, tokenizers 0.22.2 and primepy 1.3 ship no licence file; the texts of their
repositories are in deploy/docker/whisper/licenses/ and in the image of the transcription service.
Askurious runs them as a web service and passes none of them on; whoever passes on an image or a copy of the
installed packages adds their licence texts and NOTICE files.

Not listed below, also part of a server: CPython (PSF License), the Docker base images python:3.12-slim (Debian),
nginx:alpine, postgres:17 and certbot with their system packages, and the C libraries inside the wheels (e.g.
OpenBLAS in NumPy, libjpeg/libpng/FreeType in Pillow, OpenSSL in cryptography, libpq in psycopg-binary, libxml2 in
lxml, RE2/Abseil in google-re2), each under its own licence.
FreeType, inside the wheels of Pillow and matplotlib: Portions of this software are copyright © The FreeType
Project (www.freetype.org). All rights reserved.
In the browser: Leaflet, Leaflet.heat with simpleheat, PDF.js with OpenJPEG and core-js, the font Atkinson
Hyperlegible Next and the icons, see /sources. The PDF.js bundles include core-js 3.39.0 (MIT, Copyright (c) 2014-2024 Denis Pushkarev).
Its unchanged licence is app/static/vendor/pdfjs/LICENSE_CORE_JS, from
https://raw.githubusercontent.com/zloirock/core-js/v3.39.0/LICENSE.
Icons (app/templates/_icons.html, the sun and moon in base.html, the restart arrow in live/screen.html): all drawn
for Askurious. The ORCID iD icons
(app/static/img/orcid) are the official files of the ORCID brand library, used as the ORCID Brand Guidelines allow;
ORCID and the iD logo are trademarks of ORCID, Inc.
Data (maps, postcodes, federal states, IP ranges, population figures, handwritten digits, the password block
list): source and licence of each file in app/data/README.md and on /sources.

Transcription service, if set up (deploy/docker/whisper): models loaded once by prepare.py, each under its own
licence: Whisper weights (Systran/faster-whisper-small, mobiuslabsgmbh/faster-whisper-large-v3-turbo) MIT;
pyannote/speaker-diarization-community-1 CC BY 4.0 (pyannoteAI); alignment models wav2vec2 WAV2VEC2_ASR_BASE_960H
(English) MIT and jonatasgrosman/wav2vec2-large-xlsr-53-german (German) Apache 2.0; Silero VAD MIT; the NLTK sentence
data punkt_tab (Jan Strunk) Apache 2.0, the licence of the nltk_data repository (the package names none of its
own). ffmpeg from Debian (GPL build) runs as a separate program.
Inside the wheels of the transcription service: av 19.0.1 bundles FFmpeg built with libx264 and libx265
(GPL-2.0-or-later); ctranslate2 4.8.3 (CTranslate2 itself MIT) links Intel oneMKL (Intel Simplified Software
License) and oneDNN (Apache-2.0) statically and ships no licence file for them; triton 3.8.0 contains NVIDIA tools
(ptxas, cuobjdump, nvdisasm, CUPTI) under NVIDIA's licence terms.
Works inside other wheels, each under its own licence, not only that of the package: setuptools 84.1.0
vendors autocommand 2.2.2 (LGPL-3.0) and parts under MPL-2.0; pyannote-audio 4.0.7 contains code from WeSpeaker
(Apache-2.0).
"""



def pins(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return [(m.group(1).lower().replace("_", "-"), m.group(2)) for m in PIN.finditer(f.read())]


def licence(name, version):
    if name in KNOWN:
        return KNOWN[name] + " (repository)"
    public = version.split("+")[0]  # torch==2.8.0+cpu: the licence of its public release
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{public}/json", timeout=30) as r:
        info = json.load(r)["info"]
    expr = (info.get("license_expression") or "").strip()
    if expr:
        return expr
    text = (info.get("license") or "").strip()
    if text and len(text) <= 80 and "\n" not in text:
        return text
    found = [c.split(" :: ")[-1] for c in info.get("classifiers") or () if c.startswith("License :: ")]
    return "; ".join(found) or "see the package"


def main():
    lines = [HEAD]
    for title, files in GROUPS:
        lines.append(f"\n{title}\n{'-' * len(title)}\n")
        seen = {}
        for path in files:
            for name, version in pins(path):
                seen.setdefault(name, version)
        for name in sorted(seen):
            lines.append(f"{name}=={seen[name]}  {licence(name, seen[name])}\n")
            print(name, file=sys.stderr)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))


if __name__ == "__main__":
    main()
