
import ast
import json
import os
import re
import shutil
import urllib.error
import urllib.request


OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
_PREFERRED_MODELS = ['qwen2.5-coder:7b', 'qwen2.5-coder:3b', 'deepseek-coder:6.7b',
                     'deepseek-coder:latest', 'llama3.1:8b', 'llama3:8b', 'mistral:instruct']

def _resolve_default_model():
    env = os.environ.get('SELF_DEV_AGENT_MODEL', '')
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=3) as r:
            models = [m['name'] for m in json.loads(r.read()).get('models', [])]
        if env and env in models:
            return env
        for pref in _PREFERRED_MODELS:
            if pref in models:
                return pref
        return models[0] if models else 'qwen2.5-coder:7b'
    except Exception:
        return env or 'qwen2.5-coder:7b'

DEFAULT_MODEL = _resolve_default_model()

ROUTER_SYSTEM_PROMPT = """Tu es le cerveau décisionnel d'un assistant de développement. Ton rôle est de COMPRENDRE ce que l'utilisateur veut VRAIMENT, même quand c'est mal formulé, incomplet, ou informel.

PRINCIPE FONDAMENTAL : L'utilisateur ne sait pas comment formuler exactement sa demande. Ta job est de déduire son INTENTION REELLE, pas de prendre ses mots au pied de la lettre.

Exemples de traduction intentionnelle (ce que l'utilisateur dit -> ce qu'il veut vraiment) :

1. "il y a quoi dans main.py ?"
   -> intention: voir le contenu, comprendre ce que fait ce fichier
   -> action: read, file: main.py

2. "ajoute une fonction hello" (sans dire où)
   -> intention: ajouter à un fichier logique, probablement le fichier principal
   -> action: append, nécessite d'identifier le bon fichier

3. "supprime la fonction calculate dans utils.py"
   -> intention: supprimer précisément cette fonction
   -> action: delete, file: utils.py, target: calculate

4. "modifie le fichier d'authentification pour mieux gérer les erreurs"
   -> intention: modifier le fichier qui gère l'auth (peut être auth.py, login.py, etc.)
   -> action: edit, file: (trouver le bon), instruction: améliorer gestion erreurs

5. "corrige le bug dans le parser"
   -> intention: trouver et corriger le code de parsing qui a un problème
   -> action: edit, file: (trouver parser), instruction: corriger le bug

6. "ajoute la validation dans le formulaire de contact"
   -> intention: ajouter de la validation d'entrée somewhere dans le code de formulaire
   -> action: edit, file: (trouver form/contact), instruction: ajouter validation

7. "déplace la fonction getUser vers auth.py"
   -> intention: refactorer en déplaçant du code entre fichiers
   -> action: edit, instruction: déplacer getUser vers auth.py

8. "explique-moi ce que fait ce projet"
   -> intention: avoir un résumé conversationnel du projet entier
   -> action: answer, answer: résumé du projet

9. "où est la logique qui gère les erreurs ?"
   -> intention: localiser le code de gestion d'erreurs
   -> action: locate, target: gestion erreur

10. "rajoute-moi un try-catch dans process()"
    -> intention: ajouter gestion d'erreurs autour de la fonction process
    -> action: edit, file: (auto-détecter), instruction: ajouter try-catch autour de process()

11. "vire la fonction foo" (familier)
    -> intention: supprimer la fonction foo
    -> action: delete, target: foo (inférer le fichier depuis le contexte)

12. "change le timeout de 30 à 60"
    -> intention: trouver et modifier une valeur de configuration
    -> action: edit, instruction: changer timeout 30->60

13. "fais en sorte que le login marche avec google"
    -> intention: ajouter authentification Google OAuth
    -> action: answer/investigate d'abord, puis edit

14. "clean le code de main.py"
    -> intention: refactorer/nettoyer le code de main.py
    -> action: edit, file: main.py, instruction: nettoyer/refactorer le code

15. "ajoute des commentaires dans data.py"
    -> intention: ajouter de la documentation au fichier
    -> action: edit, file: data.py, instruction: ajouter commentaires pertinents

RÈGLES DE RAISONNEMENT :
- Si l'utilisateur mentionne une action floue ("corrige", "améliore", "ajoute"), demande-toi QUOI exactly corriger/améliorer/ajouter
- Si l'utilisateur mentionne un concept sans fichier ("le système d'auth", "la logique de parsing"), trouve le fichier qui correspond
- Si l'utilisateur demande quelque chose de vague ("nettoie le bordel", "répare ça"), cible le problème evident
- Contexte précédent compte : si quelqu'un parle de "ce fichier" puis "supprime la fonction", c'est le même fichier

Pour les cas VRAIMENT ambigus (genre "fait quelque chose"), retourne action=ask avec reason claire.
Pour les cas semi-clairs, fait de ton mieux avec les infos disponibles."""

CODE_SYSTEM_PROMPT = """Tu es un générateur de code expert. L'utilisateur te donne une instruction et le contexte du fichier à modifier. Ta job est de produire le code EXACT qui implémente cette instruction.

RÈGLES CRITICALES :
1. COMPRENDS l'intention, pas juste les mots. "ajoute une fonction" = crée une fonction pertinente, pas juste un stub.
2. RESPECTE le style existant du fichier (mêmes conventions, mêmes patterns)
3. Si tu ajoutes une fonction, donne-lui un nom pertinent et cohérent avec le contexte
4. NE REMPLACE pas le contenu existant sans raison explicite
5. Pour append: produit SEULEMENT le nouveau code à ajouter, pas de wrapper
6. Pour edit: produit le code COMPLET du fichier modifié
7. Élimine les backticks, markdown, ou toute décoration
8. Le code doit être syntaxiquement correct et compilable

Contexte: tu modifies du code réel dans un projet réel. Sois précis, pertinent, utile."""

DELETE_SYSTEM_PROMPT = """Tu es un expert en suppression de code Python. Détermine QUOI exactement supprimer et OÙ.

RÈGLES :
1. Identifie le symbole exact (fonction, classe, méthode) à supprimer
2. Utilise la ligne de commande "Détermine: je veux supprimer [X] dans [Y]"
3. Si ambigu, demande clarification
4. Retourne JSON: {"action": "delete", "target": "nom_du_symbole", "file": "fichier.py", "reason": "..."} ou {"action": "ask", "reason": "..."}
5. Vérifie que le symbole existe vraiment avant de confirmer"""

SUMMARIZE_SYSTEM_PROMPT = """Tu es un analyste de code. Résume ce fichier de manière UTILE pour un développeur qui doit travailler dessus.

Structure ta réponse ainsi:
- Rôle: ce que fait ce fichier (1 phrase)
- Sections: liste des fonctions/classes principales avec leur rôle
- Points clés:choses importantes à savoir (gotchas, patterns, dépendances)

Sois concis mais exhaustif."""


def project_root():
    return os.path.abspath(os.getcwd())


def index_path():
    return os.path.join(project_root(), '.knowledge_base', 'file_index.json')


def load_index():
    path = index_path()
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as file_handle:
        return json.load(file_handle)


def normalize_path_hint(file_hint):
    return file_hint.replace('\\', '/').strip().lower().lstrip('./')


def find_file_candidates(file_hint):
    root = project_root()
    hint = normalize_path_hint(file_hint)
    if not hint:
        return []

    candidates = []
    direct_path = os.path.join(root, file_hint)
    if os.path.isfile(direct_path):
        candidates.append(os.path.relpath(direct_path, root))

    for rel_path in load_index():
        rel_norm = rel_path.replace('\\', '/').lower()
        base_norm = os.path.basename(rel_norm)
        if hint == rel_norm or hint == base_norm:
            candidates.append(rel_path)
        elif rel_norm.endswith('/' + hint):
            candidates.append(rel_path)
        # Recherche floue: mots-clés dans le chemin
        elif fuzzy_match(hint, base_norm):
            candidates.append(rel_path)

    # Recherche dans les fichiers SELF_DEV_AGENT aussi
    tool_root = os.path.dirname(os.path.abspath(__file__))
    for dirpath, _, filenames in os.walk(tool_root):
        for filename in filenames:
            if filename.lower() == hint or filename.lower().endswith('/' + hint):
                rel_path = os.path.relpath(os.path.join(dirpath, filename), root)
                candidates.append(rel_path)

    unique = []
    seen = set()
    for candidate in candidates:
        key = candidate.replace('\\', '/').lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def fuzzy_match(hint, filename):
    """匹配 floue entre un hint et un nom de fichier.

    Par exemple: 'config' correspond à 'config.py', 'settings.py', 'app_config.py'
    """
    hint_clean = hint.replace('_', '').replace('-', '').lower()
    filename_clean = filename.replace('_', '').replace('-', '').replace('.py', '').lower()

    # Mot exact dans le nom
    if hint_clean in filename_clean:
        return True

    # Tous les mots du hint sont dans le filename
    hint_words = hint_clean.split()
    if all(word in filename_clean for word in hint_words):
        return True

    # Matching partiel (au moins 60% de similarité)
    if len(hint_clean) >= 3 and len(filename_clean) >= 3:
        similarity = sum(1 for c in hint_clean if c in filename_clean) / len(hint_clean)
        if similarity >= 0.6:
            return True

    return False


def find_best_file_for_concept(concept, model=None):
    """Trouve le fichier le plus pertinent pour un concept vague.

    Par exemple: 'auth' -> auth.py, login.py, etc.
    """
    index = load_index()
    if not index:
        return None, []

    # Construction du prompt pour Ollama
    file_list = '\n'.join(f"- {f}" for f in index if not f.startswith('.'))

    prompt = (
        f"Trouve le fichier le plus pertinent pour ce concept: '{concept}'\n\n"
        f"Fichiers disponibles:\n{file_list}\n\n"
        "Retourne JSON: {\"file\": \"chemin/vers/fichier.py\", \"reason\": \"pourquoi ce fichier\"}\n"
        "Si aucun fichier ne correspond, retourne {\"file\": null, \"reason\": \"...\"}"
    )

    try:
        raw = ask_ollama_with_options(prompt, model=model, response_format='json', temperature=0.2).strip()
        result = json.loads(raw)
        if result.get('file'):
            return result['file'], find_file_candidates(result['file'])
    except Exception:
        pass

    # Fallback: recherche par mots-clés
    keywords = concept.lower().split()
    candidates = []
    for path in index:
        path_lower = path.lower()
        if any(kw in path_lower for kw in keywords):
            candidates.append(path)

    return candidates[0] if candidates else None, candidates


def resolve_file_path(file_hint):
    candidates = find_file_candidates(file_hint)
    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_file_path_with_candidates(file_hint):
    candidates = find_file_candidates(file_hint)
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def ask_ollama(prompt, model=None, response_format=None):
    return ask_ollama_with_options(prompt, model=model, response_format=response_format)


def ask_ollama_with_options(prompt, model=None, response_format=None, temperature=0.2):
    payload = {
        'model': model or DEFAULT_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
        'options': {'temperature': temperature},
    }
    if response_format is not None:
        payload['format'] = response_format
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(OLLAMA_URL, data=data, headers={'Content-Type': 'application/json'})

    try:
        with urllib.request.urlopen(request) as response:
            response_json = json.loads(response.read().decode('utf-8'))
            return response_json.get('message', {}).get('content', '')
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Erreur HTTP Ollama: {error.code} {error.reason}') from error
    except urllib.error.URLError as error:
        raise RuntimeError(f'Erreur de connexion Ollama: {error.reason}') from error


def clean_code_output(text):
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else ''
    if cleaned.endswith('```'):
        cleaned = cleaned.rsplit('```', 1)[0].rstrip()
    cleaned = re.sub(r'^```[a-zA-Z0-9_+-]*\s*\n', '', cleaned)
    cleaned = re.sub(r'\n```\s*$', '', cleaned)
    return cleaned.strip() + ('\n' if cleaned and not cleaned.endswith('\n') else '')


def generate_code_block(instruction, file_path=None, model=None):
    """Génère un bloc de code pour une instruction donnée.

    Améliore le contexte en incluant plus d'informations sur le fichier cible.
    """
    context_parts = []

    # Charger le contexte du fichier si disponible
    if file_path:
        resolved = resolve_file_path(file_path)
        if resolved:
            abs_path = os.path.join(project_root(), resolved)

            # Lire le début et la fin du fichier
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()

                total_lines = len(lines)
                if total_lines <= 50:
                    full_content = ''.join(lines)
                    context_parts.append(f"Fichier: {resolved}\nContenu complet:\n{full_content}")
                else:
                    # Début + fin
                    start = ''.join(lines[:30])
                    end = ''.join(lines[-20:])
                    context_parts.append(f"Fichier: {resolved} ({total_lines} lignes)\n")
                    context_parts.append(f"--- Début ---:\n{start}")
                    context_parts.append(f"\n--- Fin ---:\n{end}")

                # Lister les fonctions/classes du fichier
                functions = extract_python_functions(resolved)
                if functions:
                    context_parts.append(f"\nFonctions/classes du fichier: {', '.join(functions)}")

            except OSError:
                context_parts.append(f"Fichier: {file_path} (contenu non disponible)")

    context = '\n'.join(context_parts) if context_parts else "Contexte non disponible"

    # Construire le prompt avec instructions explicites
    prompt = f"""Tu es un générateur de code Python expert. L'utilisateur veut que tu produises du code pour implémenter une instruction.

INSTRUCTION UTILISATEUR: {instruction}

CONTEXTE DU FICHIER:
{context}

RÈGLES ABSOLUES:
1. Pour EDIT/APPEND: produces UNIQUEMENT le code à ajouter/remplacer, pas le fichier entier
2. Pour une nouvelle fonction: produit le code complet de la fonction avec un nom pertinent
3. Respecte le style Python (snake_case, indentation 4 espaces, etc.)
4. Pas de markdown, pas de backticks, pas d'explications
5. Le code doit être syntaxiquement correct et prêt à l'emploi
6. Ne fais pas de modifications non demandées

Si l'instruction est vague ("ajoute quelque chose"), déduis ce qui serait le plus pertinent dans le contexte."""

    return clean_code_output(ask_ollama_with_options(prompt, model=model, temperature=0.2))


def generate_delete_plan(instruction, context, model=None):
    prompt = (
        f"{ROUTER_SYSTEM_PROMPT}\n\n"
        "Tu analyses une demande de suppression de code. "
        "Réponds uniquement en JSON valide avec ce schéma :\n"
        '{"thought":"","action":"delete|ask","file":"","target":"","reason":""}\n\n'
        f"Contexte disponible :\n{context}\n\n"
        f"Instruction : {instruction}\n\n"
        "Règles :\n"
        "- action=delete si tu identifies clairement un symbole ou une fonction à supprimer.\n"
        "- action=ask si la cible est ambiguë.\n"
        "- target doit contenir le nom exact à supprimer.\n"
        "- thought doit résumer brièvement l'analyse, sans raisonnement détaillé.\n"
        "- reason doit expliquer pourquoi la demande est ambiguë si action=ask."
    )
    raw = ask_ollama_with_options(prompt, model=model, response_format='json', temperature=0.2).strip()
    return json.loads(raw)


def route_query(query, model=None):
    """Route une requête utilisateur vers l'action appropriée.

    Comporte deux phases:
    1. Analyse locale (regex/heuristiques) pour les cas évidents
    2. Delegation à Ollama pour les cas complexes ou ambigus
    """
    index = load_index()

    # Phase 1: Analyse locale rapide pour les cas évidents
    local_analysis = fast_intent_detection(query)
    if local_analysis.get('confidence') == 'high' and local_analysis.get('action') in ('read', 'locate'):
        return local_analysis

    # Phase 2: Delegation à Ollama pour routage intelligent
    file_list_summary = format_file_index_for_llm(index)

    prompt = f"""Tu es un assistant de développement qui comprend VRAIMENT ce que les développeurs veulent dire, même quand c'est mal formulé.

UTILISATEUR: "{query}"

FICHIERS DU PROJET:
{file_list_summary}

Ton travail est de COMPRENDRE l'intention réelle et retourner l'action appropriée.

RÈGLES DE RAISONNEMENT:
1. Déduis l'intention derriere les mots, pas juste les mots eux-mêmes
2. "modifie" peut signifier: corrige un bug, ajoute une feature, refactor, change une config
3. "ajoute" peut signifier: nouvelle fonction, import, test, documentation, fonctionnalité
4. "supprime" peut signifier: efface du code mort, retire une feature, clean un import
5. Contexte: si l'utilisateur mentionne quelque chose qu'on a déjà vu, c'est le même fichier

Exemples de COMPRÉHENSION (pas juste matching):
- "le fichier de config" -> cherche config.py, settings.py, .env, etc.
- "la logique d'auth" -> cherche auth, login, oauth, jwt, etc.
- "cette fonction" -> déduit de quelle fonction on parle
- "corrige-le" -> corrige le dernier problème mentionné

FORMAT DE RÉPONSE (JSON SEULEMENT):
{{"action": "locate|read|edit|append|delete|create|answer|ask",
  "thought": "ton raisonnement en 1 phrase",
  "file": "fichier.py ou vide si non identifié",
  "target": "nom de fonction/classe ou vide",
  "instruction": "instruction reformulée pour le LLM de code ou vide",
  "answer": "réponse textuelle ou vide",
  "reason": "pourquoi ask ou vide"}}

Exemples concrets:
- "lis le fichier utils.py" -> {{"action": "read", "file": "utils.py"}}
- "ajoute validation email" -> {{"action": "edit", "file": "...", "instruction": "ajouter validation email dans le formulaire approprié"}}
- "supprime la méthode deprecated" -> {{"action": "delete", "target": "deprecated"}}
- "ou est le router ?" -> {{"action": "locate", "target": "router"}}
- "explique le code" -> {{"action": "answer", "answer": "résumé..."}}
- "fait quelque chose" -> {{"action": "ask", "reason": "trop vague, précise ce que tu veux"}}

N'inclus que les champs pertinents. Réponds en JSON valide."""

    raw = ask_ollama_with_options(prompt, model=model, response_format='json', temperature=0.3).strip()

    # Nettoyage de la réponse
    raw = clean_json_response(raw)

    try:
        result = json.loads(raw)
        # Validation et fallback
        if 'action' not in result:
            result['action'] = 'ask'
            result['reason'] = 'Réponse Ollama incomplète'
        return result
    except json.JSONDecodeError:
        # Fallback intelligent
        return fallback_route(query, index)


def format_file_index_for_llm(index, max_files=100):
    """Formate l'index des fichiers pour le prompt LLM."""
    if not index:
        return "(projet vide ou non indexé)"

    # Répartir par type
    py_files = [f for f in index if f.endswith('.py')]
    other_files = [f for f in index if not f.endswith('.py')]

    result = []
    result.append("Fichiers Python:")
    result.extend(f"  - {f}" for f in py_files[:50])

    if len(index) > 50:
        result.append(f"  ... et {len(index) - 50} autres fichiers")

    if other_files:
        result.append("Autres fichiers:")
        result.extend(f"  - {f}" for f in other_files[:30])

    return '\n'.join(result)


def clean_json_response(raw):
    """Nettoie une réponse JSON potentiellement corrompue."""
    # Supprime les markdown code blocks
    raw = re.sub(r'^```json\s*', '', raw, flags=re.MULTILINE)
    raw = re.sub(r'^```\s*', '', raw, flags=re.MULTILINE)
    raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)

    # Supprime le texte avant le JSON
    first_brace = raw.find('{')
    last_brace = raw.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        raw = raw[first_brace:last_brace + 1]

    return raw.strip()


def fast_intent_detection(query):
    """Détection rapide d'intention pour les cas évidents.

    Retourne un dict avec 'action', 'confidence', et les champs appropriés.
    """
    q = query.lower().strip()

    # Patterns très explicites
    patterns = [
        # Lecture
        (r'^(lis|voir|affiche|montre|contenu|consult)\s+(?:le\s+)?(.+\.py)', 'read', 'high'),
        (r'^contenu\s+(?:de|from)?\s*(.+\.py)', 'read', 'high'),
        (r'^cat\s+(.+\.py)', 'read', 'high'),

        # Suppression
        (r'^(supprime|delete|enlève|retire|vire|efface)\s+(?:la\s+)?(?:fonction|classe|méthode|procédure)\s+(\w+)', 'delete', 'high'),
        (r'^delete\s+(?:function|class)\s+(\w+)', 'delete', 'high'),
        (r'^(vire|supprime)\s+(\w+)\s*\(\)', 'delete', 'high'),

        # Append
        (r'^(ajoute|append|rajoute)\s+(?:à\s+la\s+fin\s+(?:de|dans?))?\s*(.+\.py)?\s*$', 'append', 'medium'),
        (r'^(ajoute|append)\s+(une?\s+)?(fonction|méthode|classe)', 'append', 'medium'),

        # Locate
        (r'^ou\s+(est|sont?|se\s+trouve)\s+(.+)$', 'locate', 'high'),
        (r'^(trouve|localise|cherch)\s+(.+)$', 'locate', 'high'),
    ]

    for pattern, action, confidence in patterns:
        match = re.match(pattern, q)
        if match:
            result = {'action': action, 'confidence': confidence}
            groups = match.groups()
            if action == 'read' and len(groups) >= 2:
                result['file'] = groups[1]
            elif action == 'delete' and len(groups) >= 2:
                result['target'] = groups[1]
            elif action == 'locate' and len(groups) >= 2:
                result['target'] = groups[2] if len(groups) > 2 else groups[1]
            return result

    return {'action': None, 'confidence': 'low'}


def fallback_route(query, index):
    """Fallback quand Ollama échoue ou retourne du JSON invalide.

    Utilise une analyse heuristique pour déterminer l'action.
    """
    q = query.lower()

    # Analyse du premier mot/phrase pourdeviner l'action
    action_keywords = {
        'read': ['lis', 'voir', 'montre', 'affiche', 'contenu', 'cat', 'read', 'consulte', 'montre-moi'],
        'edit': ['modifie', 'change', 'corrige', 'ajoute', 'rajoute', 'édite', 'edit', 'remplace', 'réécris', 'écris'],
        'delete': ['supprime', 'vire', 'delete', 'enlève', 'retire', 'efface'],
        'locate': ['où', 'ou est', 'trouve', 'localise', 'cherch', 'search', 'où est-ce'],
        'append': ['ajoute à la fin', 'append', 'rajoute à la fin'],
    }

    for action, keywords in action_keywords.items():
        if any(k in q for k in keywords):
            result = {'action': action, 'thought': f'Fallback heuristique: {action}'}
            # Extraire le fichier
            file_hint = extract_file_from_query(query)
            if file_hint:
                result['file'] = file_hint
            # Extraire la cible
            target = extract_target_from_query(query)
            if target:
                result['target'] = target
            return result

    # Dernier recours: ask
    return {'action': 'ask', 'reason': 'Impossible de déterminer l\'intention automatiquement'}


def extract_file_from_query(query):
    """Extrait un nom de fichier d'une requête."""
    # Extensions communes
    patterns = [
        r'(\w+\.py)',
        r'(\w+\.js)',
        r'(\w+\.ts)',
        r'(\w+\.json)',
        r'(\w+\.md)',
        r'(\w+\.txt)',
        r'"([^"]+\.\w+)"',
        r"'([^']+\.\w+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return match.group(1)
    return None


def extract_target_from_query(query):
    """Extrait une cible (fonction, classe) d'une requête."""
    patterns = [
        r'(?:fonction|méthode|class|classe)\s+(\w+)',
        r'(\w+)\s*\(\)',
        r'supprime(?:r|)?\s+(\w+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_python_functions(file_hint):
    resolved, _ = resolve_file_path_with_candidates(file_hint)
    if not resolved:
        return []

    abs_path = os.path.join(project_root(), resolved)
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            source = file_handle.read()
    except OSError:
        return []

    if not resolved.lower().endswith('.py'):
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    functions = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            functions.append(node.name)
    return functions


def find_function_location(function_name):
    results = []
    root = project_root()

    for rel_path in load_index():
        abs_path = os.path.join(root, rel_path)
        if not os.path.isfile(abs_path):
            continue

        try:
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
                for line_number, line in enumerate(file_handle, 1):
                    if function_name in line:
                        results.append({'file': rel_path, 'line': line_number, 'content': line.strip()})
        except OSError:
            continue

    return results


def find_file_location(file_name):
    results = []
    root = project_root()
    target = file_name.lower()

    for rel_path in load_index():
        if target in rel_path.lower():
            results.append({'file': rel_path, 'line': 1, 'content': rel_path})

    tool_root = os.path.dirname(os.path.abspath(__file__))
    for dirpath, _, filenames in os.walk(tool_root):
        for filename in filenames:
            if target in filename.lower():
                rel_path = os.path.relpath(os.path.join(dirpath, filename), root)
                if rel_path not in {item['file'] for item in results}:
                    results.append({'file': rel_path, 'line': 1, 'content': filename})

    return results


def locate_anything(target):
    results = find_file_location(target)
    results.extend(find_function_location(target))
    unique = []
    seen = set()
    for item in results:
        key = (item['file'], item['line'], item['content'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def read_file_content(file_hint, max_lines=200):
    resolved = resolve_file_path(file_hint)
    if not resolved:
        return None, None

    abs_path = os.path.join(project_root(), resolved)
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            lines = file_handle.readlines()
    except OSError:
        return resolved, None

    excerpt = ''.join(lines[:max_lines]).rstrip()
    return resolved, excerpt


def read_file_tail(file_hint, max_lines=10):
    resolved = resolve_file_path(file_hint)
    if not resolved:
        return None, None

    abs_path = os.path.join(project_root(), resolved)
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            lines = file_handle.readlines()
    except OSError:
        return resolved, None

    excerpt = ''.join(lines[-max_lines:]).rstrip()
    return resolved, excerpt


def summarize_file_content(file_hint, max_chars=20000):
    resolved = resolve_file_path(file_hint)
    if not resolved:
        return None, None

    abs_path = os.path.join(project_root(), resolved)
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            content = file_handle.read(max_chars)
    except OSError:
        return resolved, None

    prompt = (
        f"Voici le contenu du fichier {resolved} :\n\n{content}\n\n"
        "Résume ce fichier en français de manière utile pour un développeur. "
        "Donne : 1) le rôle du fichier, 2) les fonctions ou sections importantes, 3) ce qu'il faut retenir. "
        "Réponds de façon concise mais précise."
    )
    summary = ask_ollama_with_options(prompt, temperature=0.2)
    return resolved, summary


def delete_python_symbol(file_path, symbol_name):
    resolved = resolve_file_path(file_path) or file_path
    abs_path = os.path.join(project_root(), resolved)
    backup_path = create_backup(abs_path)
    original_size = os.path.getsize(abs_path)

    with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
        source = file_handle.read()

    if not resolved.lower().endswith('.py'):
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Suppression sûre non disponible pour ce type de fichier.")

    try:
        tree = ast.parse(source)
    except SyntaxError:
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Le fichier Python cible n'est pas syntaxiquement valide avant suppression.")

    lines = source.splitlines(True)
    nodes_to_remove = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol_name:
            nodes_to_remove.append(node)

    if not nodes_to_remove:
        restore_backup(backup_path, abs_path)
        raise RuntimeError(f"Aucun symbole '{symbol_name}' trouvé dans {resolved}.")

    removals = []
    for node in nodes_to_remove:
        start = node.lineno - 1
        end = getattr(node, 'end_lineno', node.lineno)
        removals.append((start, end))

    kept_lines = []
    current = 0
    for start, end in sorted(removals):
        kept_lines.extend(lines[current:start])
        current = end
    kept_lines.extend(lines[current:])

    new_content = ''.join(kept_lines).rstrip() + '\n'
    with open(abs_path, 'w', encoding='utf-8') as file_handle:
        file_handle.write(new_content)

    try:
        validate_python_syntax(abs_path)
    except Exception:
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Suppression rejetée: le fichier Python n'est plus syntaxiquement valide.")

    if not validate_written_file(original_size, abs_path):
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Suppression rejetée: le fichier généré est trop petit ou vide.")

    try:
        os.remove(backup_path)
    except OSError:
        pass
    return resolved


def create_backup(file_path):
    backup_path = file_path + '.bak'
    shutil.copy2(file_path, backup_path)
    return backup_path


def restore_backup(backup_path, destination_path):
    shutil.copy2(backup_path, destination_path)


def validate_written_file(original_size, new_path):
    new_size = os.path.getsize(new_path)
    if new_size == 0:
        return False
    if original_size > 0 and new_size < max(1, original_size // 4):
        return False
    return True


def validate_python_syntax(file_path):
    if not file_path.lower().endswith('.py'):
        return True

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
        source = file_handle.read()

    compile(source, file_path, 'exec')
    return True


def edit_file(file_path, instruction):
    resolved = resolve_file_path(file_path) or file_path
    abs_path = os.path.join(project_root(), resolved)
    backup_path = create_backup(abs_path)
    original_size = os.path.getsize(abs_path)

    with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
        original_content = f.read()

    prompt = f"""Tu dois modifier le fichier Python suivant. INSTRUCTIONS DE L'UTILISATEUR: {instruction}

FICHIER ACTUEL (complet):
```python
{original_content}
```

RÈGLES ABSOLUES:
1. Tu dois retourner le CONTENU COMPLET DU FICHIER MODIFIÉ (pas juste les modifications)
2. Ajoute UNIQUEMENT ce qui est demandé, ne supprime rien d'autre
3. Conserve tout le code existant, imports, fonctions, classes
4. Place les nouvelles fonctions/classes à un endroit logique
5. Respecte le style du fichier (mêmes conventions)
6. Pas de markdown, pas de backticks
7. Retourne EXACTEMENT le contenu complet du fichier modifié"""

    new_content = clean_code_output(ask_ollama_with_options(prompt, model='deepseek-coder:6.7b', temperature=0.2))

    if not new_content or len(new_content.strip()) < len(original_content) * 0.3:
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Modification rejetée: le code généré est invalide ou vide.")

    with open(abs_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    try:
        validate_python_syntax(abs_path)
    except Exception:
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Modification rejetée: la syntaxe Python générée est invalide.")

    if not validate_written_file(original_size, abs_path):
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Modification rejetée: le fichier généré est trop petit ou vide.")

    try:
        os.remove(backup_path)
    except OSError:
        pass
    return True


def append_file_block(file_path, instruction, block):
    resolved = resolve_file_path(file_path) or file_path
    abs_path = os.path.join(project_root(), resolved)
    backup_path = create_backup(abs_path)
    original_size = os.path.getsize(abs_path)
    with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
        original = file_handle.read()

    if original and not original.endswith('\n'):
        original += '\n'

    appended = clean_code_output(block).rstrip() + '\n'
    new_content = original + '\n' + appended if original else appended
    with open(abs_path, 'w', encoding='utf-8') as file_handle:
        file_handle.write(new_content)

    try:
        validate_python_syntax(abs_path)
    except Exception:
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Ajout rejeté: la syntaxe Python générée est invalide.")

    if not validate_written_file(original_size, abs_path):
        restore_backup(backup_path, abs_path)
        raise RuntimeError("Ajout rejeté: le fichier généré est trop petit ou vide.")

    try:
        os.remove(backup_path)
    except OSError:
        pass
    return resolved


def generate_append_block(file_path, instruction):
    resolved = resolve_file_path(file_path) or file_path
    return clean_code_output(generate_code_block(instruction, file_path=resolved))


def is_real_code(content, file_path=''):
    """Vérifie que le contenu n'est pas un placeholder vide ou un commentaire seul.

    Retourne False si le contenu est trop court, ne contient que des commentaires
    ou correspond à un texte de remplissage typique généré par les LLM.
    """
    if not content:
        return False

    stripped = content.strip()
    if len(stripped) < 20:
        return False

    placeholder_markers = (
        '# contenu', '# code ici', '# code complet', '# todo',
        'contenu de', 'contenu complet', '# placeholder', '# à compléter',
        '# a completer', '# implementation', '# implémentation',
    )
    low = stripped.lower()
    if any(low == m or low.startswith(m) for m in placeholder_markers):
        return False

    # Pour les fichiers Python: au moins une ligne non-commentaire non-vide
    if file_path.endswith('.py'):
        non_comment_lines = [
            line for line in stripped.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        if not non_comment_lines:
            return False

    return True


def create_file(file_path, content):
    """Crée un nouveau fichier avec le contenu fourni."""
    root = project_root()
    abs_path = os.path.join(root, file_path.lstrip('/\\'))

    parent_dir = os.path.dirname(abs_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    with open(abs_path, 'w', encoding='utf-8') as f:
        f.write(content)

    if file_path.endswith('.py'):
        try:
            validate_python_syntax(abs_path)
        except Exception as e:
            try:
                os.remove(abs_path)
            except OSError:
                pass
            raise RuntimeError(f"Syntaxe invalide dans {file_path}: {e}")

    update_index_after_creation(file_path)
    return file_path


def update_index_after_creation(new_file_path):
    """Ajoute un nouveau fichier à l'index sans rescan complet."""
    index_path = os.path.join(project_root(), '.knowledge_base', 'file_index.json')

    if not os.path.exists(index_path):
        return

    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)

        normalized_path = new_file_path.replace('\\', '/')
        if normalized_path not in index:
            index.append(normalized_path)
            index.sort()

            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, OSError):
        pass


def find_project_prompt_file():
    """Recherche un fichier de prompt dans le répertoire courant.

    Cherche dans cet ordre:
    - prompt.txt
    - prompt.md
    - PROJECT.txt
    - PROJECT.md
    - .prompt
    - INSTRUCTIONS.txt
    - INSTRUCTIONS.md
    """
    root = project_root()
    prompt_filenames = [
        'prompt.txt',
        'prompt.md',
        'PROJECT.txt',
        'PROJECT.md',
        '.prompt',
        '.project',
        'INSTRUCTIONS.txt',
        'INSTRUCTIONS.md',
        'SPEC.txt',
        'SPEC.md',
    ]

    for filename in prompt_filenames:
        path = os.path.join(root, filename)
        if os.path.isfile(path):
            return path
    return None


def load_project_prompt():
    """Charge et retourne le contenu du fichier de prompt du projet.

    Returns:
        tuple: (content, filename) ou (None, None) si pas trouvé
    """
    prompt_path = find_project_prompt_file()
    if not prompt_path:
        return None, None

    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content, os.path.basename(prompt_path)
    except OSError:
        return None, None


def apply_project_prompt(prompt_content):
    """Applique le prompt du projet en générant/mettant à jour les fichiers.

    Args:
        prompt_content: Contenu du fichier de prompt

    Returns:
        list: Fichiers créés ou modifiés
    """
    index = load_index()
    file_list_summary = format_file_index_for_llm(index)

    prompt = f"""Tu es un developpeur expert. Tu dois ECRIRE LE CODE REEL d'un projet complet.

CONTEXTE ACTUEL:
- Fichiers existants: {file_list_summary}
- Nombre de fichiers: {len(index)}

INSTRUCTIONS DU PROJET:
---
{prompt_content}
---

RÈGLES ABSOLUES:
1. Le champ "content" doit contenir LE CODE COMPLET ET FONCTIONNEL du fichier (pas un placeholder, pas un commentaire vide).
2. INTERDIT: "# contenu ici", "# code ici", "# TODO", des commentaires seuls, ou un fichier vide. Tu dois ECRIRE LE VRAI CODE.
3. Chaque fichier doit etre autonome, executable, avec tous les imports.
4. Si un fichier existe deja, content = nouvelle version COMPLETE (pas un diff).
5. Reponds UNIQUEMENT avec un JSON valide, sans texte autour, sans markdown.

FORMAT DE REPONSE (exemple concret avec du VRAI CODE):
{{
    "files": [
        {{
            "path": "main.py",
            "action": "create",
            "content": "from fastapi import FastAPI\\n\\napp = FastAPI()\\n\\n@app.get('/')\\ndef root():\\n    return {{'message': 'Hello'}}\\n"
        }},
        {{
            "path": "requirements.txt",
            "action": "create",
            "content": "fastapi==0.110.0\\nuvicorn==0.27.0\\n"
        }}
    ]
}}

Maintenant, genere le projet demande avec du code REEL et FONCTIONNEL."""

    raw = ask_ollama_with_options(prompt, model='deepseek-coder:6.7b', response_format='json', temperature=0.3)
    raw = clean_json_response(raw)
    result = json.loads(raw)

    created_files = []
    updated_files = []
    skipped_files = []

    for file_info in result.get('files', []):
        path = file_info.get('path', '')
        content = file_info.get('content', '')
        action = file_info.get('action', 'create')

        if not path or not content:
            continue

        # Refuser le contenu placeholder / trop court
        if not is_real_code(content, path):
            skipped_files.append(path)
            print(f"  ! Ignore (contenu placeholder): {path}")
            continue

        try:
            abs_path = os.path.join(project_root(), path.lstrip('/\\'))

            # Vérifier si le fichier existe déjà
            file_exists = os.path.exists(abs_path)

            if file_exists:
                # Créer un backup avant modification
                backup_path = create_backup(abs_path)
                original_size = os.path.getsize(abs_path)
            else:
                # Créer les répertoires parents
                parent_dir = os.path.dirname(abs_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)

            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(content)

            # Valider syntaxe Python
            if path.endswith('.py'):
                try:
                    validate_python_syntax(abs_path)
                except Exception as e:
                    if file_exists:
                        restore_backup(backup_path, abs_path)
                    raise RuntimeError(f"Syntaxe invalide dans {path}: {e}")

            if not file_exists:
                update_index_after_creation(path)

            if action == 'update' or file_exists:
                updated_files.append(path)
            else:
                created_files.append(path)

        except Exception as e:
            print(f"Erreur traitement {path}: {e}")

    return created_files, updated_files


def generate_project(directive, model=None):
    """Génère un projet complet (multi-fichiers) en une seule passe."""
    index = load_index()

    prompt = f"""Tu dois créer un projet COMPLET en une seule génération.

DIRECTIVE UTILISATEUR: {directive}

FICHIERS EXISTANTS: {json.dumps(index, ensure_ascii=False)}

RÈGLES:
1. Réponds en JSON avec ce format exact:
{{
    "files": [
        {{"path": "chemin/vers/fichier.py", "content": "# code ici"}},
        ...
    ]
}}

2. Chaque fichier doit être COMPLET et exécutable
3. Tous les imports nécessaires doivent être inclus
4. Respecte les conventions Python
5. Assure la cohérence entre les fichiers (mêmes imports, types compatibles)
6. Crée TOUS les fichiers nécessaires pour que le projet fonctionne

7. Pour les chemins, utilise des noms pertinents:
   - main.py, config.py, models.py, utils.py, etc.
   - Organise en sous-dossiers si logique (ex: src/, tests/, etc.)

8. Réponds UNIQUEMENT avec le JSON, pas de texte autour.
"""

    raw = ask_ollama_with_options(prompt, model=model or DEFAULT_MODEL, response_format='json', temperature=0.3)

    raw = clean_json_response(raw)
    result = json.loads(raw)
    created_files = []

    # Fichiers du projet SELF_DEV_AGENT à ne jamais écraser
    _PROTECTED = {'brain.py', 'main.py', 'install.py', 'uninstall.py', 'scanner.py', 'selfdev.bat'}

    for file_info in result.get('files', []):
        path = file_info.get('path', '')
        content = file_info.get('content', '')
        if not path or not content:
            continue
        if os.path.basename(path) in _PROTECTED:
            print(f"  ! Ignore (fichier protégé): {path}")
            continue
        try:
            create_file(path, content)
            created_files.append(path)
        except Exception as e:
            print(f"Erreur création {path}: {e}")

    return created_files
