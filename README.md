# 🏇 MEGASUS — analyse explicable de la course française relayée par la LONAB

MEGASUS est un système **explicable** d'analyse de la course hippique
**française** relayée par la LONAB / PMU'B pour le marché **burkinabè**. Il
implémente l'architecture unique *Hyperion*. **100 % gratuit**, entièrement
automatisable sur GitHub Actions, sans aucun service payant.

> **La LONAB est une source relais.** Les courses analysées se déroulent en
> France (Vincennes, ParisLongchamp, Chantilly, Auteuil, Cagnes-sur-Mer…) ;
> la LONAB publie le programme et prend les paris au Burkina Faso.

> L'architecture complète, module par module, est décrite dans
> **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — c'est la source unique
> de vérité. Ce README est le mode d'emploi.

---

## Ce que fait le système

À partir du journal officiel du jour, Hyperion produit un rapport complet en
5 messages courts :

| # | Message | Contenu |
|---|---|---|
| 1/5 | **Course** | discipline, dérive des cotes, chevaux retenus/écartés avec motifs |
| 2/5 | **Compétitivité** | note 0–10 sur 5 dimensions pondérées |
| 3/5 | **Classement** | ordre probable + probabilité par position + stabilité |
| 4/5 | **Signaux annexes** | HADES (valeur masquée) + comparaison au panel de 12 sources |
| 5/5 | **Confiance** | indice 0–10 calculé, justifié, avec les données manquantes |

Chaque chiffre du rapport est accompagné de sa justification (poids, motif
d'élimination, écart de score, données manquantes) : rien n'est une boîte noire.

---

## Périmètre : course française, marché burkinabè

| Élément | Valeur | Où c'est géré |
|---|---|---|
| Lieu de la course | **France** (hippodromes PMU) | `RaceMeta.race_country` |
| Marché de paris | **Burkina Faso** (LONAB, PMU'B) | `RaceMeta.country` — vérifié avant analyse |
| Heure du départ | heure de **Paris** (UTC+1 l'hiver, UTC+2 l'été) | `hyperion/relay.py` |
| Heures du programme LONAB | heure de **Ouagadougou** (UTC+0) | lecture du PDF |
| Heure limite pour jouer | **clôture LONAB** ≈ départ − 10 min | `HYPERION_LONAB_CLOSING_MINUTES` |
| Pari du jour | Tiercé mer./sam. · Quarté lun./mar./jeu. · 4+1 ven./dim. et dernier mardi du mois | `relay.lonab_game_for()` |

Le premier message du rapport affiche explicitement :
`Paris-Vincennes (France) · R1C4 · Trot attelé`, puis
`départ 14h15 à Ouagadougou (16h15 heure de Paris, écart 2 h) · clôture LONAB 14h05`,
le pari PMU'B du jour et les minutes restantes avant la clôture. Si la clôture
LONAB est passée — même si la course n'est pas encore partie en France — le
rapport s'ouvre sur **« ANALYSE HORS DÉLAI »**.

Sources du calendrier et des horaires : page PMU'B de la LONAB
(<https://lonab.bf/fr/pmub>) ; exemples de programmes relayés (départ 14h15 /
clôture 14h05 à Vincennes). Le délai de 10 minutes est une valeur observée,
paramétrable, et remplacée par l'heure de clôture imprimée quand le programme
la donne.

---

## Démarrage en 30 secondes

```bash
git clone https://github.com/arthur12arthur/MEGASUS.git
cd MEGASUS
pip install -e ".[dev]"

# Démonstration immédiate sur une course synthétique
python -m hyperion.cli demo

# Analyse d'une course réelle (journal structuré en JSON)
python -m hyperion.cli run --input data/samples/journal_2026-09-20.json \
    --panel data/samples/panel_2026-09-20.json \
    --odds  data/samples/cotes_fraiches_2026-09-20.json \
    --store
```

---

## Le pipeline

```
1. DataIngestion → 2. DisciplineDetector → 3. MarketWatch (AVANT filtrage)
→ 4. DataFilter (portillon) → 5. BaseScorer (compétitivité)
→ 6. ConsensusInterne (MonteCarlo + Borda + MetaFusion → classement)
→ 7. HADES → 8. ExternalConsensus (12 sources) → 9. ConfidenceIndex
→ 10. Storage → 11. Delivery → 12. EveningEvaluation (J+1)
```

**Point clé :** le PDF officiel est publié 2 à 3 jours avant la course, donc ses
cotes peuvent être obsolètes. MarketWatch est positionné **avant** le filtrage
pour que le filtrage dispose d'une cote à jour.

### Séparation stricte des scores

| Module | Produit | Ne produit jamais |
|---|---|---|
| `BaseScorer` | score de **compétitivité** (0–10) | un classement |
| `ConsensusInterne` | **classement** + probabilités | une note de compétitivité |
| `DataFilter` | groupe binaire retenu/écarté | un score de cheval |
| `HADES` | signal annexe | un filtre bloquant |
| `ExternalConsensus` | comparaison | une influence sur les scores internes |

---

## Résultats mesurés

Backtest de non-régression sur 40 courses synthétiques à vérité connue
(`python -m hyperion.cli backtest --n 40 --seed 2026`) :

| Mesure | Hyperion | Marché (cote) | Hasard |
|---|---|---|---|
| Gagnant dans le Top 5 | **80 %** | 80 % | 40 % |
| Gagnant annoncé en tête | **35 %** | 3 % | 8 % |
| Score de Brier | **0.070** | — | — |
| Log-loss moyenne | **2.68** | — | — |

Le système est **compétitif avec un marché efficient** sur le Top 5, et
**10× plus précis que la cote** pour identifier le gagnant. La calibration des
probabilités (Brier 0.070) est excellente.

> ⚠️ Ces chiffres portent sur des courses **synthétiques** : ils valident la
> mécanique du pipeline (les observables portent bien un signal, les
> probabilités sont cohérentes). Battre un marché réel est une question
> empirique distincte, qui nécessite un historique de vraies courses — c'est
> exactement le rôle du module EveningEvaluation.

---

## Tester

```bash
python -m pytest              # 182 tests
python -m hyperion.cli backtest --n 40   # non-régression
python -m hyperion.cli calibrate --n 20  # calibration walk-forward
```

La CI GitHub Actions exécute les tests + le backtest de non-régression sur
Python 3.11, 3.12 et 3.13 à chaque push.

---

## Automatisation quotidienne

Le workflow `.github/workflows/daily.yml` analyse la course du jour à 09h30 UTC
(09h30 à Ouagadougou, 10h30 ou 11h30 à Paris selon la saison), bien avant la
clôture LONAB des courses françaises de l'après-midi, et évalue le résultat
officiel PMU à 22h00 UTC. Aucun serveur à maintenir.

**Secrets à renseigner** (Settings → Secrets and variables → Actions) :

| Secret | Rôle |
|---|---|
| `HYPERION_TELEGRAM_TOKEN` / `HYPERION_TELEGRAM_CHAT_ID` | livraison Telegram (gratuit) |
| `HYPERION_SMTP_HOST` / `_USER` / `_PASSWORD` | livraison email Gmail (mot de passe d'application) |
| `HYPERION_MAIL_FROM` / `HYPERION_MAIL_TO` | expéditeur / destinataires |
| `HYPERION_LONAB_URL` | URL du journal officiel du jour |
| `HYPERION_GEMINI_KEYS` | clés API Gemini (optionnel, rotation automatique) |

Voir [`.env.example`](.env.example) pour la liste complète. **Aucune clé n'est
codée en dur** : tout passe par l'environnement.

---

## Format d'entrée

Deux chemins, du plus simple au plus automatique :

**1. Journal structuré (recommandé, reproductible).** Un JSON décrivant la
course — voir `data/samples/journal_2026-09-20.json`. C'est le chemin utilisé
par les tests et par l'exploitation manuelle.

**2. Journal PDF officiel.** Renseigner `HYPERION_LONAB_URL`. Le scraper
télécharge le PDF, en extrait le texte et l'analyse. Les expressions de
reconnaissance des partants sont surchargeables — **le rendu exact d'un PDF
LONAB doit être validé sur un vrai document**. En cas d'échec, le pipeline
bascule automatiquement sur le provider JSON de secours.

Dans les deux cas, le parseur **n'invente jamais** une valeur manquante : toute
ligne non reconnue est rapportée plutôt que devinée.

---

## Principes de conception

1. **On n'estime jamais une donnée absente.** Une dimension non déterminée vaut
   `None` et ses poids sont redistribués — pas de valeur inventée.
2. **La cote n'entre jamais dans le score de compétitivité.** Le marché est
   comparé, jamais mélangé.
3. **Mieux vaut aucune donnée qu'une donnée inventée.** Une source externe
   sous 60 % de partants retrouvés est déclarée non exploitable.
4. **Un indice de confiance ne se présente jamais seul.** Il vient toujours
   avec sa justification et la liste des données manquantes.
5. **Aucune modification sans non-régression.** Le labo Ouroboros teste en mode
   shadow, par protocole glissant sans fuite de données, avant tout déploiement.

---

## Stack

Python 3.11+ · NumPy · requests · BeautifulSoup · pytest · GitHub Actions
Aucune dépendance payante, aucun service externe obligatoire.

## Licence

MIT
