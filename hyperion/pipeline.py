"""Pipeline Hyperion — enchaînement des modules 1.1 à 1.10.

Ordre d'exécution (architecture unique, section 0) :

    DataIngestion → DisciplineDetector → MarketWatch → DataFilter
    → BaseScorer → ConsensusInterne → HADES → ExternalConsensus
    → ConfidenceIndex → Storage → Delivery

Point clé respecté ici : le PDF officiel étant publié 2 à 3 jours avant la
course, MarketWatch est positionné AVANT le filtrage, pour que le filtrage
dispose d'une cote à jour et non de la cote figée du PDF.

La séparation des responsabilités est stricte :
  * BaseScorer produit l'unique score de COMPÉTITIVITÉ ;
  * ConsensusInterne produit l'unique score de CLASSEMENT ;
  * HADES et ExternalConsensus ne produisent que des signaux annexes.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from hyperion.analysis.discipline import detect_discipline, explain_weights
from hyperion.analysis.filter import DataFilter, FilterResult, summarise as filter_summary
from hyperion.analysis.market import MarketWatch, MarketWatchResult, summarise as market_summary
from hyperion.analysis.scorer import BaseScorer, ScorerResult, summarise as scorer_summary
from hyperion.anomaly.hades import HADES, HadesResult, summarise as hades_summary
from hyperion.config import Settings
from hyperion.consensus import ConsensusInterne, ConsensusResult, summarise as consensus_summary
from hyperion.confidence import ConfidenceIndex, ConfidenceResult, summarise as confidence_summary
from hyperion.delivery import OutOfDeadline, check_deadline
from hyperion.external import (
    ExternalConsensus,
    ExternalConsensusResult,
    SourcePick,
    summarise as external_summary,
)
from hyperion import relay
from hyperion.models import Race


@dataclass
class PipelineResult:
    """Sortie complète du pipeline, prête à être stockée et livrée."""

    race: Race
    market: MarketWatchResult
    filter_result: FilterResult
    scorer: ScorerResult
    consensus: ConsensusResult
    hades: HadesResult
    external: ExternalConsensusResult
    confidence: ConfidenceResult
    out_of_deadline: OutOfDeadline
    record: dict[str, Any] = field(default_factory=dict)
    blocks: dict[str, str] = field(default_factory=dict)

    # -- accès rapides -----------------------------------------------------

    @property
    def ranking(self) -> list[str]:
        return list(self.consensus.ranking)

    @property
    def top5(self) -> list[str]:
        return list(self.consensus.ranking[:5])

    def names(self) -> dict[str, str]:
        return {h.horse_id: h.name for h in self.race.runners}

    def as_record(self) -> dict[str, Any]:
        return dict(self.record)


def run_pipeline(
    race: Race,
    settings: Settings | None = None,
    latest_odds: Mapping[str, float] | None = None,
    external_picks: Sequence[SourcePick] | None = None,
    late_non_runners: Iterable[str] = (),
    now: dt.datetime | None = None,
) -> PipelineResult:
    """Exécute la chaîne complète du pipeline « course du jour »."""
    settings = settings or Settings()

    # -- 1.3 DisciplineDetector -------------------------------------------
    discipline, why = detect_discipline(race.meta.race_type, free_text=_free_text(race))
    if discipline.value == "unknown":
        # Dernier repli : hippodrome français mono-discipline (Auteuil, Chantilly…).
        hinted, hint_why = relay.discipline_hint(race.meta.hippodrome)
        if hinted is not None:
            discipline, why = hinted, f"{why} ; repli hippodrome ({hint_why})"
    race.meta.discipline = discipline

    # -- 1.4 MarketWatch (AVANT le filtrage) ------------------------------
    market = MarketWatch(settings).observe(
        race,
        latest_odds=latest_odds,
        as_of=now,
        non_runners=late_non_runners,
    )
    MarketWatch(settings).apply_fresh_odds(race, market)

    # -- 1.5 DataFilter ---------------------------------------------------
    filter_result = DataFilter(settings).filter(race, market)

    # -- 1.6 BaseScorer ---------------------------------------------------
    scorer = BaseScorer(settings).score(race, filter_result.selected, discipline)

    # -- 1.7 ConsensusInterne ---------------------------------------------
    competitiveness = {s.horse_id: s.competitiveness for s in scorer.scores}
    consensus = ConsensusInterne(settings).compute(competitiveness, names=race.names_map())

    # -- 1.8 HADES --------------------------------------------------------
    hades = HADES(settings).analyse(
        race,
        market=market,
        internal_ranking=consensus.ranking,
        competitiveness=competitiveness,
    )

    # -- 1.9 ExternalConsensus --------------------------------------------
    external = ExternalConsensus(settings).compute(
        race, external_picks or [], internal_ranking=consensus.ranking
    )
    divergences = ExternalConsensus(settings).divergences(external, consensus.ranking, race)

    # -- 1.10 ConfidenceIndex ---------------------------------------------
    confidence = ConfidenceIndex().compute(
        race,
        competitiveness,
        stability=consensus.stability,
        convergence=external.convergence if external.picks else None,
        discipline=discipline,
    )

    # -- délai ------------------------------------------------------------
    late = check_deadline(
        race, now=now, tz_name=settings.local_tz,
        closing_minutes=settings.lonab_closing_minutes,
    )

    # -- assemblage de l'enregistrement -----------------------------------
    result = PipelineResult(
        race=race,
        market=market,
        filter_result=filter_result,
        scorer=scorer,
        consensus=consensus,
        hades=hades,
        external=external,
        confidence=confidence,
        out_of_deadline=late,
    )
    result.record = _build_record(result, why, divergences)
    result.blocks = _build_blocks(result, divergences)
    return result


def _relay_context(race: Race) -> dict[str, Any]:
    """Contexte relais explicite : course française, marché LONAB burkinabè."""
    game = relay.lonab_game_for(race.meta.date, race.meta.bet_type)
    return {
        "operateur_relais": race.meta.operator or "LONAB",
        "marche": race.meta.country or "Burkina Faso",
        "pays_course": race.meta.race_country or relay.RACE_COUNTRY,
        "fuseau_course": relay.RACE_TZ,
        "fuseau_marche": relay.RELAY_TZ,
        "pari_du_jour": game.as_dict() if game else None,
        "coherence_hippodrome": relay.consistency_warnings(
            race.meta.hippodrome, race.meta.discipline
        ),
    }


def _build_record(
    result: PipelineResult,
    discipline_reason: str,
    divergences: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    race = result.race
    names = result.names()
    return {
        "kind": "daily_analysis",
        "race_id": race.race_id,
        "date": race.meta.date.isoformat() if race.meta.date else None,
        "meta": race.meta.as_dict(),
        "discipline": {
            "value": race.meta.discipline.value,
            "label": race.meta.discipline.label_fr,
            "raison": discipline_reason,
            "poids": result.scorer.weights,
        },
        "marketwatch": result.market.as_dict(),
        "datafilter": result.filter_result.as_dict(),
        "basescorer": result.scorer.as_dict(),
        "consensusinterne": result.consensus.as_dict(),
        "hades": result.hades.as_dict(),
        "externalconsensus": result.external.as_dict(),
        "divergences": {k: list(v) for k, v in divergences.items()},
        "confiance": result.confidence.as_dict(),
        "delai": result.out_of_deadline.as_dict(),
        "relais": _relay_context(race),
        "classement": [
            {"rang": i, "cheval": names.get(h, h), "horse_id": h}
            for i, h in enumerate(result.consensus.ranking, start=1)
        ],
        "competitivite": [
            {"cheval": names.get(s.horse_id, s.horse_id), "score": round(s.competitiveness, 4)}
            for s in result.scorer.ordered()
        ],
    }


def _build_blocks(
    result: PipelineResult,
    divergences: Mapping[str, Sequence[str]],
) -> dict[str, str]:
    """Messages courts thématiques (préférence confirmée de l'utilisateur)."""
    race = result.race
    names = result.names()
    from hyperion.delivery import Delivery

    delivery = Delivery()
    header = delivery.render_header(race, result.out_of_deadline)

    #: Chaque bloc est un message court thématique, auto-suffisant : le titre
    #: est répété dans le corps pour rester lisible sur Telegram et en email.
    blocks: dict[str, str] = {}

    def add(title: str, body: str) -> None:
        blocks[title] = f"*{title}*\n{body}"

    add(
        "1/5 · Course",
        f"{header}\n\n"
        f"Discipline : {race.meta.discipline.label_fr}\n"
        f"{explain_weights(race.meta.discipline)}\n\n"
        f"{market_summary(result.market)}\n\n"
        f"{filter_summary(result.filter_result, race)}",
    )
    add("2/5 · Compétitivité", scorer_summary(result.scorer))
    add("3/5 · Classement", consensus_summary(result.consensus, names))
    add(
        "4/5 · Signaux annexes",
        f"{hades_summary(result.hades)}\n\n"
        f"{external_summary(result.external, race, divergences)}",
    )
    add(
        "5/5 · Confiance",
        f"{confidence_summary(result.confidence)}\n\n"
        f"{delivery.signature({'course': race.race_id})}",
    )
    return blocks


def _free_text(race: Race) -> str:
    """Texte libre de repli pour le DisciplineDetector (commentaires, musique)."""
    parts = [race.meta.name or "", race.meta.terrain or ""]
    parts.extend(h.comment or "" for h in race.horses)
    return " ".join(part for part in parts if part)


__all__ = ["run_pipeline", "PipelineResult"]
