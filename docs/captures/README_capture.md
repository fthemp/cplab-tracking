# Capture Wireshark factice – CP Lab 400 (MQTT en clair)

Fichiers fournis pour la Partie B du LAB Sécurité MQTT (GI-CNAM 4). La capture est **fabriquée pour l'enseignement** : les trames sont construites à la main mais parfaitement valides, Wireshark les décode comme du vrai trafic MQTT sur TCP/1883.

## Fichiers

- `cplab_mqtt_clair.pcapng` — capture à ouvrir dans Wireshark (format pcapng).
- `cplab_mqtt_clair.pcap` — même capture au format pcap classique (compatibilité).
- `wireshark_publish.png` — copie d'écran : une trame **Publish** décodée (topic `cplab/prod/st2/count` + JSON en clair), topic surligné en jaune, charge utile en vert.
- `wireshark_connect.png` — copie d'écran : une trame **Connect** où le **nom d'utilisateur** et le **mot de passe** apparaissent en clair.

## Contenu de la capture (21 trames MQTT)

| Trames | Scénario |
| --- | --- |
| 1–16 | Tab5 `prod-st2` : connexion, abonnements, publication du compteur `cplab/prod/st2/count` (retained) et du heartbeat |
| 21–27 | Tab5 `maint-m3` : connexion puis publication d'un état `ABNORMAL` sur `cplab/maint/m3/state` |
| 32–36 | Node-RED : publication des statistiques `cplab/maint/m3/stats` (pannes, MTTR, plan) |
| 38–47 | Démonstration : un client s'authentifie (`webadmin` / `Festo2026!`) → identifiants visibles dans le CONNECT |
| 52–56 | Attaque : injection de `{"cmd":"reset"}` sur `cplab/cmd/prod/action`, sans aucune authentification |

## Filtres Wireshark utiles

- `mqtt` — toutes les trames MQTT
- `mqtt.msgtype == 1` — les CONNECT (voir les identifiants)
- `mqtt.msgtype == 3` — les PUBLISH (voir topics et JSON)
- `mqtt.topic contains "maint"` — le trafic de maintenance
- clic droit sur une trame MQTT → **Follow > TCP Stream** pour suivre un échange complet

## Exercice

1. Ouvrir `cplab_mqtt_clair.pcapng`, appliquer le filtre `mqtt`.
2. Sélectionner la trame `Publish Message [cplab/prod/st2/count]`, déplier **MQ Telemetry Transport** et lire le topic et le JSON (comparer avec `wireshark_publish.png`).
3. Appliquer `mqtt.msgtype == 1`, ouvrir le CONNECT du client `mqtt-explorer`, relever User Name et Password (comparer avec `wireshark_connect.png`).
4. Conclure : sur le port 1883, rien n'est chiffré, même les mots de passe → passer à MQTTS (Partie D).

> Les identifiants et adresses de cette capture sont fictifs et n'existent que pour le TP.
