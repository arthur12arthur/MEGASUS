"""Tests du module 1.4 — MarketWatch (positionné avant le filtrage)."""

from __future__ import annotations

import datetime as dt

from hyperion.analysis.market import MarketWatch, summarise


class TestMarketWatch:
    def test_delta_calcule(self, simple_race):
        watch = MarketWatch()
        result = watch.observe(
            simple_race, latest_odds={"h1": 2.5, "h2": 5.0}, as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc)
        )
        signal = result.signal("h1")
        assert signal is not None
        assert abs(signal.delta - (2.5 - 3.5) / 3.5) < 1e-9
        assert signal.direction == "raccourci"

    def test_derive(self, simple_race):
        watch = MarketWatch()
        result = watch.observe(
            simple_race, latest_odds={"h3": 12.0}, as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc)
        )
        assert result.signal("h3").direction == "derive"

    def test_stable(self, simple_race):
        watch = MarketWatch()
        result = watch.observe(
            simple_race, latest_odds={"h2": 5.0}, as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc)
        )
        assert result.signal("h2").direction == "stable"

    def test_sans_cote_fraiche(self, simple_race):
        watch = MarketWatch()
        result = watch.observe(
            simple_race, latest_odds={}, as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc)
        )
        assert result.signal("h1").direction == "inconnu"
        assert any("h1" in w for w in result.warnings)

    def test_non_partant_tardif(self, simple_race):
        watch = MarketWatch()
        result = watch.observe(
            simple_race,
            latest_odds={},
            as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc),
            non_runners=["h4"],
        )
        assert "h4" in result.late_non_runners

    def test_application_des_cotes_fraiches(self, simple_race):
        watch = MarketWatch()
        result = watch.observe(
            simple_race, latest_odds={"h1": 3.0}, as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc)
        )
        watch.apply_fresh_odds(simple_race, result)
        assert simple_race.by_id("h1").odds == 3.0
        # La cote du PDF reste conservée pour la traçabilité.
        assert simple_race.by_id("h1").odds_pdf == 3.5

    def test_resume_lisible(self, simple_race):
        watch = MarketWatch()
        result = watch.observe(
            simple_race,
            latest_odds={"h1": 2.5},
            as_of=dt.datetime(2026, 9, 20, 10, tzinfo=dt.timezone.utc),
        )
        text = summarise(result)
        assert "MarketWatch" in text
        assert "Tonnerre de Mai" in text
