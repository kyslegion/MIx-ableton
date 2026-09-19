# Ableton AutoMix V12

Application Windows en Python pour automatiser une première passe de mix dans Ableton Live.

## Fonctionnement

1. **Créer les stems + les analyser** : AutoMix tente l’export automatique via Ableton.
2. **Créer le mix automatique** : première balance prudente gain/pan via Codex CLI si disponible, avec moteur local en secours.
3. **Rendre + vérifier** : nouvelle passe de rendu et contrôle objectif avant de préparer la passe suivante.

Le fichier `.als` original n'est jamais modifié.

## V12 : plus de mode veille

Le système de veille ajouté dans les versions précédentes a été supprimé.

Si AutoMix n’arrive pas à détecter **Export Audio/Vidéo** ou la fenêtre **Enregistrer**, il s’arrête immédiatement au lieu d’attendre inutilement.

Tu peux alors :
- exporter les stems toi-même depuis Ableton ;
- cliquer sur **Importer des stems déjà exportés** ;
- choisir le dossier contenant les WAV/AIFF/FLAC ;
- AutoMix les analyse directement et te laisse passer à l’étape 2.

## Installation

- Python 3.11+ recommandé.
- Lance `install.bat` une fois.
- Lance ensuite `RUN_AUTOMIX.bat`.
- `CODEX_LOGIN.bat` sert uniquement si tu veux utiliser Codex CLI.

## Mises à jour

Le bouton **Mises à jour** lit `latest.json` sur ce dépôt. Depuis la V11, les nouvelles versions peuvent être récupérées directement depuis GitHub sans télécharger un nouveau ZIP manuellement.

Les données locales comme `AutoMix_Projects/`, `.last_project.txt` et les sauvegardes d'auto-réparation ne sont pas versionnées.
