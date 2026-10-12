"""La validation par le manager : installe ce que le module 2 fait à l'écran.

Prérequis : le module 1 installé (table « Congés », identifiant « Jeton Open WebUI », workflow
« Outils RH pour Open WebUI », connexion « n8n RH » et modèle « Assistant congés »), par exemple
avec l'installer.py du dépôt clic-par-clic-integration-n8n-01-assistant-mcp.

Côté Open WebUI (API, jeton administrateur OWUI_JETON) :
  1. le compte robot « Assistant RH » (rôle utilisateur, mot de passe tiré au hasard) ;
  2. le canal « validations-rh », ouvert en lecture et en écriture au manager (MANAGER_EMAIL) et au robot.
Côté n8n (API de l'éditeur, avec le compte N8N_EMAIL) :
  3. les identifiants « Jeton interne » (Header Auth X-Jeton-Interne) et « Robot Open WebUI » (Bearer, jeton du robot) ;
  4. le workflow « Validation des congés » (n8n/validation-des-conges.json), publié ;
  5. le serveur d'outils « Outils RH pour Open WebUI » remplacé par n8n/outils-rh.json (poser_conge
     devient un outil HTTP qui appelle le workflow de validation), publié.

Usage :
  python installer.py              # installe (ce qui existe déjà sous le même nom est remplacé)
  python installer.py --retirer    # retire le robot, le canal, les deux identifiants et le workflow de validation

Réglages : fichier .env (voir .env.example) ; les variables d'environnement l'emportent.
Aucune dépendance : Python 3.10 ou plus récent, bibliothèque standard seulement.
"""
import http.cookiejar
import json
import os
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid

ICI = pathlib.Path(__file__).resolve().parent
CLES = ("N8N_URL", "N8N_EMAIL", "N8N_MOT_DE_PASSE", "OWUI_URL", "OWUI_INTERNE", "OWUI_JETON", "MANAGER_EMAIL")
ROBOT = {"email": "assistant-rh@example.com", "name": "Assistant RH"}
CANAL = "validations-rh"
JETON_INTERNE = "Jeton interne"
JETON_ROBOT = "Robot Open WebUI"
JETON_MCP = "Jeton Open WebUI"
TABLE = "Congés"


def reglages() -> dict:
    """Lit .env (clé=valeur), puis l'environnement, qui l'emporte."""
    r = {}
    env = ICI / ".env"
    if env.exists():
        for ligne in env.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                cle, valeur = ligne.split("=", 1)
                r[cle.strip()] = valeur.strip()
    for cle in CLES:
        if os.environ.get(cle):
            r[cle] = os.environ[cle]
    return r


R = reglages()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows : guillemets et accents


# ---------------------------------------------------------------- Open WebUI : API

def owui(methode: str, chemin: str, corps=None, anonyme=False, tolerer=()):
    donnees = None if corps is None else json.dumps(corps).encode()
    entetes = {"Content-Type": "application/json"}
    if not anonyme:
        entetes["Authorization"] = f"Bearer {R['OWUI_JETON']}"
    req = urllib.request.Request(R["OWUI_URL"].rstrip("/") + chemin, data=donnees, method=methode, headers=entetes)
    try:
        with urllib.request.urlopen(req, timeout=30) as rep:
            texte = rep.read().decode()
    except urllib.error.HTTPError as e:
        if e.code in tolerer:
            return None
        sys.exit(f"Open WebUI {methode} {chemin} : {e.code} {e.read().decode()[:300]}")
    return json.loads(texte) if texte else None


def utilisateurs() -> list:
    return owui("GET", "/api/v1/users/?page=1")["users"]


def retirer_owui():
    for c in owui("GET", "/api/v1/channels/") or []:
        if c["name"] == CANAL:
            owui("DELETE", f"/api/v1/channels/{c['id']}/delete", tolerer=(400, 404))
            print(f"  Open WebUI : canal « {CANAL} » retiré")
    robot = next((u for u in utilisateurs() if u["email"] == ROBOT["email"]), None)
    if robot:
        owui("DELETE", f"/api/v1/users/{robot['id']}", tolerer=(400, 404))
        print(f"  Open WebUI : robot « {ROBOT['name']} » retiré")


def installer_owui() -> tuple[str, str]:
    """Robot et canal ; renvoie (jeton du robot, identifiant du canal)."""
    retirer_owui()
    mot_de_passe = secrets.token_hex(12) + "A1"  # jamais affiché : seul le jeton sert, rangé dans n8n
    robot = owui("POST", "/api/v1/auths/add", {**ROBOT, "password": mot_de_passe, "role": "user"})
    manager = next((u for u in utilisateurs() if u["email"] == R["MANAGER_EMAIL"]), None)
    if not manager:
        sys.exit(f"Open WebUI : aucun compte {R['MANAGER_EMAIL']} (MANAGER_EMAIL)")
    droits = [{"principal_type": "user", "principal_id": i, "permission": p} for i in (manager["id"], robot["id"]) for p in ("read", "write")]
    canal = owui("POST", "/api/v1/channels/create", {"name": CANAL, "description": "Demandes de congé à valider", "access_grants": droits})
    jeton = owui("POST", "/api/v1/auths/signin", {"email": ROBOT["email"], "password": mot_de_passe}, anonyme=True)["token"]
    print(f"  Open WebUI : robot « {ROBOT['name']} » et canal « {CANAL} » (lecture et écriture : {R['MANAGER_EMAIL']} et le robot)")
    return jeton, canal["id"]


# ---------------------------------------------------------------- n8n : API de l'éditeur (session par cookie)

class N8n:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.navigateur = str(uuid.uuid4())  # n8n lie la session à cet identifiant
        self.ouvreur = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def appel(self, methode: str, chemin: str, corps=None, ignorer404=False, conflit=False, deja=False):
        donnees = None if corps is None else json.dumps(corps).encode()
        req = urllib.request.Request(self.url + chemin, data=donnees, method=methode,
                                     headers={"Content-Type": "application/json", "browser-id": self.navigateur})
        try:
            with self.ouvreur.open(req, timeout=30) as rep:
                texte = rep.read().decode()
        except urllib.error.HTTPError as e:
            if ignorer404 and e.code == 404:
                return None
            if deja and e.code == 400:  # déjà dépublié, déjà archivé
                return None
            if conflit and e.code == 409:
                return "conflit"
            sys.exit(f"n8n {methode} {chemin} : {e.code} {e.read().decode()[:300]}")
        if not texte:
            return None
        j = json.loads(texte)
        return j.get("data", j) if isinstance(j, dict) else j

    def connexion(self, email: str, mot_de_passe: str):
        self.appel("POST", "/rest/login", {"emailOrLdapLoginId": email, "password": mot_de_passe})
        projets = self.appel("GET", "/rest/projects/my-projects")
        self.projet = next(p["id"] for p in projets if p["type"] == "personal")

    def workflows(self, nom: str) -> list:
        return [w for w in self.appel("GET", "/rest/workflows") or [] if w["name"] == nom]

    def supprimer_workflow(self, w):
        self.appel("POST", f"/rest/workflows/{w['id']}/deactivate", ignorer404=True, deja=True)
        self.appel("POST", f"/rest/workflows/{w['id']}/archive", ignorer404=True, deja=True)
        # Juste après la dépublication, n8n répond 409 (« still being unpublished ») : on réessaie.
        for _ in range(20):
            if self.appel("DELETE", f"/rest/workflows/{w['id']}", ignorer404=True, conflit=True) != "conflit":
                return
            time.sleep(1)
        sys.exit(f"n8n : le workflow « {w['name']} » ne se laisse pas supprimer")

    def identifiant(self, nom: str):
        return next((c for c in self.appel("GET", "/rest/credentials") or [] if c["name"] == nom), None)

    def table(self):
        tables = self.appel("GET", f"/rest/projects/{self.projet}/data-tables") or {}
        liste = tables.get("data", tables) if isinstance(tables, dict) else tables
        return next((t for t in liste if t["name"] == TABLE), None)

    def creer_et_publier(self, workflow: dict):
        wf = self.appel("POST", "/rest/workflows", {k: workflow[k] for k in ("name", "nodes", "connections", "settings")})
        self.appel("POST", f"/rest/workflows/{wf['id']}/activate", {"versionId": wf["versionId"]})
        return wf


def gabarit(fichier: str, valeurs: dict) -> dict:
    """Lit un workflow du dépôt et remplace ses ${VARIABLE} (identifiants propres à votre instance)."""
    texte = (ICI / "n8n" / fichier).read_text(encoding="utf-8")
    for cle, valeur in valeurs.items():
        texte = texte.replace("${" + cle + "}", json.dumps(valeur)[1:-1])
    return json.loads(texte)


def retirer_n8n(n8n: N8n):
    nom = json.loads((ICI / "n8n" / "validation-des-conges.json").read_text(encoding="utf-8"))["name"]
    for w in n8n.workflows(nom):
        n8n.supprimer_workflow(w)
        print(f"  n8n : workflow « {nom} » retiré")
    for nom in (JETON_INTERNE, JETON_ROBOT):
        c = n8n.identifiant(nom)
        if c:
            n8n.appel("DELETE", f"/rest/credentials/{c['id']}", ignorer404=True)
            print(f"  n8n : identifiant « {nom} » retiré")


def installer_n8n(n8n: N8n, jeton_robot: str, canal: str):
    table, mcp = n8n.table(), n8n.identifiant(JETON_MCP)
    if not table or not mcp:
        sys.exit("n8n : table « Congés » ou identifiant « Jeton Open WebUI » absent : installez d'abord le module 1.")
    retirer_n8n(n8n)
    # 1. Identifiants : les valeurs ne sont écrites nulle part ailleurs que dans n8n.
    interne = n8n.appel("POST", "/rest/credentials", {"name": JETON_INTERNE, "type": "httpHeaderAuth",
                                                      "data": {"name": "X-Jeton-Interne", "value": secrets.token_hex(16)}})
    robot = n8n.appel("POST", "/rest/credentials", {"name": JETON_ROBOT, "type": "httpBearerAuth", "data": {"token": jeton_robot}})
    valeurs = {"ID_JETON_INTERNE": interne["id"], "ID_ROBOT": robot["id"], "ID_IDENTIFIANT": mcp["id"], "ID_TABLE": table["id"],
               "ID_CANAL": canal, "OWUI_INTERNE": R["OWUI_INTERNE"].rstrip("/")}
    # 2. Workflow de validation, publié : son adresse web de production répond.
    validation = gabarit("validation-des-conges.json", valeurs)
    n8n.creer_et_publier(validation)
    print(f"  n8n : identifiants « {JETON_INTERNE} », « {JETON_ROBOT} » et workflow « {validation['name']} » publié")
    # 3. Serveur d'outils remplacé : poser_conge appelle le workflow de validation.
    outils = gabarit("outils-rh.json", valeurs)
    for w in n8n.workflows(outils["name"]):
        n8n.supprimer_workflow(w)
    n8n.creer_et_publier(outils)
    print(f"  n8n : serveur d'outils « {outils['name']} » republié (poser_conge appelle le workflow de validation)")


def main():
    manque = [c for c in CLES if not R.get(c)]
    if manque:
        sys.exit(f"Réglages manquants (.env) : {', '.join(manque)}")
    n8n = N8n(R["N8N_URL"])
    n8n.connexion(R["N8N_EMAIL"], R["N8N_MOT_DE_PASSE"])
    if "--retirer" in sys.argv:
        retirer_n8n(n8n)
        retirer_owui()
        print("Le serveur d'outils garde sa version du module 2 : relancez l'installer.py du module 1 pour revenir à l'insertion directe.")
        return
    jeton_robot, canal = installer_owui()
    installer_n8n(n8n, jeton_robot, canal)
    print("Terminé : demandez à « Assistant congés » de poser des congés, puis ouvrez le canal « validations-rh » avec le compte du manager.")


if __name__ == "__main__":
    main()
