# MEGASUS / Hyperion — Architecture Unique

> **Ce fichier est la source unique de vérité.** Le code et les prompts de
> chaque plateforme en sont des implémentations, jamais l'inverse. Toute
> modification d'un module doit être répercutée ici d'abord.

Dépôt : <https://github.com/arthur12arthur/MEGASUS>
Implémentation de référence : package Python `hyperion/` (V1).

---

## Périmètre — course française relayée par la LONAB

MEGASUS est un système **explicable** d'analyse de la course **française**
relayée par la LONAB / PMU'B pour le marché **burkinabè**. La LONAB est une
**source relais** : elle publie le programme et prend les paris au Burkina
Faso ; les courses se déroulent sur les hippodromes français du PMU.
Implémentation : `hyperion/relay.py`.

| Notion | Règle | Champ / fonction |
|---|---|---|
| Pays du marché | Burkina Faso (opérateur LONAB / PMU'B), vérifié par 1.1 | `RaceMeta.country` |
| Pays de la course | France ; toute autre valeur est refusée par 1.1 | `RaceMeta.race_country` |
| Fuseau de la course | `Europe/Paris` (UTC+1 hiver, UTC+2 été) | `relay.RACE_TZ` |
| Fuseau du programme LONAB | `Africa/Ouagadougou` (UTC+0) ; une heure naïve y est lue | `relay.RELAY_TZ` |
| Heure limite | clôture des enjeux LONAB : heure imprimée si présente, sinon départ − 10 min | `relay.schedule_for()` |
| Pari du jour | Tiercé mer./sam. ; Quarté lun./mar./jeu. ; 4+1 ven./dim. + dernier mardi du mois ; programme prioritaire | `relay.lonab_game_for()` |
| Hippodrome | référentiel français ; déduction de discipline **uniquement** pour les hippodromes mono-discipline (Auteuil ⇒ obstacle ; ParisLongchamp, Chantilly, Saint-Cloud, Deauville ⇒ plat) ; incohérence signalée (ex. plat à Vincennes) | `relay.discipline_hint()`, `relay.consistency_warnings()` |

Sources : calendrier PMU'B publié par la LONAB (<https://lonab.bf/fr/pmub>) ;
programmes relayés observés (Vincennes : départ 14h15, clôture 14h05, heure de
Ouagadougou ⇒ 15h15 à Paris en hiver). Le délai de 10 min est paramétrable
(`HYPERION_LONAB_CLOSING_MINUTES`) et doit être confirmé localement.

---

## 0. Vue d'ensemble du pipeline

Chaîne complète, dans l'ordre d'exécution :

```
1. DataIngestion → 2. DisciplineDetector → 3. MarketWatch (delta de cote, AVANT filtrage)
→ 4. DataFilter (portillon éliminatoire) → 5. BaseScorer (grille figée, score de compétitivité)
→ 6. ConsensusInterne = MonteCarloEngine + BordaConsensus + MetaFusion (score de classement)
→ 7. HADES → 8. ExternalConsensus (panel 12 sources) → 9. ConfidenceIndex
→ 10. Storage → 11. Delivery → 12. EveningEvaluation (J+1)
```

**Point clé :** le PDF officiel est publié 2 à 3 jours avant la course, donc ses
cotes peuvent être obsolètes. MarketWatch a été repositionné en amont du
filtrage (et non en fin de chaîne comme dans les versions précédentes) pour que
le filtrage dispose d'une donnée de cote à jour, et non uniquement de la cote
figée du PDF.

En parallèle, au niveau supérieur : l'**Orchestrateur / Confédération** agrège
les sorties de plusieurs systèmes Hyperion (V6, V7, V10, super agents) pour
produire une synthèse pondérée par la fiabilité historique de chaque source.

### Séparation stricte des responsabilités

| Module | Produit | Ne produit JAMAIS |
|---|---|---|
| BaseScorer (1.6) | score de **compétitivité** (0–10) | un classement |
| ConsensusInterne (1.7) | score de **classement** + probabilités | une note de compétitivité |
| DataFilter (1.5) | groupe binaire retenu/écarté | un score de cheval |
| HADES (1.8) | signal qualitatif annexe | un score, un filtre bloquant |
| ExternalConsensus (1.9) | comparaison + taux de convergence | une influence sur les scores internes |

---

## 1. Modules du pipeline course-du-jour

### 1.1 DataIngestion

**Rôle.** Récupérer le journal hippique officiel LONAB/PMU'B du jour et
identifier sans ambiguïté la course principale **française** relayée ce
jour-là par la LONAB pour le Burkina Faso.

**Entrées.** URL/scraper LONAB, date du jour.
**Sorties.** Course structurée + métadonnées (opérateur relais, pays du
marché, pays de la course, réunion R/C, hippodrome français, date, départ et
clôture LONAB conscients du fuseau, pari du jour) + `ParseReport` de
traçabilité (dont la justification du pays et de la discipline).

**Méthode.** Scraping + vérification croisée opérateur / pays du marché /
pays de la course (France) pour éviter de
récupérer la course d'un autre marché de la zone (Côte d'Ivoire, Mali, Togo) ou
d'un autre jour. Trois providers, du plus fiable au plus automatisé :

| Provider | Usage |
|---|---|
| `JsonFileProvider` / `DirectoryProvider` | journal déjà structuré — chemin reproductible, tests, exploitation manuelle |
| `LonabProvider` | scrape le journal officiel, télécharge et analyse le PDF, bascule sur un provider de secours en cas d'échec |
| `SyntheticProvider` | génère des courses à vérité connue pour les backtests |

**Règle absolue.** Le parseur n'invente jamais une valeur manquante : toute
ligne non reconnue est rapportée dans `ParseReport.unparsed` plutôt que devinée.

**Dépendances.** `GeminiManager` (1.2) pour l'extraction si le PDF n'est pas
directement parsable ; `pypdf` (paquet optionnel `hyperion[pdf]`).

**Statut.** Fonctionnel. Source de secours (Zenserp) recommandée en cas d'échec
du scraper primaire. **À valider sur un vrai PDF LONAB** : les expressions
rationnelles de reconnaissance des partants sont surchargeables via
`partant_re`.

---

### 1.2 GeminiManager

**Rôle.** Extraction structurée du contenu du PDF (partants, conditions,
musique, cotes, commentaires) et gestion des appels IA.

**Entrées.** Document texte, clés API en rotation.
**Sorties.** Données structurées par cheval (schéma fixe `RESPONSE_SCHEMA`).

**Méthode.** Extraction multi-passes, rotation de clés, cascade de modèles.

**Statut.** Point de fragilité historique (erreurs de parsing d'objet, modèles
obsolètes). Trois garde-fous en place :

1. **rotation des clés** — une clé en quota dépassé ne fait pas échouer
   l'extraction ;
2. **cascade de modèles** — un nom de modèle retiré côté Google est
   automatiquement remplacé par le suivant de `HYPERION_GEMINI_MODELS` ;
3. **validation du schéma** — une réponse incomplète est re-demandée, une
   réponse invalide est **rejetée** plutôt que partiellement utilisée.

Sans clé configurée, le manager est inopérant et le pipeline bascule sur le
parsing direct : il n'échoue jamais à cause de l'IA.

---

### 1.3 DisciplineDetector *(nouveau)*

**Rôle.** Déterminer la discipline de la course (trot attelé, trot monté, plat,
obstacle) dès l'ingestion, pour activer la bonne grille de pondération.

**Entrées.** Champ `race_type` du journal officiel ; à défaut, texte libre.
**Sorties.** Étiquette `Discipline`, utilisée par BaseScorer.

**Méthode.** Lecture directe du champ officiel (`FIELD_MAP`), avec
correspondance partielle ; règle de repli par mots-clés (`DISCIPLINE_KEYWORDS`)
si le champ est absent, vide ou non reconnu ; dernier repli sur l'hippodrome
français **seulement s'il est mono-discipline** (Vincennes, attelé ou monté,
ne permet aucune déduction). Une discipline incompatible avec l'hippodrome est
signalée, jamais corrigée en silence. Normalisation insensible aux accents et
à la casse.

**Statut.** Nouveau module — premier pas léger vers des sous-modèles complets
par discipline, sans attendre un échantillon de backtest suffisant pour
calibrer trois modèles séparés.

---

### 1.4 MarketWatch *(repositionné avant le filtrage)*

**Rôle.** Suivre l'évolution des cotes entre la publication du PDF et l'instant
de l'analyse — **avant que le filtrage n'intervienne**, pour que ce dernier ne
se base jamais sur une cote déjà obsolète.

**Entrées.** Cote indicative du PDF, cote la plus récente disponible.
**Sorties.** Delta de cote par cheval + alerte non-partant tardif, transmis à
DataFilter (1.5) et à HADES (1.8).

**Méthode.** Comparaison directe ; réutilise le panel de 12 sources externes
comme source plutôt qu'une nouvelle dépendance. Seuils : raccourcissement à
−15 %, dérive à +20 %.

**Statut.** Correctif de repositionnement : dans les versions précédentes, ce
module s'exécutait en fin de pipeline et son signal n'était donc jamais
réellement utilisé par le filtrage. Nécessite une source de secours (déjà vécu :
blocage IP des scrapers PMU sur GitHub Actions).

---

### 1.5 DataFilter — portillon éliminatoire

**Rôle.** Réduire le champ de partants au groupe des chevaux réellement
compétitifs, comme portillon binaire (retenu/écarté) — sans les ordonner et
sans produire de score propre.

**Entrées.** Données structurées par cheval (1.2) + delta de cote MarketWatch
(1.4).
**Sorties.** Groupe de sélection (5 chevaux minimum) + liste des chevaux
écartés avec motif. **Aucun score chiffré n'est attaché aux chevaux retenus.**

**Méthode.** Score de risque interne par points (gains, forme, cote, absence,
delta de cote MarketWatch) ; seuil d'élimination figé à 3 points ; maintien
forcé des 5 favoris aux cotes les plus basses ; réintégration si le groupe
descend sous 5.

**Statut.** Correctif appliqué : l'ancienne version produisait un score de
sélection normalisé en plus du score de BaseScorer (double notation) —
supprimé. Les points de risque restent un diagnostic interne
(`risk_diagnostics`), jamais une note affichée. Validé empiriquement par
Jonathan sur plusieurs tests : les chevaux éliminés figurent rarement à
l'arrivée officielle, et le groupe retenu la couvre bien.

---

### 1.6 BaseScorer — grille figée, score de compétitivité

**Rôle.** Noter chaque cheval du groupe filtré (1.5) sur 5 dimensions
pondérées, avec des poids qui varient selon la discipline détectée (1.3).
C'est l'unique score de compétitivité du pipeline.

**Entrées.** Groupe filtré, étiquette de discipline, historique de ferrure
(trot uniquement, calculé pour ce groupe filtré seulement — jamais pour
l'ensemble des partants, coût de recherche trop élevé sinon).

**Sorties.** Score pondéré par cheval (0 à 10 par dimension, agrégé en un score
de compétitivité).

**Méthode.**

| Dimension | Calcul |
|---|---|
| `historique` | gains (échelle log, queue lourde) + taux de réussite |
| `forme` | musique du cheval, places pondérées par récence décroissante |
| `aptitude` | surface (60 %) + distance (40 %) |
| `technique` | spécifique discipline, voir ci-dessous |
| `fraicheur` | distance gaussienne à la fenêtre d'absence optimale |

**Grilles de poids (figées).**

| Discipline | historique | forme | aptitude | technique | fraicheur |
|---|---|---|---|---|---|
| Trot attelé | 25 % | 20 % | 20 % | 20 % | 15 % |
| Trot monté | 25 % | 20 % | 20 % | 20 % | 15 % |
| Plat | 28 % | 22 % | 20 % | 15 % | 15 % |
| Obstacle | 25 % | 18 % | 22 % | 20 % | 15 % |

En trot attelé, la composante Technique (20 %) se décompose en 60 % adéquation
de la ferrure du jour, 25 % qualité du driver dans cette configuration, 15 %
fiabilité historique dans cette configuration. En trot monté : 25 % ferrure,
55 % driver, 20 % fiabilité. Hors trot : 50 % driver, 50 % fiabilité.

**Lissage bayésien obligatoire** sur les petits échantillons :

```
efficacité_lissée = (n × observé + 3 × baseline) / (n + 3)
```

avec `baseline` = taux de réussite moyen du champ. Sans ce lissage, une
victoire isolée suffirait à qualifier un cheval de spécialiste.

**Règles d'or du module :**

- une dimension non déterminée vaut `None` et ses poids sont redistribués sur
  les dimensions déterminées — **on n'estime jamais une donnée absente** ;
- **la cote n'entre jamais dans le calcul** (vérifié par test) ;
- la sous-composante ferrure enrichit uniquement ce module : elle ne modifie
  jamais directement le score de classement (1.7) ni le consensus externe
  (1.9).

**Statut.** Poids fixes tant qu'un backtest élargi et séparé par discipline
n'a pas permis de les recalibrer.

---

### 1.7 ConsensusInterne — score de classement

**Rôle.** Déterminer l'ordre le plus probable au sein du groupe filtré et déjà
noté par BaseScorer.

**Entrées.** Scores de compétitivité (1.6).
**Sorties.** Classement final + probabilité par position + indicateur de
stabilité inter-seeds.

**Méthode.**

1. **MonteCarloEngine** — 5 seeds fixes × 10 000 simulations. Modèle de
   Plackett-Luce échantillonné **exactement** par l'astuce de Gumbel :
   `u_i = concentration × score_i + G_i`, `G_i ~ Gumbel(0,1)`, le classement de
   la simulation étant l'ordre décroissant des `u_i`. Ce n'est pas une
   approximation : les probabilités obtenues sont cohérentes entre elles et
   somment à 1 sur chaque position (vérifié par test).
2. **BordaConsensus** — comptage de Borda sur les classements de chaque seed
   (fusion inter-seeds) et sur le classement de compétitivité.
3. **MetaFusion** — fusion **pairwise** (Copeland généralisée) des classements
   produits. Pourquoi pairwise plutôt qu'une moyenne de rangs : une moyenne de
   rangs peut désigner un vainqueur qu'aucun classement d'entrée ne place
   premier ; la fusion pairwise respecte la structure de préférence des
   entrées.

**Robustesse.** Top 3 stable sur ≥ 4 seeds sur 5 ; le rapport le signale
explicitement quand ce n'est pas le cas.

**Statut.** Historiquement performant pour retrouver le bon groupe (backtest
Manus : 8/8 gagnant dans le Top 5) mais moins précis sur l'ordre exact — c'est
le problème que corrige la séparation compétitivité/classement.

**Concentration.** Paramètre `HYPERION_MC_CONCENTRATION`, valeur par défaut
**0.5**, issue de la calibration du labo Ouroboros par protocole glissant
(section 3). L'ancienne valeur 0.8 donnait une log-loss hors échantillon de
3.25 contre 3.12 pour 0.5.

---

### 1.8 HADES

**Rôle.** Détecter les chevaux dont les chances réelles semblent masquées ou
sous-évaluées par le marché.

**Entrées.** Delta de cote (1.4), commentaires officiels, classement interne,
scores de compétitivité.
**Sorties.** Signal qualitatif par cheval (pas un score chiffré intégré au
classement).

**Méthode.** Détection d'anomalies : drop de cote sans justification visible,
absence de mise en avant malgré critères favorables, écart de rang entre
l'analyse interne et le marché (≥ 3 rangs). Sévérité faible/moyenne/forte selon
le nombre de signaux concordants.

**Statut.** Historiquement mal calibré (faux positifs fréquents) ; combiné à
tort avec le risk-management, provoquait parfois une suppression totale des
prédictions. **Doit rester un signal annexe, jamais un filtre bloquant** —
vérifié par test : un signal HADES ne retire aucun cheval du classement.

---

### 1.9 ExternalConsensus — panel fixe de 12 sources

**Rôle.** Comparer l'analyse interne aux pronostics de la presse hippique
française, à titre de comparaison uniquement.

**Entrées.** Nom de la course, date, hippodrome, fiabilité historique par
source (1.13, si disponible).
**Sorties.** Tableau comparatif affiché en **deux blocs distincts** — « sources
du panel de référence consultées » et « sources additionnelles trouvées » —
+ `p_implicite` par cheval + taux de convergence Top 5 interne/externe.

**Panel de référence (12 sources).** Genybet, Equidia, Canal Turf, PMU, France
Galop, Paris-Turf, ZEturf, Turf BZH, RueDesJoueurs, Betclic Turf, Turfomania,
Quinté du Jour (ces deux dernières ajoutées car seules sources confirmées, via
évaluation Manus AI, à publier un historique daté de pronostics et d'arrivées
directement exploitable).

**Liste de référence, pas fermée** : toute source pertinente trouvée en plus
(presse, synthèses) est ajoutée et distinguée du panel dans la sortie —
cohérent avec le comportement réel observé chez ChatGPT/Grok/Manus AI, qui
recherchent organiquement plutôt que de suivre une liste figée.

**Probabilité implicite du marché.**

```
p_implicite_i = (1 / cote_i) / Σ_j (1 / cote_j)
```

**Stratégie d'extraction (honnête et robuste).** Comme les partants sont déjà
connus, chaque source n'a pas besoin d'être « comprise » : on repère simplement
l'ordre de première apparition de chaque partant dans le texte récupéré. Une
source dont le taux de partants retrouvés est inférieur à 60 % est déclarée
**non exploitable** et exclue — mieux vaut aucune donnée qu'une donnée
inventée. Le provider HTTP est à repli gracieux : blocage, timeout ou 404
n'interrompent pas le pipeline.

**Statut.** N'influence jamais le score de compétitivité ni le score de
classement internes — comparaison affichée seulement, sauf via son taux de
convergence qui nourrit l'indice de confiance.

---

### 1.10 ConfidenceIndex

**Rôle.** Produire un indice de confiance global, calculé et non arbitraire.

**Entrées et poids.**

| Entrée | Poids |
|---|---|
| Écart de score entre rang 1 et rang 2 (saturé à 2.0) | 30 % |
| Stabilité inter-seeds (1.7) | 25 % |
| Taux de convergence externe / p_implicite (1.9) | 20 % |
| Complétude des données (1.1–1.2) | 25 % |

**Sorties.** Indice 0–10 + niveau qualitatif + justification ligne par ligne.

**Règle.** L'indice n'est **jamais** présenté sans la liste des données
manquantes qui l'affectent : un indice élevé sur des données incomplètes serait
trompeur. Quand la convergence externe est indisponible, la composante prend
une valeur neutre de 5.0 et la justification le dit explicitement.

---

### 1.11 Storage

**Rôle.** Conserver les sorties du pipeline pour permettre l'évaluation du
soir (1.13) et les backtests.

**Méthode.** JSON local commité en Git : `data/runs/AAAA/MM/JJ/<race_id>.json`.
Gratuit, versionné, lisible par n'importe quel outil.

**Alternative légère** envisagée pour V11 si Firebase reste jugé trop coûteux à
dupliquer : un unique fichier JSONL par mois, plus simple à committer.

---

### 1.12 Delivery

**Rôle.** Transmettre le rapport final à l'utilisateur, de façon identifiable
et dans les délais.

**Entrées.** Rapport structuré, signé en ouverture et clôture par le nom de la
plateforme/agent exécutant + horodatage.
**Sorties.** Message(s) Telegram et/ou email Gmail — chaque envoi porte la
signature de la plateforme d'origine, pour rester identifiable dans une boîte
Gmail qui reçoit plusieurs systèmes.

**Méthode.** Cinq messages courts thématiques plutôt qu'un message unique
(préférence confirmée) : `1/5 · Course`, `2/5 · Compétitivité`,
`3/5 · Classement`, `4/5 · Signaux annexes`, `5/5 · Confiance`.

**Vérification AVANT envoi.** Confirmation humaine si un humain est présent
(`HYPERION_HUMAN_PRESENT=true`) ; sinon, en automatisation programmée, remplacée
par une **checklist d'auto-validation** portant sur les 9 étapes du pipeline,
la séparation des deux scores et la justification de l'indice de confiance.
Jamais un envoi sans aucune vérification.

**Délai.** L'exécution doit être complétée avant la **clôture des enjeux
LONAB** (fuseau `Africa/Ouagadougou`), qui précède le départ en France.
L'en-tête affiche le départ dans les deux fuseaux, la clôture LONAB et sa
provenance, le pari PMU'B du jour et les minutes restantes. Si la clôture est
passée, le rapport s'ouvre sur **« ANALYSE HORS DÉLAI »** en précisant si la
course est déjà partie en France ou seulement close aux enjeux au Burkina.

**Statut.** Canaux hétérogènes selon la plateforme (Telegram direct pour
V6/V7/V10 ; Gmail automatisé pour les super agents) — un dashboard unique
reste à construire (Hyperion Decision Engine). Correctifs signature et timing
ajoutés suite au test comparatif du 20/09/2026 : rapports reçus dans Gmail sans
origine identifiable, et un cas (ChatGPT) exécuté après le départ de la course
sans avertissement.

---

### 1.13 EveningEvaluation (AgentH)

**Rôle.** Comparer, après la course, la prédiction du système et celle de
chaque source externe au résultat officiel — et calibrer la fiabilité de chacun
dans la durée.

**Entrées.** Rapport du matin (Storage), résultat officiel du soir, historique
cumulé des prédictions passées.
**Sorties.** Score de réussite du système + score de fiabilité cumulé par
source externe, **décomposé par discipline et par intervalle de cote** (un site
peut être fiable sur les favoris et faible sur les outsiders) + métriques de
calibration (log loss, score de Brier, ECE) en complément du simple taux de
présence dans le Top 5.

**Méthode.** Scraping du résultat officiel (API JSON PMU, configurable) avec
repli sur une saisie manuelle ; mise à jour d'un historique de fiabilité par
source et par segment (discipline × intervalle de cote), utilisé ensuite par
ExternalConsensus (1.9) et l'Orchestrateur (2.1). Repli automatique sur le taux
global d'une source quand un segment compte moins de 5 courses.

**Intervalles de cote.** `<3/1`, `3-6/1`, `6-11/1`, `11-21/1`, `>21/1`.

**Statut.** Les métriques de calibration ne sont pertinentes qu'une fois un
historique suffisant accumulé (seuil : 30 courses, bien au-delà des 8 courses
du premier backtest) — à activer progressivement, pas comme prérequis immédiat.

---

## 2. Niveau supérieur — Orchestrateur multi-systèmes

### 2.1 Orchestrateur / Confédération

**Rôle.** Synthétiser les sorties de plusieurs systèmes Hyperion indépendants
(V6, V7, V10, super agents Accio/GenSpark/Manus/Groq) en une décision unique,
en pondérant chaque source selon sa fiabilité historique.

**Entrées.** Rapports bruts de chaque système (`SystemReport`, collés
manuellement dans un premier temps ; récupération automatique envisagée
ensuite).
**Sorties.** Synthèse pondérée + décision de consensus final + taux d'accord
inter-systèmes.

**Méthode.** Classement consolidé par points de Borda pondérés. La pondération
applique un effet confédération :

```
poids_i ∝ fiabilité_i ^ 2 ,  puis  poids_i = 0.9 × normalisé_i + 0.1 / n
```

L'exposant 2 renforce le système le mieux calibré ; le plancher de 10 %
garantit qu'aucune source n'est totalement réduite au silence — une source
faible peut toujours apporter une information que les autres n'ont pas. La
somme des poids vaut exactement 1.

**Statut.** Correspond au projet Hyperion Decision Engine — priorité : fichier
d'architecture avant code, solutions gratuites uniquement. Premier signal réel
obtenu le 20/09/2026 (hors mécanisme automatisé, comparaison manuelle) :
ChatGPT n'exécute pas réellement le protocole Monte Carlo (pas d'accès code,
remplace par un consensus qualitatif non chiffré) ; Grok produit un classement
chiffré complet mais sans détail par seed ni tableau p_implicite ; Manus AI a
produit le rapport le plus complet et rigoureux (MC+Borda+MetaFusion détaillés,
MarketWatch horodaté), avec toutefois un Top 3 identique sur les 5 seeds à
vérifier (possible signe que les seeds ne varient pas réellement l'aléa).
Confirme la distinction « pipelines avec code » vs « agents prompt seul » de la
section 14 du Système Prompt Canonique, plutôt qu'une hiérarchie de qualité
générale entre plateformes.

---

## 3. Principe de non-régression

Toute modification apportée à un module (recalibration de poids, ajout d'une
dimension par discipline, changement de seuil) doit être :

1. **Testée d'abord dans le laboratoire Ouroboros** (lecture seule, mode shadow
   sur courses passées) avant tout déploiement sur un système en production,
   selon un protocole glissant sans fuite de données : entraîner/calibrer
   uniquement sur des courses antérieures à une date donnée, tester sur la
   période suivante, avancer la fenêtre, puis agréger — **jamais recalibrer un
   paramètre en observant directement le résultat qu'on cherche à prédire**.
2. **Appliquée identiquement sur toutes les plateformes** qui partagent ce
   module, pour ne jamais casser la comparabilité entre systèmes utilisée par
   l'Orchestrateur.
3. **Documentée dans ce fichier**, qui reste la source unique de vérité.

### Laboratoire Ouroboros — ce qu'il mesure

Le labo compare une configuration candidate à une configuration de référence
sur des courses passées et émet une recommandation explicite :
`RÉGRESSION détectée — ne pas déployer` ou `Aucune régression — déployable`.

Il expose aussi `calibrate_concentration()`, qui choisit la concentration du
Monte Carlo minimisant la log-loss, et `walk_forward_folds()`, qui découpe une
série de dates en plis (entraînement, test) glissants.

### Résultat de la calibration de la concentration (V1)

Protocole : 60 courses synthétiques, calibration sur les 30 premières,
validation hors échantillon sur les 30 suivantes.

| Concentration | Log-loss (entraînement) | Log-loss (hors échantillon) | Brier (HORS) |
|---|---|---|---|
| 0.3 | 3.0620 | 3.1402 | 0.0811 |
| **0.4** | 3.0034 | **3.1185** | 0.0813 |
| 0.5 | 2.9714 | 3.1222 | 0.0823 |
| 0.6 | 2.9607 | 3.1485 | 0.0840 |
| 0.7 | 2.9743 | 3.1925 | 0.0862 |
| **0.8** (ancien défaut) | 2.9994 | **3.2525** | 0.0887 |
| 1.0 | 3.0879 | 3.4085 | 0.0941 |
| 2.0 | 4.5569 | 4.5511 | 0.1159 |

Décision : **0.5** retenu (0.4 et 0.5 sont statistiquement indifférenciés ;
0.5 est le choix le moins extrême des deux et reste bien meilleur que 0.8).
Le taux de gagnant dans le Top 5 est inchangé (73 %) : la concentration pilote
la **calibration** des probabilités, pas le pouvoir de discrimination du
classement.

---

## 4. Implémentation de référence (package `hyperion`)

```
hyperion/
├── __init__.py            point d'entrée du package
├── cli.py                 interface en ligne de commande
├── config.py              configuration (variables d'environnement)
├── models.py              modèles de données canoniques
├── pipeline.py            enchaînement des modules 1.1 à 1.10
├── ingestion.py           1.1 DataIngestion + parsing du journal
├── gemini.py              1.2 GeminiManager
├── analysis/
│   ├── discipline.py      1.3 DisciplineDetector + grilles de poids
│   ├── market.py          1.4 MarketWatch
│   ├── filter.py          1.5 DataFilter
│   └── scorer.py          1.6 BaseScorer
├── consensus/
│   ├── __init__.py        1.7 ConsensusInterne
│   ├── montecarlo.py      1.7a MonteCarloEngine (Plackett-Luce)
│   ├── borda.py           1.7b BordaConsensus
│   └── metafusion.py      1.7c MetaFusion (pairwise)
├── anomaly/
│   └── hades.py           1.8 HADES
├── external.py            1.9 ExternalConsensus
├── confidence.py          1.10 ConfidenceIndex
├── storage.py             1.11 Storage
├── delivery.py            1.12 Delivery
├── evaluation/
│   ├── calibration.py     log loss, Brier, ECE, fiabilité par segment
│   └── evening.py         1.13 EveningEvaluation
├── orchestrator.py        2.1 Orchestrateur / Confédération
├── lab.py                 laboratoire Ouroboros (non-régression)
└── synthetic.py           générateur de courses à vérité connue
```

### Commandes

```bash
python -m hyperion.cli demo                     # démonstration sur course synthétique
python -m hyperion.cli run --input journal.json # analyse d'une course réelle
python -m hyperion.cli backtest --n 40          # backtest de non-régression
python -m hyperion.cli calibrate --n 20         # calibration de la concentration
python -m hyperion.cli evaluate                 # évaluation du soir
```

### Données d'exemple

| Fichier | Rôle |
|---|---|
| `data/samples/journal_2026-09-20.json` | course structurée synthétique (trot attelé, hippodrome français, départ 15h15 heure de Paris) |
| `data/samples/panel_2026-09-20.json` | pronostics de 5 sources externes |
| `data/samples/cotes_fraiches_2026-09-20.json` | cotes récentes (dérive depuis le PDF) |
| `data/samples/resultats_2026-09-20.json` | arrivée officielle, pour l'évaluation J+1 |

---

## 5. Distinction « pipelines avec code » vs « agents prompt seul »

Un module peut être implémenté en code (pipelines) ou suivi comme procédure de
prompt (agents sans exécution de code) — **la logique reste identique**. Ce
dépôt est l'implémentation en code de référence. Les procédures de prompt des
autres plateformes doivent reproduire la même logique, module par module, et
être validées par ce même laboratoire Ouroboros pour rester comparables.

---

## 6. Journal des modifications

| Date | Module | Changement | Justification |
|---|---|---|---|
| 2026-09-24 | 1.4 | MarketWatch repositionné avant le filtrage | son signal n'était jamais utilisé par le filtrage |
| 2026-09-24 | 1.5 | suppression du score de sélection normalisé | double notation avec BaseScorer |
| 2026-09-24 | 1.6 | dimension non déterminée ⇒ `None`, poids redistribués | on n'estime jamais une donnée absente |
| 2026-09-24 | 1.6 | lissage bayésien sur driver et fiabilité | une victoire isolée ne fait pas un spécialiste |
| 2026-09-24 | 1.7 | concentration 0.8 ⇒ 0.5 | calibration walk-forward, log-loss 3.25 ⇒ 3.12 |
| 2026-09-24 | 1.8 | HADES strictement annexe, jamais bloquant | faux positifs fréquents historiquement |
| 2026-09-24 | 1.9 | source non exploitable sous 60 % de partants retrouvés | mieux vaut aucune donnée qu'une donnée inventée |
| 2026-09-24 | 1.10 | liste des données manquantes obligatoire | un indice élevé sur données incomplètes est trompeur |
| 2026-09-24 | 1.12 | signature + horodatage + « ANALYSE HORS DÉLAI » | test comparatif du 20/09/2026 |
| 2026-09-24 | périmètre | course française relayée par la LONAB : `race_country`, double fuseau, clôture LONAB, pari du jour | la LONAB est une source relais, les courses se déroulent en France |
| 2026-09-24 | 1.1 | hippodromes, heure de départ/clôture, R/C, pari et nom de l'épreuve lus dans le programme | les heures étaient lues en UTC et les hippodromes supposés burkinabè |
| 2026-09-24 | 1.3 | repli hippodrome mono-discipline + signalement d'incohérence | explicabilité, aucune devinette sur Vincennes |
| 2026-09-24 | 1.12 | heure limite = clôture LONAB (≈ départ − 10 min), plus le départ | un rapport après la clôture n'est plus jouable |
| 2026-09-24 | tests | écritures isolées hors de `data/runs/` (`HYPERION_RUNS_DIR`) | un test polluait le dépôt |
| 2026-09-24 | 2.1 | pondération par fiabilité² avec plancher de 10 % | effet confédération sans réduire une source au silence |
