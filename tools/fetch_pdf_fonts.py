"""Fetch the large PDF fonts (Arabic, Chinese) into a folder, each checked against its SHA-256.

    python tools/fetch_pdf_fonts.py /usr/share/fonts/askurious      # deploy/docker/Dockerfile does this

Noto Sans itself lies in the repository (app/data/fonts); these are too large for it (Noto Sans SC about 10 MB per
weight) and are only needed where a survey speaks Arabic or Chinese. app/pdf_fonts.py finds them in PDF_FONT_DIR
(default /usr/share/fonts/askurious); without them the characters stay "?" in the PDFs.

All under the SIL Open Font License 1.1; its text goes into the folder next to the fonts (OFL-*.txt).
Noto Sans Arabic: notofonts.github.io at a fixed commit (the release files of the Noto project). Noto Sans SC: the
static TrueType files Google Fonts serves (version v41); the Noto CJK release has only OpenType/CFF, which reportlab
cannot read. A changed file stops the build (a different SHA-256)."""
import hashlib
import os
import sys
import urllib.request

NOTO = "https://raw.githubusercontent.com/notofonts/notofonts.github.io/c1d3718e2a0eb25c2567a694ee017061fdaa5f51/fonts"
FILES = (
    ("NotoSansArabic-Regular.ttf", f"{NOTO}/NotoSansArabic/unhinted/ttf/NotoSansArabic-Regular.ttf",
     "bd86ca02f087d7f3c3788ba458fb6b73744c7639ed276b8d870dba6def6c40d0"),
    ("NotoSansArabic-Bold.ttf", f"{NOTO}/NotoSansArabic/unhinted/ttf/NotoSansArabic-Bold.ttf",
     "f4cb79f842a07d1bcf597879574358fb7010f73761396d1d0f2445dfedb4d2ca"),
    ("NotoSansSC-Regular.ttf", "https://fonts.gstatic.com/s/notosanssc/v41/k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaG9_FnYw.ttf",
     "450625c8d46ab3df97b7904ded955ec2746d17ec76740cb1e91d1ba63a0f89af"),
    ("NotoSansSC-Bold.ttf", "https://fonts.gstatic.com/s/notosanssc/v41/k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaGzjCnYw.ttf",
     "0066a522a1ac007c1d72bc4fccb114f80ff7294641c78cead9715bd14d43b9ea"),
    ("OFL-NotoSansArabic.txt", "https://raw.githubusercontent.com/notofonts/arabic/"
     "f1ba474be91d6a5d2cfcc4374bb03cc5f1011088/OFL.txt", None),
    ("OFL-NotoSansSC.txt", "https://raw.githubusercontent.com/google/fonts/"
     "6157dfaa640ca5738b4d1a7070fe389c3036d7fc/ofl/notosanssc/OFL.txt", None),
)


def fetch(folder):
    os.makedirs(folder, exist_ok=True)
    for name, url, digest in FILES:
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - fixed https addresses above
            data = r.read()
        if digest and hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f"{name}: SHA-256 differs from the one fixed in tools/fetch_pdf_fonts.py")
        if not digest and b"SIL Open Font License" not in data:
            raise SystemExit(f"{name}: not the text of the SIL Open Font License")
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        print(f"{name}: {len(data) // 1024} KB")


if __name__ == "__main__":
    fetch(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/askurious")
