# Ableton AutoMix V13

Application Windows en Python pour automatiser une première passe de mix dans Ableton Live.

## Principe

- chaque piste Live est exportée comme un stem séparé ;
- si une piste contient un **Drum Rack**, AutoMix détecte ses chaînes actives et rend aussi chaque sous-instrument séparément : kick, snare, cymbale, hi-hat, clap, tom, etc. ;
- ces sous-instruments deviennent ensuite de vraies cibles de mix indépendantes dans AutoMix ;
- le fichier `.als` original n'est jamais modifié ni sauvegardé.

## Drum Rack

Live exporte normalement un Drum Rack comme la sortie de sa piste. La V13 ajoute donc une seconde étape automatique : pour chaque chaîne du Drum Rack, AutoMix crée une **copie de rendu jetable** du Set, coupe les autres chaînes, puis rend uniquement la piste parente. Le résultat est un fichier audio séparé pour chaque élément de batterie.

Cela évite d'altérer le projet d'origine et permet de conserver les instruments/effets de chaque chaîne.

## Fonctionnement

1. **Créer les stems + les analyser** : export des pistes Live, puis export individuel des sous-instruments de Drum Rack.
2. **Créer le mix automatique** : balance prudente gain/pan via Codex CLI si disponible, avec moteur local en secours.
3. **Rendre + vérifier** : nouvelle passe de rendu, y compris les sous-instruments de Drum Rack, puis contrôle objectif.

## Si l'automatisation Ableton échoue

Le mode veille a été supprimé. AutoMix s'arrête rapidement et tu peux utiliser **Importer des stems déjà exportés**. Si le projet contient un Drum Rack, le dossier manuel doit lui aussi contenir un fichier séparé pour chaque sous-instrument.

## Installation

- Python 3.11+ recommandé.
- Lance `install.bat` une fois.
- Lance ensuite `RUN_AUTOMIX.bat`.
- `CODEX_LOGIN.bat` sert uniquement si tu veux utiliser Codex CLI.

## Mises à jour

Le bouton **Mises à jour** lit `latest.json` sur ce dépôt. Les nouvelles versions sont récupérées directement depuis GitHub sans nouveau ZIP manuel.

Les données locales comme `AutoMix_Projects/`, `.last_project.txt` et les sauvegardes d'auto-réparation ne sont pas versionnées.
