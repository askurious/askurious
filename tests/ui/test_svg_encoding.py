"""SVG logos only in UTF-8 or an encoding that writes ASCII as ASCII (security and code audit 2026-10-13, IN-8): in
UTF-16 the check for DOCTYPE and entities on the bytes saw nothing, and the parser read them all the same."""
import pytest

from app import svg_safe

SVG = ('<?xml version="1.0" encoding="{enc}"?><!DOCTYPE svg [<!ENTITY a "b">]>'
       '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>')
PLAIN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


@pytest.mark.parametrize("data", [SVG.format(enc="UTF-16").encode("utf-16"), SVG.format(enc="UTF-16").encode("utf-16-le"),
                                  SVG.format(enc="UTF-32").encode("utf-32"), PLAIN.encode("utf-16"),
                                  ('<?xml version="1.0" encoding="UTF-7"?>' + PLAIN).encode()])
def test_other_encodings_are_refused(data):
    with pytest.raises(svg_safe.Refused):
        svg_safe.clean(data)


def test_utf8_and_latin1_still_work():
    assert svg_safe.clean(PLAIN.encode())[1:] == (10.0, 10.0)
    assert svg_safe.clean(b"\xef\xbb\xbf" + PLAIN.encode())[1:] == (10.0, 10.0)
    assert svg_safe.clean(('<?xml version="1.0" encoding="ISO-8859-1"?>' + PLAIN).encode("latin-1"))[1:] == (10.0, 10.0)
