import datetime as dt

import pytest

from hyperion.config import Settings
from hyperion.ingestion import IngestionError
from hyperion import lonab_auto as la

PAGE1 = """<table><tr class="odd"><td class="views-field-title">journal hippique PMU'B du 25 septembre 2026</td>
<td><a href="/sites/default/files/j25.pdf">Télécharger</a></td></tr>
<tr><td class="views-field-title">journal hippique PMU'B du 24 septembre 2026</td>
<td><a href="/sites/default/files/j24.pdf">Télécharger</a></td></tr></table>
<a rel="next" href="/fr/programme-pmub?page=1">suivant</a>"""
PAGE2 = """<table><tr><td class="views-field-title">journal hippique PMU'B du 20 septembre 2026</td>
<td><a href="/sites/default/files/j20.pdf">Télécharger</a></td></tr></table>"""

TEXT = """Prix de la Basse Automne - Compiègne - Plat
 1 NATIONAL STAR      M. GUYON   15 000     4.5/1
 2 MAJOR OAK          C. DEMURO  8 200      12/1
 3 SWEET CHOP         H. BOUTIN  0          --
"""


class Resp:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content
    def raise_for_status(self):
        pass


class Sess:
    def __init__(self):
        self.calls = []
    def get(self, url, timeout=0):
        self.calls.append(url)
        if url.endswith("programme-pmub"):
            return Resp(PAGE1)
        if url.endswith("page=1"):
            return Resp(PAGE2)
        return Resp(content=b"%PDF")


def test_date_exacte_pas_premier_pdf():
    link = la.find_journal_link(PAGE1, dt.date(2026, 9, 24))
    assert link.pdf_url.endswith("j24.pdf")


def test_suit_la_pagination():
    prov = la.LonabAutoProvider(Settings(), session=Sess())
    assert prov.locate(dt.date(2026, 9, 20)).pdf_url.endswith("j20.pdf")


def test_journal_absent_est_signale():
    prov = la.LonabAutoProvider(Settings(), session=Sess())
    with pytest.raises(IngestionError):
        prov.locate(dt.date(2026, 9, 1))


def test_partants_reels_sans_invention():
    horses, unparsed = la.parse_real_horses(TEXT)
    assert [h.number for h in horses] == [1, 2]
    assert horses[0].odds_pdf == 4.5 and horses[0].gains == 15000
    assert horses[0].name.startswith("NATIONAL STAR")
    assert any("SWEET CHOP" in u for u in unparsed)  # cote absente : rapportée, pas devinée


def test_fetch_de_bout_en_bout(monkeypatch):
    monkeypatch.setattr(la, "_pdf_text", lambda payload: TEXT)
    race = la.LonabAutoProvider(Settings(), session=Sess()).fetch(dt.date(2026, 9, 24))
    assert len(race.horses) == 2 and race.meta.source_url.endswith("j24.pdf")


def test_provider_sans_url_utilise_automatique(monkeypatch):
    from hyperion.ingestion import LonabProvider
    monkeypatch.setattr(la.LonabAutoProvider, "fetch", lambda self, d=None: "AUTO")
    s = Settings(); s.lonab_url = None
    assert LonabProvider(s).fetch(dt.date(2026, 9, 24)) == "AUTO"
