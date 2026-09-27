"""Boucle agentique multi-étapes façon Claude Code, pilotée par Ollama.

Le modèle reçoit une liste d'outils (tools.TOOL_SCHEMAS) et peut en enchaîner
plusieurs appels d'affilée (explorer, lire, éditer, vérifier) avant de répondre
en langage naturel. On s'arrête dès que le modèle répond sans appel d'outil,
ou après max_steps itérations.
"""

import json
import re

import brain
from tools import TOOL_SCHEMAS, execute_tool


_TOOL_CALL_TAG_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)
_BARE_JSON_RE = re.compile(r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{.*?\}\s*\}', re.DOTALL)

# Nom d'outil -> premier paramètre requis, pour reconnaître les cas où le modèle
# "invente" un appel de fonction Python (ex: run_python("test_book.py")) au lieu
# d'utiliser le vrai tool-calling structuré. Observé notamment pour run_python,
# que certains modèles traitent comme un import fictif dans un bloc de code.
_TOOL_FIRST_PARAM = {
    schema['function']['name']: (schema['function']['parameters'].get('required') or [None])[0]
    for schema in TOOL_SCHEMAS
}
_FAKE_CALL_RE = re.compile(
    r'\b(' + '|'.join(re.escape(name) for name in _TOOL_FIRST_PARAM) + r')\(\s*["\']([^"\')]+)["\']\s*\)'
)


def _extract_fake_function_calls(content):
    """Reconnaît des appels façon `run_python("test_book.py")` écrits comme du code
    Python plutôt que comme de vrais appels d'outils, et les convertit en appels réels.
    Limité aux outils dont le premier argument est une simple chaîne (path/pattern).
    """
    calls = []
    for match in _FAKE_CALL_RE.finditer(content):
        name, value = match.group(1), match.group(2)
        param = _TOOL_FIRST_PARAM.get(name)
        if param:
            calls.append({'name': name, 'arguments': {param: value}})
    return calls


def _extract_fallback_tool_calls(content):
    """Filet de sécurité pour les petits modèles locaux qui produisent le JSON
    d'appel d'outil dans le texte au lieu du champ structuré tool_calls
    (ex: sans les balises <tool_call> attendues par le template Ollama).
    """
    if not content:
        return []

    calls = []
    for match in _TOOL_CALL_TAG_RE.finditer(content):
        try:
            calls.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    if calls:
        return calls

    stripped = content.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict) and 'name' in obj and 'arguments' in obj:
            return [obj]
    except json.JSONDecodeError:
        pass

    match = _BARE_JSON_RE.search(content)
    if match:
        try:
            return [json.loads(match.group(0))]
        except json.JSONDecodeError:
            pass

    return []


AGENT_SYSTEM_PROMPT = """Tu es un agent de développement autonome qui travaille dans le répertoire courant d'un projet, comme un assistant de code senior.

Tu disposes d'outils pour explorer (list_files, grep), lire (read_file), modifier le projet (edit_file, create_file, delete_symbol), et vérifier ton travail en l'exécutant (run_python). Utilise-les de façon autonome et enchaîne plusieurs appels si nécessaire pour accomplir la demande, sans t'arrêter à la première étape.

PRINCIPES :
1. Explore avant d'agir : si tu n'es pas sûr du fichier ou du contenu exact, utilise list_files/grep/read_file d'abord. Ne devine jamais le contenu d'un fichier.
2. Modifie avec précision : utilise edit_file avec old_string/new_string ciblant la plus petite portion suffisante, jamais de réécriture complète d'un fichier. old_string doit être copié exactement depuis un read_file précédent. Si edit_file échoue (old_string introuvable ou ambigu), relis le fichier et réessaie avec un old_string corrigé, ne renonce pas après un seul échec.
3. Vérifie réellement ton travail : si tu crées ou modifies un fichier de tests, ou un script autonome, exécute-le avec run_python pour confirmer qu'il fonctionne avant de conclure. Ne lance jamais run_python sur un serveur (Flask/Django app.run, boucle d'écoute, etc.).
4. Ne t'arrête que quand la tâche est vraiment terminée et vérifiée quand c'est possible, ou si une clarification est impossible à deviner (dans ce cas, explique ce qui manque au lieu d'utiliser un outil).
5. Réponds en français, de façon concise, uniquement quand la tâche est terminée ou bloquée. Ne réponds pas "je vais faire X", fais-le directement avec les outils.
"""


def _looks_like_unapplied_fix(content):
    """Détecte quand le modèle propose un correctif en texte/markdown au lieu de
    l'appliquer avec edit_file/create_file. Certains petits modèles locaux
    glissent vers ce mode narratif après plusieurs allers-retours d'outils.
    """
    if '```' not in content:
        return False
    return any(token in content for token in ('def ', 'class ', 'return ', 'import '))


_FILE_TOKEN_RE = re.compile(r'\b[\w./-]+\.(?:py|js|ts|json|md|txt)\b')

PLANNING_PROMPT_TEMPLATE = """Tu vas devoir créer ou modifier plusieurs fichiers liés entre eux pour cette demande :
{query}

Avant d'écrire le moindre code, établis un plan court en JSON avec ce schéma exact :
{{"files": [{{"path": "nom_fichier.py", "role": "à quoi il sert en une phrase", "imports_from": ["autre_fichier.py"]}}]}}

"imports_from" liste les AUTRES fichiers du plan dont celui-ci doit importer des classes/fonctions déjà définies
ailleurs, pour ne jamais les redéfinir en double. Liste vide si aucune dépendance.
Réponds en JSON uniquement, sans texte autour, sans balises markdown."""


def _needs_plan(query):
    """Heuristique : une tâche qui touche plusieurs fichiers explicitement nommés
    bénéficie d'un plan amont pour éviter que le modèle improvise des classes en
    double ou des imports incohérents entre fichiers.
    """
    return len(set(_FILE_TOKEN_RE.findall(query))) >= 2


def _plan_files(query, model):
    """Demande un plan (fichiers + rôle + dépendances) avant d'entrer dans la boucle
    d'outils. Best-effort : si le modèle ne répond pas en JSON exploitable, on
    abandonne silencieusement et l'agent démarre sans plan (comportement inchangé).
    """
    prompt = PLANNING_PROMPT_TEMPLATE.format(query=query)
    try:
        message = brain.chat_completion([{'role': 'user', 'content': prompt}], model=model, temperature=0.1)
        content = (message.get('content') or '').strip()
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.MULTILINE).strip()
        plan = json.loads(content)
        files = plan.get('files') or []
        if not files:
            return None
    except Exception:
        return None

    lines = ["PLAN À RESPECTER (établi avant d'écrire le code) :"]
    for entry in files:
        path = entry.get('path', '?')
        role = entry.get('role', '')
        deps = entry.get('imports_from') or []
        dep_note = f" — doit importer depuis : {', '.join(deps)}" if deps else ""
        lines.append(f"- {path} : {role}{dep_note}")
    lines.append(
        "Respecte ce plan strictement : ne redéfinis jamais dans un fichier une classe/fonction déjà définie "
        "dans un autre fichier du plan, importe-la à la place."
    )
    return '\n'.join(lines)


_STUCK_WINDOW = 6
_STUCK_THRESHOLD = 4


def _is_stuck(history):
    """Détecte un thrashing : le même outil appliqué au même fichier plusieurs fois
    de suite sans que la tâche progresse (ex: edit_file en boucle sur un fichier
    toujours en échec). Évite de brûler tout le budget d'étapes pour rien.
    """
    if len(history) < _STUCK_THRESHOLD:
        return False
    recent = history[-_STUCK_WINDOW:]
    last = history[-1]
    return recent.count(last) >= _STUCK_THRESHOLD


def run_agent(query, model=None, max_steps=40, on_tool=None):
    """Exécute la boucle agentique pour une requête utilisateur.

    on_tool(name, args) est appelé avant chaque exécution d'outil, utile pour
    afficher la progression à l'utilisateur en temps réel.
    Retourne le texte de la réponse finale du modèle.
    """
    messages = [{'role': 'system', 'content': AGENT_SYSTEM_PROMPT}]

    if _needs_plan(query):
        plan_digest = _plan_files(query, model or brain.AGENT_MODEL)
        if plan_digest:
            messages.append({'role': 'system', 'content': plan_digest})

    messages.append({'role': 'user', 'content': query})

    nudged = False
    tool_history = []
    for _ in range(max_steps):
        message = brain.chat_completion(messages, tools=TOOL_SCHEMAS, model=model or brain.AGENT_MODEL)
        tool_calls = message.get('tool_calls') or []

        if not tool_calls:
            content = (message.get('content') or '').strip()
            fallback_calls = _extract_fallback_tool_calls(content) or _extract_fake_function_calls(content)
            if fallback_calls:
                tool_calls = [
                    {'function': {'name': call.get('name'), 'arguments': call.get('arguments', {})}}
                    for call in fallback_calls
                ]
                message = {'role': 'assistant', 'tool_calls': tool_calls}
            elif not nudged and _looks_like_unapplied_fix(content):
                # Le modèle a décrit un correctif au lieu de l'appliquer : on le relance une fois.
                nudged = True
                messages.append({'role': 'assistant', 'content': content})
                messages.append({
                    'role': 'user',
                    'content': "N'explique pas le correctif, applique-le directement avec edit_file ou create_file sur le(s) fichier(s) concerné(s), maintenant.",
                })
                continue
            else:
                return content or "(le modèle n'a produit ni action ni réponse)"

        messages.append(message)

        for call in tool_calls:
            function = call.get('function', {}) or {}
            name = function.get('name')
            raw_args = function.get('arguments', {})
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args or {}

            if on_tool:
                on_tool(name, args)

            tool_history.append((name, args.get('path')))
            if _is_stuck(tool_history):
                stuck_path = tool_history[-1][1]
                return (
                    f"Je tourne en boucle sur '{stuck_path}' sans arriver à converger "
                    f"({_STUCK_THRESHOLD} tentatives similaires sans succès). J'arrête ici plutôt que de "
                    "continuer à l'aveugle : la tâche est probablement trop complexe pour être faite d'un "
                    "coup, ou ce fichier a besoin d'une relecture manuelle. Découpe la demande en étapes "
                    "plus petites, ou précise ce qui bloque."
                )

            result = execute_tool(name, args)
            messages.append({'role': 'tool', 'content': result})

    return "Nombre maximal d'étapes atteint sans conclusion claire. Essaie de reformuler ou de préciser la demande."
