ABLETON AUTOMIX V11

IMPORTANT - EMPLACEMENT DES FICHIERS
Tous les fichiers créés automatiquement par AutoMix restent dans le dossier du logiciel.
Ils sont rangés dans :

    Ableton_AutoMix_V11\AutoMix_Projects\<nom_du_projet_date>\

Chaque passe contient notamment ses stems dans pass_XX_stems.
La fenêtre Enregistrer d’Ableton reçoit un chemin absolu vers ce dossier : AutoMix ne doit donc pas utiliser Téléchargements, Documents ou le dernier dossier d’export de Live.

Le projet .als original n’est jamais modifié.

ABLETON AUTOMIX V5

Correctif principal V5 :
- ne confond plus le projet temporaire render_source_pass_XX.als avec la fenêtre Export Audio/Vidéo ;
- ferme les menus Ableton restés ouverts avant Ctrl+Maj+R ;
- coordonnées Live 12 recalées pour Mono/Normaliser/PCM/MP3/Vidéo et le bouton Exporter ;
- l’onglet Correction / aide de V4 reste disponible pour texte + captures + auto-réparation Codex.

ABLETON AUTOMIX V11 — MIX + AUTO-RÉPARATION
==========================================

Objectif
--------
Tu avances surtout avec les trois gros boutons :

1. CRÉER LES STEMS + LES ANALYSER
   - crée une copie temporaire du .als ;
   - ouvre Ableton Live ;
   - ouvre Export Audio/Vidéo ;
   - choisit les pistes individuelles ;
   - force un export WAV/PCM stéréo, sans normalisation, MP3 ni vidéo ;
   - exporte les stems dans le workspace ;
   - attend la fin puis analyse automatiquement les WAV.

2. CRÉER LE MIX AUTOMATIQUE
   - utilise Codex CLI s'il est disponible, sinon le moteur local ;
   - calcule une première balance gain/pan ;
   - crée AUTOMIX_PASS_01.als sans toucher à l'original.

3. RENDRE + VÉRIFIER + PRÉPARER LA PASSE SUIVANTE
   - rend les stems de la nouvelle passe ;
   - les réanalyse ;
   - compare la qualité mesurée ;
   - prépare la passe suivante si nécessaire.

NOUVEAU : onglet « Correction / aide »
--------------------------------------
Si AutoMix bloque sur ta version d'Ableton :
- l'erreur est automatiquement copiée dans cet onglet ;
- AutoMix essaie de capturer la fenêtre Ableton au moment de l'erreur ;
- tu peux écrire ce que tu observes ;
- tu peux cliquer « Capturer Ableton » ;
- tu peux coller une capture depuis le presse-papiers ;
- tu peux ajouter plusieurs PNG/JPG ;
- « AUTO-CORRIGER LE LOGICIEL AVEC CODEX » fait réparer le code directement.

La réparation est faite de façon isolée :
1. AutoMix copie uniquement son propre code dans un dossier de staging ;
2. Codex reçoit le texte, le log, le diagnostic Ableton et les captures avec entrée image ;
3. Codex modifie la copie, pas tes projets ;
4. AutoMix lance py_compile ;
5. si le code est valide, il sauvegarde la version précédente puis applique le correctif ;
6. par défaut, l'application redémarre automatiquement.

Le bouton « Annuler le dernier correctif » restaure la sauvegarde de code précédente.

Correctif Live 12 inclus dans V4
--------------------------------
Ta capture montrait que plusieurs contrôles de la fenêtre Export Audio/Vidéo sont dessinés par Live
et ne sont pas exposés correctement à Windows UI Automation.
La V4 ajoute donc des secours par coordonnées normalisées et lecture visuelle de la couleur On/Off.
Elle vise notamment à régler automatiquement :
- Toutes les pistes individuelles ;
- Convertir en mono : Off ;
- Normaliser : Off ;
- Encoder en PCM : On ;
- Encoder en MP3 : Off ;
- Créer vidéo : Off ;
- puis le bouton Exporter, même s'il n'est pas exposé comme un bouton Windows standard.

Installation
------------
1. Extraire le ZIP.
2. Double-cliquer install.bat une seule fois.
3. Si Codex n'est pas encore connecté, lancer CODEX_LOGIN.bat.
4. Double-cliquer RUN_AUTOMIX.bat.
5. Le projet test « 24-chorus chateau.als » est déjà inclus.

Sécurité
--------
- Le .als original n'est jamais modifié.
- Les passes sont créées sous AUTOMIX_PASS_XX.als.
- Les copies de rendu sont séparées.
- L'auto-réparation ne donne à Codex qu'une copie du code du logiciel, pas ton .als comme workspace éditable.
- Une sauvegarde du code est créée avant chaque correctif appliqué.
- Les changements de gain restent limités à ±4 dB par passe.
- Les panoramiques proposés restent limités à ±0,35.

Limites actuelles
-----------------
La V4 se concentre sur le rendu fiable des stems, leur analyse, puis la balance gain/pan.
L'automatisation d'EQ Eight, compression, de-essing et autres traitements viendra après stabilisation
complète du rendu automatique sur ta version de Live.



V9 - CORRECTIF ÉTAPE 1 / BOUTON EXPORTER
- ne dépend plus uniquement du point fixe (~50 % / 95,8 %) de la V7 ;
- cherche Export/Exporter dans tous les types de contrôles accessibles, pas seulement les boutons UIA ;
- si Annuler/Cancel est détecté, déduit la position d'Exporter juste à sa gauche ;
- ajoute plusieurs positions de secours prudentes et une activation clavier ;
- vérifie toujours que la fenêtre Enregistrer apparaît réellement ;
- en cas d'échec, enregistre automatiquement ableton_export_before_click.png et ableton_export_failed.png avec le diagnostic UI.

V7 - EXPORT 100 % AUTOMATIQUE
- Le clic sur le bouton Exporter de Live 12 a été recalibré sur la fenêtre française.
- AutoMix vérifie maintenant que la fenêtre Enregistrer apparaît réellement et retente sinon.
- La fenêtre Enregistrer est naviguée automatiquement vers AutoMix_Projects\<projet>\session_...\pass_XX_stems.
- Aucun choix manuel de dossier n'est nécessaire.


=== NOUVEAU V9 ===
- Si AutoMix n'arrive pas à cliquer sur Exporter, il passe en mode veille au lieu d'abandonner.
- Tu peux cliquer toi-même sur Exporter dans Ableton ; AutoMix détecte Enregistrer et reprend automatiquement.
- Veille manuelle par défaut : 30 minutes.
- Moteur d'auto-mise à jour intégré (automix/updater.py + update_worker.py).
- Les mises à jour préservent AutoMix_Projects, .last_project.txt et update_config.json.
- Le bouton « Mises à jour » utilise l'adresse manifest_url de update_config.json.
  Une fois cette adresse de publication configurée, les versions suivantes se téléchargent et s'installent depuis AutoMix.


=== NOUVEAU V10 ===
- L'absence de détection de la fenêtre Export Audio/Vidéo après Ctrl+Maj+R n'arrête plus l'étape 1.
- AutoMix passe alors en veille jusqu'à 30 minutes. Tu peux ouvrir Export Audio/Vidéo toi-même dans Ableton.
- La veille surveille à la fois la fenêtre Export Audio/Vidéo et la fenêtre Windows Enregistrer.
- Si tu vas jusqu'à cliquer toi-même sur Exporter, AutoMix détecte Enregistrer, impose le dossier des stems et reprend la suite automatiquement.
- Le délai automatique initial a été raccourci afin d'éviter une impression de blocage quand Windows refuse de donner le focus à Ableton.
