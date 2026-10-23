"""Builds app/data/common_passwords.txt, the block list of app/passwords.py (audit 2026-10-11, AU4/N12).

Source: the frequency list "passwords.txt" of zxcvbn (Dropbox, MIT licence; the list itself goes back to Mark
Burnett's ten million passwords, a freely published list). Chrome and Edge ship it as "ZxcvbnData"; on
Windows e.g. %LOCALAPPDATA%/Google/Chrome/User Data/ZxcvbnData/3/passwords.txt. The list is ordered by frequency;
the first TOP entries are taken, each as passwords.normalize() writes it (lower case, digits and characters at the end
cut off), without duplicates and without empty stems.

    python tools/passwort_sperrliste.py <path to passwords.txt>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOP = 10000
HEAD = """# The 10,000 most frequent passwords as stems (lower case, digits and characters at the end cut off), for the
# block list of app/passwords.py (audit 2026-10-11, AU4). Built with tools/passwort_sperrliste.py from the frequency
# list of zxcvbn, (c) 2012-2016 Dan Wheeler and Dropbox, Inc., MIT licence (page /sources); the list itself
# goes back to Mark Burnett's ten million passwords, a freely published list (2015).
# The licence of zxcvbn (copyright audit 2026-10-16, U31):
#
# Copyright (c) 2012-2016 Dan Wheeler and Dropbox, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


def main(source):
    from app.passwords import normalize

    with open(source, encoding="utf-8") as f:
        words = [line.strip() for line in f if line.strip()][:TOP]
    out, seen = [], set()
    for word in words:
        stem = normalize(word)
        if stem and stem not in seen:
            seen.add(stem)
            out.append(stem)
    target = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "common_passwords.txt")
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEAD + "\n".join(out) + "\n")
    print(f"{len(out)} stems from {len(words)} passwords -> {target}")


if __name__ == "__main__":
    main(sys.argv[1])
