# Ableton AutoMix V11

Application Windows en Python pour automatiser une première passe de mix dans Ableton Live.

## Fonctionnement

1. **Créer les stems + les analyser** : AutoMix ouvre le rendu de Live, demande les pistes individuelles, exporte les WAV dans `AutoMix_Projects` et mesure le niveau / spectre / stéréo.
2. **Créer le mix automatique** : première balance prudente gain/pan via Codex CLI si disponible, avec moteur local en secours.
3. **Rendre + vérifier** : nouvelle passe de rendu et contrôle objectif avant de préparer la passe suivante.

Le fichier `.als` original n'est jamais modifié.

## Correctif V11 pour l'étape 1

Si Windows ou Ableton empêche AutoMix d'ouvrir ou d'identifier **Export Audio/Vidéo**, le logiciel passe en veille au lieu d'abandonner.

Tu peux alors ouvrir toi-même la fenêtre d'export, et même cliquer toi-même sur **Exporter**. Dès que la fenêtre Windows **Enregistrer** apparaît, AutoMix la détecte, impose le dossier des stems et continue automatiquement.

## Installation

- Python 3.11+ recommandé.
- Lance `install.bat` une fois.
- Lance ensuite `RUN_AUTOMIX.bat`.
- `CODEX_LOGIN.bat` sert uniquement si tu veux utiliser Codex CLI.

## Mises à jour

La V11 est reliée à ce dépôt avec `update_config.json`.

Le bouton **Mises à jour** lit `latest.json`. Les futures versions pourront être publiées directement ici : AutoMix téléchargera les fichiers modifiés, les appliquera puis redémarrera sans demander de nouveau ZIP manuel.

Les données locales comme `AutoMix_Projects/`, `.last_project.txt` et les sauvegardes d'auto-réparation ne doivent pas être versionnées.
