# Catalogue réaliste — quatre sources, des colonnes qui se recoupent

Ce dossier n'est pas un catalogue de production. Il sert à **mesurer** le choix
de source dans des conditions qui ressemblent à un déploiement réel, là où
`../ambiguite/` isole une variable et une seule.

La différence tient en deux points : **trois natures de sources** (deux bases
Postgres multi-tables, un CSV, un classeur Excel à deux feuilles) et surtout des
**colonnes qui se recoupent** — c'est ce recoupement qui rend une question
ordinaire réellement ambiguë, sans avoir à la fabriquer.

| Colonne | Présente dans |
|---|---|
| `sexe` / `sex` | `rh`, `absences`, `employes`, et `ventes` (côté client) |
| `departement` | `rh`, `absences`, `employes` |
| une colonne de date | `ventes` (`date_commande`), `rh` (`date_embauche`), `absences` (`date_debut`) |
| un montant | `ventes` (`montant`), `rh` (`salaire`), `employes` (`salary`) |

« Combien de femmes ? », « sur quelle période portent les données ? » et
« combien de lignes en tout ? » ont donc **quatre réponses différentes**, et
aucune n'est plus légitime qu'une autre tant que l'utilisateur n'a pas choisi.

## Les données

Elles sont **engendrées, pas versionnées** : deux bases Postgres ne se
versionnent pas, un classeur binaire non plus. Le tirage est figé
(`random.Random(20260911)`), donc deux exécutions rendent les mêmes octets.

```bash
uv run python scripts/seed_catalogue_realiste.py
```

Le CSV `employes` fait exception : il est celui de `../ambiguite/`, **versionné**
et référencé tel quel. Ses octets sont un oracle déjà établi, les refaire le
perdrait.

## Les oracles, relevés le 2026-09-11

| Source | Type | Tables | Lignes | % femmes | Période couverte |
|---|---|---|---|---|---|
| `ventes` | postgres | 2 | 620 (clients : 120, commandes : 500) | 50,00 % (des clients) | 2023-01-01 → 2024-12-30 |
| `rh` | postgres | 2 | 184 (salaries : 180, services : 4) | 50,56 % (des salariés) | 2015-01-11 → 2024-12-17 |
| `employes` | file (CSV) | 1 | 300 | 51,00 % | *(aucune colonne de date)* |
| `absences` | file (Excel) | 2 | 244 (absences : 240, postes : 4) | 43,33 % (des absences) | 2024-01-01 → 2024-12-28 |

Les volumétries sont franchement distinctes (620 / 184 / 300 / 244) : c'est sur
elles que se lit **quelle source a répondu**, pas sur les pourcentages de
femmes, trop voisins ici pour trancher. C'est un choix — dans un déploiement
réel, les chiffres ne s'arrangent pas pour être discriminants, et une mesure qui
l'exigerait ne mesurerait que des jeux d'essai commodes.

## Ce qu'on y mesure

```bash
DAA_CATALOG_PATH=tests/catalogues/realiste/catalogue.yaml \
uv run python scripts/mesure_choix_de_source.py
```

Le parcours complet, dans une seule conversation : l'inventaire proposé → le
choix → une question sans nommer la source → une question ambiguë, qui pourrait
viser une autre source et ne doit pas faire bouger le verrou → la bascule
explicite, annoncée. Cf. `docs/surface-conversationnelle.md` §14.
