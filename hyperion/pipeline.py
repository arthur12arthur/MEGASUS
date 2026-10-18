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
from hyperion.confidence import (
    ConfidenceIndex,
    ConfidenceResult,
    score_coverage,
    summarise as confidence_summary,
)
from hyperion.delivery import OutOfDeadline, check_deadline
from hyperion.external import (
    ExternalConsensus,
    ExternalConsensusResult,
    SourcePick,
    summarise as external_summary,
)
from hyperion import relay
from hyperion.decision import Decision, decide, render as decision_render
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
    decision: Decision | None = None
    decision_note: str = ""

    # -- accès rapides -----------------------------------------------------

    @property
    def ranking(self) -> list[str]:
        return list(self.consensus.ranking)

    @property
    def top5(self) -> list[str]:
        return list(self.consensus.ranking[:5])

    def names(self) -> dict[str, str]:
        return {h.horse_id: h.name for h in self.race.runners}

    def numbers(self) -> dict[str, int | None]:
        return {h.horse_id: h.number for h in self.race.runners}

    def display_names(self) -> dict[str, str]:
        """Nom précédé du numéro au journal — c'est ce que les blocs affichent."""
        return {
            h.horse_id: (f"n°{h.number} · {h.name}" if h.number is not None else h.name)
            for h in self.race.runners
        }

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
        scoring_coverage=score_coverage(scorer),
        identification=identification_warnings(race),
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
    result.decision, result.decision_note = _compute_decision(race, list(consensus.ranking))
    result.record = _build_record(result, why, divergences)
    result.record["decision"] = (
        result.decision.as_dict() if result.decision else {"status": "indisponible", "raison": result.decision_note}
    )
    result.blocks = _build_blocks(result, divergences)
    return result


def _compute_decision(race: Race, system_ranking: list[str]) -> tuple[Decision | None, str]:
    """Moteur de décision sur la meilleure cote disponible (directe PMU > PDF)."""
    rows, source, stamp = [], "cote du PDF LONAB (relevé du matin)", None
    for horse in race.runners:
        direct = horse.odds_latest
        pmu = (horse.raw or {}).get("pmu") or {}
        if direct and direct > 1.0:
            odds = direct
            source = "cote directe PMU"
            stamp = stamp or pmu.get("odds_direct_at")
        elif horse.odds_pdf and horse.odds_pdf > 1.0:
            odds = horse.odds_pdf
        else:
            continue
        rows.append({"horse_id": horse.horse_id, "number": horse.number, "name": horse.name, "odds": odds})
    try:
        return decide(rows, system_ranking=system_ranking, cote_source=source, cote_horodatage=stamp), ""
    except ValueError as exc:
        return None, f"cotes insuffisantes ({exc})"


def identification_warnings(race: Race) -> list[str]:
    """Liste ce qui manque pour identifier la course de façon fiable.

    Une course sans hippodrome, sans discipline reconnue ou sans nom lisible est
    analysée avec des poids génériques : le résultat doit alors être présenté
    comme peu fiable, jamais comme une analyse normale.
    """
    meta = race.meta
    issues: list[str] = []
    if not meta.hippodrome:
        issues.append("hippodrome non identifié")
    if meta.discipline.value == "unknown":
        issues.append("discipline non reconnue (poids génériques appliqués)")
    if not meta.name:
        issues.append("nom de la course non lu")
    return issues


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
    numbers = result.numbers()
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
        "identification": identification_warnings(race),
        "confiance": result.confidence.as_dict(),
        "delai": result.out_of_deadline.as_dict(),
        "relais": _relay_context(race),
        "classement": [
            {
                "rang": i,
                "numero": numbers.get(h),
                "cheval": names.get(h, h),
                "horse_id": h,
            }
            for i, h in enumerate(result.consensus.ranking, start=1)
        ],
        "competitivite": [
            {
                "numero": s.number,
                "cheval": names.get(s.horse_id, s.horse_id),
                "score": round(s.competitiveness, 4),
            }
            for s in result.scorer.ordered()
        ],
    }


def _build_blocks(
    result: PipelineResult,
    divergences: Mapping[str, Sequence[str]],
) -> dict[str, str]:
    """Messages courts thématiques (préférence confirmée de l'utilisateur)."""
    race = result.race
    names = result.display_names()
    from hyperion.delivery import Delivery

    delivery = Delivery()
    header = delivery.render_header(race, result.out_of_deadline)

    #: Chaque bloc est un message court thématique, auto-suffisant : le titre
    #: est répété dans le corps pour rester lisible sur Telegram et en email.
    blocks: dict[str, str] = {}

    def add(title: str, body: str) -> None:
        blocks[title] = f"*{title}*\n{body}"

    ident = identification_warnings(race)
    ident_line = (
        "⚠ Identification incomplète : " + " ; ".join(ident) + ". "
        "Analyse à prendre avec grande prudence.\n\n"
        if ident
        else ""
    )
    add(
        "1/5 · Course",
        f"{ident_line}{header}\n\n"
        f"Discipline : {race.meta.discipline.label_fr}",
    )
    add(
        "2/5 · Décision",
        decision_render(result.decision)
        if result.decision
        else f"DÉCISION indisponible : {result.decision_note}. Aucune sélection fondée sur la cote n'est possible.",
    )
    add(
        "3/5 · Classement du système (secondaire)",
        "Au backtest sur 2 057 courses réelles, ce classement fait MOINS bien que la cote "
        "(gagnant en tête : 20 % contre 31 %). À lire comme un avis complémentaire, pas comme "
        "une référence.\n\n" + consensus_summary(result.consensus, names),
    )
    add(
        "4/5 · Écarts système / marché",
        f"{hades_summary(result.hades)}\n\n"
        f"{external_summary(result.external, race, divergences)}",
    )
    cov = score_coverage(result.scorer)
    missing = list(result.confidence.missing_data)
    missing_text = ""
    if missing:
        shown = "\n".join(f"    · {m}" for m in missing[:8])
        extra = f"\n    · … et {len(missing) - 8} autre(s)" if len(missing) > 8 else ""
        missing_text = f"Données manquantes ({len(missing)}) :\n{shown}{extra}\n\n"
    add(
        "5/5 · Limites et détails",
        f"Le score du système repose sur {cov * 100:.0f} % du modèle (le reste est indéterminé faute de données).\n\n"
        f"{missing_text}"
        f"{market_summary(result.market)}\n\n"
        f"{filter_summary(result.filter_result, race)}\n\n"
        f"{delivery.signature({'course': race.race_id})}",
    )
    return blocks


def _free_text(race: Race) -> str:
    """Texte libre de repli pour le DisciplineDetector (commentaires, musique)."""
    parts = [race.meta.name or "", race.meta.terrain or ""]
    parts.extend(h.comment or "" for h in race.horses)
    return " ".join(part for part in parts if part)


__all__ = ["run_pipeline", "PipelineResult"]
