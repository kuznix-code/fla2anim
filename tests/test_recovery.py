"""Damaged-zip recovery and format sniffing (0.1.1)."""
import zipfile

import pytest

from fla2anim import xfl
from fla2anim.model import FlaError, UnsupportedFormatError

DOM = (
    '<DOMDocument width="100" height="50" frameRate="12"><timelines>'
    '<DOMTimeline name="Scene 1"><layers><DOMLayer name="L"><frames>'
    '<DOMFrame index="0" duration="1"><elements/></DOMFrame>'
    "</frames></DOMLayer></layers></DOMTimeline></timelines></DOMDocument>"
)


def _make(tmp_path):
    p = tmp_path / "ok.fla"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("DOMDocument.xml", DOM)
        z.writestr("LIBRARY/pad.bin", b"x" * 5000)
    return p


def test_intact_zip_still_loads(tmp_path):
    doc = xfl.load(_make(tmp_path))
    assert doc.width == 100 and doc.fps == 12
    assert not doc.warnings


def test_bad_central_directory_is_recovered(tmp_path):
    data = _make(tmp_path).read_bytes()
    bad = tmp_path / "bad_cd.fla"
    bad.write_bytes(data.replace(b"PK\x01\x02", b"XXXX"))
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(bad)  # reproduces the reported failure
    doc = xfl.load(bad)
    assert doc.width == 100
    assert any("recovered" in w for w in doc.warnings)


def test_truncated_zip_is_recovered(tmp_path):
    data = _make(tmp_path).read_bytes()
    bad = tmp_path / "trunc.fla"
    bad.write_bytes(data[: data.find(b"PK\x01\x02")])  # no central dir, no EOCD
    assert xfl.load(bad).height == 50


def test_unrecoverable_zip_gives_clean_error(tmp_path):
    bad = tmp_path / "junk.fla"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    with pytest.raises(FlaError):
        xfl.load(bad)


def test_legacy_binary_fla_rejected(tmp_path):
    p = tmp_path / "old.fla"
    p.write_bytes(xfl.OLE_MAGIC + b"\x00" * 512)
    with pytest.raises(UnsupportedFormatError):
        xfl.load(p)
