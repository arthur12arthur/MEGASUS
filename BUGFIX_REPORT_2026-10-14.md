# Rapport de correction — métadonnées PDF LONAB

## Cause exacte

Le PDF LONAB réel du 14/10/2026 ne suit pas le gabarit synthétique initial :

- la date principale est textuelle dans `"4+1" DU MERCREDI 14 OCTOBRE 2026` ;
- la première date numérique rencontrée par l’ancien parseur était celle d’un résultat historique (`10/10/2026`), d’où la date erronée du 10/10 ;
- l’en-tête de course est `REIMS - GRAND NATIONAL DU TROT (...)`, sans préfixe `R1C1` ni nom commençant par `PRIX` ;
- la clôture est imprimée `ARRÊT DES JEUX EST FIXÉ : 11h 45mn` ;
- le type officiel est dans la ligne `16 CONCURRENTS - 1ère COURSE - ATTELE`.

Le parseur lisait donc correctement les partants mais ne reconnaissait pas les métadonnées réelles. Le fournisseur automatique masquait en plus partiellement le défaut en réinjectant la date du lien LONAB, sans corriger le reste de l’en-tête.

## Fichiers corrigés

- `hyperion/ingestion.py` — lignes 44–78, 252–354 et 392–406 :
  - priorité à la date textuelle du titre LONAB ;
  - reconnaissance de l’en-tête réel hippodrome/nom ;
  - reconnaissance de la ligne de course et du type officiel ;
  - reconnaissance de `ARRÊT DES JEUX` ;
  - distance avec séparateur de milliers (`2 550 METRES`) ;
  - conservation de `None` quand aucun champ fiable n’est trouvé.
- `hyperion/ingestion.py` — lignes 74 et 335–406 : conservation du type
  officiel `ATTELE` dans `race_type`, afin que l’étape pipeline ne remplace
  pas la discipline reconnue par `unknown`.
- `hyperion/lonab_auto.py` — lignes 83–87 : élargissement du repli d’en-tête aux épreuves comme `GRAND NATIONAL DU TROT`.
- `tests/test_ingestion.py` — ajout d’une non-régression basée sur un extrait verbatim du PDF réel du 14/10/2026.

L’extraction des chevaux n’a pas été modifiée.

## Tests

- Avant correction, suite sur le commit parent : **285 tests passés**.
- Après correction : **286 tests passés**.
- PDF réel du 14/10/2026 : **16/16 chevaux** extraits.
- Cinq autres PDF réels validés : 13/10, 12/10, 11/10, 10/10 et 09/10/2026. Chacun fournit une date, un hippodrome, un nom, des horaires et 15 à 18 chevaux. Le 11/10 est correctement reconnu comme `obstacle` via `STEEPLE CHASE`.

Métadonnées obtenues pour le cas initial :

```text
date        = 2026-10-14
hippodrome  = REIMS
nom         = GRAND NATIONAL DU TROT
course      = 1
distance    = 2550 m
discipline  = trot_attele
clôture     = 11:45 UTC
départ      = 11:55 UTC
chevaux     = 16/16
```

## Run réel final

Les commits `20917d7` puis `6b10f5e` ont été poussés sur `main`, puis le workflow `workflow_dispatch` a été relancé en `mode=simulation`, sans `--input` ni fichier de course. Le run final a réussi :

- analyse automatique de la course du jour : **succès** ;
- évaluation du soir : **succès** ;
- exécution GitHub Actions finale : [run 38656155536](https://github.com/arthur12arthur/MEGASUS/actions/runs/38656155536).

Le JSON final retourne `20261014-reims-c1`, la date du jour et `discipline=trot_attele`. Le statut `is_late=true` est attendu car le run a été exécuté à 23h47, après la clôture réelle de 11h45 ; il n’est plus dû à la fausse date du 10/10/2026. Le faux « hors délai depuis quatre jours » a donc disparu.

## Correctif complémentaire — sources externes

Le message « aucune source externe exploitable » venait d’un défaut de câblage :
dans `cli.py`, les sources n’étaient chargées que si `--panel` était fourni.
Or le workflow, dans sa branche `--auto`, appelait `run --auto` sans `--panel`.
Le pipeline recevait donc toujours `external_picks=[]`. En outre, aucune
configuration d’URL n’existait dans `Settings`, alors que `HttpPanelProvider`
était déjà implémenté.

La correction fait désormais consulter automatiquement `HttpPanelProvider` dans
`--auto`, avec les URL déclarées dans `HYPERION_EXTERNAL_URLS` au format JSON.
Le workflow transmet aussi le fichier `data/panel_YYYY-MM-DD.json` lorsqu’il
existe, y compris dans le chemin automatique. Chaque URL absente, page bloquée
ou couverture insuffisante reste tracée dans `failed_sources` avec sa raison ;
aucun pronostic n’est inventé.

La suite de tests passe désormais à **287 tests**. La configuration des URL
réelles des sites de pronostics reste à renseigner dans le secret GitHub
`HYPERION_EXTERNAL_URLS`, car le dépôt ne doit pas deviner des URL de pages
quotidiennes ni fabriquer de classements.
