# La validation par le manager — n8n, nœud Wait, formulaire et canal Open WebUI

Workflows et script d'installation de la démonstration du module 2 (dernier module) de la série
« Clic par clic » **n8n et Open WebUI**.

Vidéo : {{video}}

## But

L'assistant du module 1 pose les congés de l'utilisateur, mais personne ne les valide. Ici, le
**manager entre dans la boucle** :

1. l'utilisateur demande ses congés à l'assistant, dans Open WebUI ;
2. l'outil `poser_conge` appelle le workflow « Validation des congés » par une **adresse web
   (Webhook) à réponse immédiate** : l'assistant répond aussitôt, la conversation n'attend pas ;
3. le workflow enregistre la demande « En attente » dans la table « Congés » ;
4. un **compte robot** d'Open WebUI publie la demande dans le canal `validations-rh`, avec le lien
   du formulaire de décision (`$execution.resumeFormUrl`) ;
5. le workflow **se met en pause** (nœud Wait, « On Form Submitted ») ;
6. le manager ouvre le lien, choisit « Validé » ou « Refusé » : le workflow **reprend** et met la
   ligne de la table à jour.

```
Camille ─▶ Assistant congés ─MCP─▶ poser_conge (outil HTTP)
                                       │ POST /webhook/demande-conge  (réponse immédiate)
                                       ▼
          Validation des congés : Demande reçue ─▶ Enregistrer la demande ─▶ Prévenir le manager ─▶ Décision du manager (pause) ─▶ Mettre à jour le statut
                                                        │                           │                        ▲
                                                 table « Congés »          canal validations-rh ─▶ manager ─┘ formulaire
```

## Pourquoi une adresse web, et pas « Call n8n Workflow Tool » ?

Un outil attend la fin de ce qu'il lance. Avec un sous-workflow appelé directement, la
conversation resterait bloquée jusqu'à la décision du manager, qui peut prendre des heures. Le
nœud Webhook en mode « Immediately » (`responseMode: onReceived`) répond `Workflow was started`
en une fraction de seconde, puis le workflow continue seul.

## Contenu

| Fichier | Rôle |
|---|---|
| `n8n/validation-des-conges.json` | Le workflow « Validation des congés », à l'état montré en fin de vidéo : **Demande reçue** (Webhook POST `demande-conge`, Header Auth « Jeton interne », réponse immédiate), **Enregistrer la demande** (insertion, statut imposé « En attente »), **Prévenir le manager** (HTTP Request vers `/api/v1/channels/<canal>/messages/post` d'Open WebUI, jeton du robot, lien `$execution.resumeFormUrl`), **Décision du manager** (Wait, formulaire « Demande de congé » à un champ « Décision »), **Mettre à jour le statut** (mise à jour de la ligne retrouvée par son `id`). Les identifiants propres à votre instance sont remplacés par `${ID_…}`, `${OWUI_INTERNE}` |
| `n8n/outils-rh.json` | Le serveur d'outils « Outils RH pour Open WebUI » du module 1, où `poser_conge` est devenu un **HTTP Request Tool** : `POST http://localhost:5678/webhook/demande-conge` (n8n s'appelle lui-même, dans son conteneur), jeton interne, corps `email` (en-tête `X-Utilisateur-Email`), `debut`, `fin`, `jours` (`$fromAI`) |
| `installer.py` | Tout ce qui précède, par les API de n8n et d'Open WebUI (`--retirer` pour revenir en arrière) |

Les valeurs par défaut que n8n n'exporte pas (mode de réponse du Webhook, colonne `id` du filtre de
mise à jour) sont écrites en clair : l'import les accepte.

## Prérequis

- Le **module 1** installé : table « Congés », identifiant « Jeton Open WebUI », workflow « Outils
  RH pour Open WebUI », connexion « n8n RH » et modèle « Assistant congés » (dépôt
  [`clic-par-clic-integration-n8n-01-assistant-mcp`](https://github.com/guilhem-/clic-par-clic-integration-n8n-01-assistant-mcp)).
- **n8n 2.x** (tourné sur 2.41.6, édition Community), un compte membre suffit.
- **Open WebUI 0.11** (tourné sur 0.11.3), un compte administrateur et son jeton ; un compte pour
  le manager.
- Les deux logiciels dans le même cluster (ou le même réseau Docker) : n8n joint Open WebUI par
  son service (`http://open-webui.<espace>.svc.cluster.local:8080`), jamais par `localhost`.
- Python 3.10 ou plus récent pour `installer.py` (bibliothèque standard seulement).

## Installation pas à pas

### À la main, comme dans la vidéo

**Côté Open WebUI (administrateur)**

1. **Administration › Utilisateurs › +** : compte « Assistant RH » (`assistant-rh@example.com`), rôle
   **utilisateur**, un mot de passe long. Connectez-vous avec ce compte et récupérez son jeton
   (Réglages › Compte), ou passez par `POST /api/v1/auths/signin`.
2. **Canaux › +** : canal `validations-rh`, accès en **lecture et écriture** pour le manager et le
   robot (constaté sur Open WebUI 0.11.3 : la lecture seule ne suffit pas au manager).

**Côté n8n**

3. **Credentials** : « Jeton interne » (Header Auth, nom `X-Jeton-Interne`, une valeur longue et
   aléatoire) et « Robot Open WebUI » (Bearer Auth, le jeton du robot).
4. **Import from file** : `n8n/validation-des-conges.json` ; dans chaque nœud, choisissez
   l'identifiant et la table ; dans « Prévenir le manager », mettez l'adresse interne d'Open WebUI et
   l'identifiant du canal (visible dans l'adresse du canal). **Publish**.
5. Dans « Outils RH pour Open WebUI », supprimez l'outil `poser_conge`, ajoutez sous **Tools** un
   **HTTP Request Tool** nommé `poser_conge` : POST, URL `http://localhost:5678/webhook/demande-conge`,
   Authentication « Generic Credential Type » › « Header Auth » › « Jeton interne » ; Send Body,
   paramètres `email` = `{{ $json.headers['x-utilisateur-email'] }}`, `debut`, `fin`, `jours` =
   `{{ $fromAI('debut', 'Premier jour, AAAA-MM-JJ') }}`… ; description : « Transmet une demande de
   congé de l'utilisateur connecté à son manager, qui la validera. » **Publish**.

### Par les API (script)

```bash
cp .env.example .env      # N8N_URL, N8N_EMAIL, N8N_MOT_DE_PASSE, OWUI_URL, OWUI_JETON, OWUI_INTERNE, MANAGER_EMAIL
python installer.py       # robot, canal, identifiants, workflow de validation publié, serveur d'outils republié
python installer.py --retirer
```

Le mot de passe du robot est tiré au hasard et n'est jamais affiché : seul son jeton sert, rangé
dans l'identifiant n8n « Robot Open WebUI ».

## Essai

1. Avec le compte d'un utilisateur, « Assistant congés » : « Pose-moi du lundi 21 au mercredi 23
   décembre. » L'outil `rh_poser_conge` répond aussitôt : demande transmise au manager.
2. Avec le compte du manager, canal `validations-rh` : le message d'« Assistant RH » et son lien
   « Valider ou refuser ».
3. Le lien ouvre le formulaire de n8n : « Validé », **Submit**.
4. De retour chez l'utilisateur : « Où en est ma demande de décembre ? » : validée.
5. Côté n8n, **Executions** du workflow de validation : l'exécution, en pause pendant la décision,
   est allée jusqu'au bout.

Vérifié (12 octobre 2026) : l'appel MCP de `poser_conge` répond en 0,13 s ; le message du robot
arrive dans le canal ; l'envoi du formulaire passe la ligne à « Validé ».

## Sécurité

- Aucun jeton ni mot de passe dans ce dépôt (`.env` est ignoré par git) ; les exports ne contiennent
  que les **noms** des identifiants.
- L'adresse web du workflow exige l'en-tête `X-Jeton-Interne` : seul le serveur d'outils la connaît.
- Le lien du formulaire est **signé** et propre à une exécution : il ne permet pas de décider d'une
  autre demande.
- Le robot est un compte **utilisateur** ordinaire, sans droits d'administration ; l'utilisateur qui
  demande ne voit pas le canal.

## Série

- Module précédent : **Un assistant Open WebUI qui agit dans n8n**
  ([`clic-par-clic-integration-n8n-01-assistant-mcp`](https://github.com/guilhem-/clic-par-clic-integration-n8n-01-assistant-mcp)).
- C'est le dernier module de la série. Pour aller plus loin : la série « Clic par clic » **Créer un
  service d'IA : le studio 3D**.

## Licence

MIT (voir `LICENSE`).
