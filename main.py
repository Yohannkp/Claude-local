import argparse
import json
import os
import re
import subprocess
import sys

# Force UTF-8 output on Windows to avoid UnicodeEncodeError with emoji/special chars
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, Exception):
        pass

from brain import (
    ask_ollama,
    append_file_block,
    edit_file,
    extract_python_functions,
    find_file_candidates,
    generate_append_block,
    delete_python_symbol,
    generate_delete_plan,
    load_index,
    locate_anything,
    route_query,
    read_file_content,
    read_file_tail,
    summarize_file_content,
    create_file,
    generate_project,
    generate_code_block,
    find_project_prompt_file,
    load_project_prompt,
    apply_project_prompt,
)
from scanner import main as scan_main


def project_root():
    return os.path.abspath(os.getcwd())


def ensure_index():
    root = project_root()
    index_file = os.path.join(root, '.knowledge_base', 'file_index.json')
    if not os.path.exists(index_file):
        scan_main(root)


def conversational_answer(query):
    """Génère une réponse conversationnelle via Ollama."""
    ensure_index()
    index = load_index()
    context_hint = ""

    if index:
        py_files = [f for f in index if f.endswith('.py')]
        if py_files:
            context_hint = f"\n\nLe projet contient {len(py_files)} fichiers Python dont: {', '.join(py_files[:5])}"

    prompt = (
        "Tu es un assistant de développement français, clair et direct comme un collègue expérimentés.\n"
        f"Question: {query}{context_hint}\n\n"
        "Réponds de façon útille. Si tu ne sais pas, dis-le honnêtement."
    )
    try:
        return ask_ollama(prompt)
    except RuntimeError:
        return offline_answer(query, index)


def offline_answer(query, index=None):
    if index is None:
        ensure_index()
        index = load_index()

    q = query.lower().strip()

    if any(word in q for word in ['help', 'aide', '?', 'comment']):
        return (
            "Je peux t'aider à:\n"
            "  - Lire un fichier: 'lis main.py'\n"
            "  - Trouver une fonction: 'où est parse_config'\n"
            "  - Modifier du code: 'ajoute validation email'\n"
            "  - Supprimer une fonction: 'supprime toto()'\n"
            "  - Poser une question: 'explique le projet'\n\n"
            "Lance Ollama pour des réponses plus intelligentes."
        )

    if any(phrase in q for phrase in ['projet', 'structure', 'fichiers', 'code']):
        if index:
            py_files = [f for f in index if f.endswith('.py')]
            other_files = [f for f in index if not f.endswith('.py')]
            response = f"Projet composé de {len(index)} fichiers:\n"
            response += f"- {len(py_files)} fichiers Python: {', '.join(py_files[:7])}"
            if len(py_files) > 7:
                response += f" ... et {len(py_files) - 7} autres"
            if other_files:
                response += f"\n- {len(other_files)} autres fichiers"
            return response
        return "Projet non indexé. Tape 'selfdev' en interactif pour commencer."

    if any(word in q for word in ['fichier', 'file', 'dans']):
        if index:
            return f"Fichiers disponibles: {', '.join(index[:10])}"
        return "Index non chargé."

    return (
        "Je suis en mode offline. Lance Ollama pour activer le routage intelligent.\n"
        "En attendant: locate, read, edit, append, delete, ask"
    )


def explicit_command(query):
    stripped = query.strip()
    if not stripped:
        return None, ''
    command, _, rest = stripped.partition(' ')
    command = command.lower()
    if command in {'locate', 'read', 'edit', 'append', 'delete', 'ask', 'create', 'prompt', 'init', 'build', 'setup'}:
        return command, rest.strip()
    return None, stripped


def infer_local_action(query):
    """Déduit l'action à partir de la requête en langage naturel.

    Cette fonction utilise des heuristiques pour comprendre l'intention
    de l'utilisateur, même pour les formulations informelles ou ambiguës.
    """
    lowered = query.lower()
    stripped = query.strip()

    # ==========================================
    # PHASE 1: Détection des intentions claires
    # ==========================================

    # Intentions de LECTURE
    read_patterns = [
        # Formes explicites
        r'^conten[ut]\s+(?:de|from|dans?)?\s*(.+)$',
        r'^(?:lis|voir|affiche|montre|consult)\s+(?:le\s+|l\'|)(.+)',
        r'^cat\s+(.+)$',
        r'^type\s+(.+)$',
        r'^(?:il y a|ya|y\'a)\s+(?:quoi|koi)\s+(?:dans?|)(?:\s*(.+))?$',
        r'^c quoi\s+(?:dans?|)(?:\s*(.+))?$',
        r'^qu\'est\s*ce\s*qu\'il\s*y\s*a\s*(?:dans?|)(?:\s*(.+))?$',
        r'^show me\s+(?:the\s+|)(.+)',
        r'^display\s+(.+)',
        r'^(?:affiche|montre)\s+(?:le\s+|l\'|)contenu\s+(?:de|from|dans?)?\s*(.+)$',
        # Patterns avec "fichier" ou "file"
        r'^(?:le\s+|l\'|)(?:fichier|file)\s+(\w+\.\w+)\s*(?:$|\?|$)',
        r'^(?:fichier|file)\s+(\w+\.\w+)',
        r'^(\w+\.\w+)\s*(?:contient|contenu|)$',
    ]

    for pattern in read_patterns:
        match = re.match(pattern, stripped, re.IGNORECASE)
        if match:
            file_hint = None
            if match.groups():
                file_hint = match.group(1) if match.group(1) else None

            if file_hint:
                # Nettoyage
                file_hint = file_hint.strip('"\' ,:;?!')
                if file_hint:
                    return {'action': 'read', 'file': file_hint}
            elif any(x in lowered for x in ['contenu', 'quoi', 'koi', 'a quoi']):
                # Requête générale sur le projet
                return {'action': 'answer', 'answer': query}
            return {'action': 'read', 'file': extract_file_hint(query) or query}

    # Intentions de SUPPRESSION
    delete_patterns = [
        r'^(?:supprime|delete|enlève|retire|vire|efface)\s+(?:la\s+|le\s+|l\'|)(?:fonction|méthode|classe|procédure|variable|constante)?\s*(\w+)(?:\s*\(\))?$',
        r'^delete\s+(?:function|class|method)?\s*(\w+)$',
        r'^rm\s+(?:function|class)?\s*(\w+)$',
        r'^vire\s+(\w+)(?:\s*\(\))?$',
        r'^suppr(?:ime|)?\s+(\w+)$',
        r'^(?:supprime|delete|enlève)\s+(.+?)\s+(?:dans?|from)\s+(.+)$',
    ]

    for pattern in delete_patterns:
        match = re.match(pattern, lowered)
        if match:
            target = match.group(1) if match.group(1) else match.group(2) if len(match.groups()) > 1 else ''
            file_hint = match.group(2) if len(match.groups()) > 1 else extract_file_hint(query)
            return {
                'action': 'delete',
                'target': target.strip() if target else extract_symbol_hint(query) or '',
                'file': file_hint or '',
                'instruction': query
            }

    # Intentions de CRÉATION
    create_patterns = [
        r'^(?:crée|create|fabrique|build|make)\s+(?:un\s+|une\s+)?(?:fichier|file)\s+(\S+\.\w+)',
        r'^(?:crée|create)\s+(\S+\.\w+)\s*(?:qui|avec|contenant)?',
        r'^nouveau\s+fichier\s+(\S+)',
        r'^(?:génère|genère)\s+(?:un\s+)?(?:fichier|projet)',
        r'^(?:fabrique|build|make)\s+(?:un\s+)?(?:fichier|projet)',
        r'^(?:crée|create|fabrique)\s+(?:un\s+)?(?:projet|bot|application|api|site|website|app)',
        r'^(?:écris|write)\s+(?:un\s+)?(?:fichier|script)',
    ]

    for pattern in create_patterns:
        match = re.match(pattern, lowered)
        if match:
            file_hint = match.group(1) if match.group(1) else None
            if file_hint:
                return {
                    'action': 'create',
                    'file': file_hint.strip(),
                    'instruction': query
                }
            # Pas de fichier explicite - c'est un projet multi-fichiers
            return {
                'action': 'create',
                'file': '',
                'instruction': query
            }

    # Intentions de prompt/init (charger le fichier prompt)
    prompt_patterns = [
        r'^(?:init|initialize)\s*$',
        r'^(?:prompt|load prompt)\s*$',
        r'^(?:charge|applique)\s+(?:le\s+)?prompt\s*$',
        r'^(?:lit|read)\s+(?:le\s+)?prompt\s*$',
        r'^build\s*$',
        r'^setup\s*$',
    ]

    for pattern in prompt_patterns:
        if re.match(pattern, lowered):
            return {'action': 'prompt', 'instruction': query}

    # Intentions d'AJOUT à la fin
    append_patterns = [
        r'^(?:ajoute|append|rajoute|ajout)\s+(?:à\s+la\s+fin\s+(?:de|dans?|)|)(?:le\s+|l\'|)(?:fichier|file|)',
        r'^(?:ajoute|append|rajoute)\s+(?:une?\s+)?(?:fonction|méthode|classe|code|bloc)',
        r'^(?:rajoute|ajoute)\s+(?:moi\s+|)(?:à\s+la\s+fin\s+|)',
        r'^(?:ajoute|append)\s+(?:dans?|at\s+end\s+of)',
    ]

    for pattern in append_patterns:
        if re.match(pattern, lowered):
            file_hint = extract_file_hint(query)
            return {
                'action': 'append',
                'file': file_hint or '',
                'instruction': query
            }

    # Intentions de LOCALISATION
    locate_patterns = [
        r'^(?:où|ou)\s+(?:est|sont?|se\s+trouve(?:nt)?)\s+(?:(?:le\s+|l\'|)(?:fichier|fonction|code|logique|variable|classe)\s+|)(.+)$',
        r'^(?:où|ou)\s+(.+?)(?:\s+(?:se?\s+)?trouve|\s+est|\s+dans|\s+dans?)$',
        r'^trouv(?:e|er)\s+(?:le\s+|l\'|)(?:fichier|fonction|code)?\s*(.+)$',
        r'^cherch(?:e|er)\s+(?:le\s+|l\'|)(?:fichier|fonction|code)?\s*(.+)$',
        r'^localise\s+(?:le\s+|l\'|)(?:fichier|fonction)?\s*(.+)$',
        r'^(?:cherche|search)\s+(?:for\s+|)(.+)$',
        r'^(?:find|locate)\s+(?:the\s+|)(?:file|function|class)?\s*(.+)$',
        r'^(?:wheres|where\'s)\s+(?:the\s+|)(.+)$',
    ]

    for pattern in locate_patterns:
        match = re.match(pattern, lowered)
        if match:
            target = match.group(1).strip() if match.group(1) else ''
            if target:
                return {'action': 'locate', 'target': target}
            return {'action': 'locate', 'target': extract_file_hint(query) or query}

    # Intentions d'ÉDITION/MODIFICATION
    edit_intent_tokens = [
        'modifie', 'modificar', 'changement', 'change', 'corrige', 'correction',
        'corriger', 'ajoute', 'rajoute', 'ajouter', 'rajouter', 'édite', 'éditer',
        'edite', 'éditer', 'remplace', 'remplacer', 'réécris', 'réécrire', 'écris',
        'écrire', 'mets', 'mettre', 'met à jour', 'mise à jour', 'update',
        'modify', 'fix', 'fixes', 'add', 'remove', 'transform', 'refactor',
        'clean', 'nettoie', 'nettoyer', 'refactor', 'refactorer',
        'amelior', 'amélior', 'améliorer', 'optimise', 'optimiser',
    ]

    if any(token in lowered for token in edit_intent_tokens):
        file_hint = extract_file_hint(query)
        target = extract_symbol_hint(query)

        # Cas particulier: demande de modification d'une fonction spécifique
        if target:
            return {
                'action': 'edit',
                'file': file_hint or '',
                'instruction': query,
                'target': target
            }

        # Demande générale de modification
        return {
            'action': 'edit',
            'file': file_hint or '',
            'instruction': query
        }

    # ==========================================
    # PHASE 2: Patterns composites et informels
    # ==========================================

    # "corrige le bug dans X" -> edit
    bug_pattern = r'(?:bug|erreur|plantage|problème|pb)\s+(?:dans?|within|inside)\s+(\w+)'
    match = re.search(bug_pattern, lowered)
    if match:
        return {
            'action': 'edit',
            'file': match.group(1) or '',
            'instruction': f'corriger le bug dans {match.group(1) or ""}'
        }

    # "ajoute la feature X" -> edit
    feature_pattern = r'ajoute(?:r|)?\s+(?:la\s+|un\s+|une\s+)(?:feature|fonctionnalité|capacité)\s+(?:de\s+|)(.+)$'
    match = re.search(feature_pattern, lowered)
    if match:
        return {
            'action': 'edit',
            'file': extract_file_hint(query) or '',
            'instruction': f'ajouter {match.group(1)}'
        }

    # "intègre X dans Y" -> edit
    integrate_pattern = r'intègr(?:e|er)\s+(.+?)\s+(?:dans?|into)\s+(.+)'
    match = re.search(integrate_pattern, lowered)
    if match:
        return {
            'action': 'edit',
            'file': match.group(2) or '',
            'instruction': f'intégrer {match.group(1)}'
        }

    # "fait en sorte que X" -> edit (demande de modification comportementale)
    behavior_pattern = r'fait\s+(?:en\s+)?sorte\s+que\s+(.+)$'
    match = re.search(behavior_pattern, lowered)
    if match:
        return {
            'action': 'edit',
            'file': extract_file_hint(query) or '',
            'instruction': f'assurer que {match.group(1)}'
        }

    # ==========================================
    # PHASE 3: Fallback intelligent
    # ==========================================

    # Si on a un fichier explicite et une demande d'action vague
    file_hint = extract_file_hint(query)
    if file_hint:
        # Vérifier si c'est clairement une question -> read
        question_words = ['quoi', 'koi', 'comment', 'pourquoi', 'quand', 'combien', 'que', 'what', 'how', 'why']
        if any(q in lowered for q in question_words) or '?' in query:
            return {'action': 'read', 'file': file_hint}

    # Dernier recours: ask pour clarification
    return {'action': 'answer', 'answer': query}


def extract_file_hint(text):
    """Extrait un nom de fichier d'une requête utilisateur.

    Gère de nombreux formats et variations pour максимальную flexibilité.
    """
    # Pattern 1: Entre guillemets
    quoted = re.search(r'"([^"]+\.[A-Za-z0-9]+)"|\'([^\']+\.[A-Za-z0-9]+)\'', text)
    if quoted:
        return quoted.group(1) or quoted.group(2)

    # Pattern 2: Extensions communes avec chemin possible
    extension_match = re.search(r'([\w./\\-]+\.[A-Za-z0-9]+)', text)
    if extension_match:
        return extension_match.group(1)

    # Pattern 3: Mot-clé fichier suivi du nom
    lowered = text.lower()
    file_keywords = ['fichier', 'file', 'dans ', 'dans le ', 'dans ', 'dans l\'', 'dans le fichier', 'dans file']

    for keyword in file_keywords:
        if keyword in lowered:
            tail = lowered.split(keyword, 1)[-1].strip()
            if tail:
                # Prendre le premier mot comme nom de fichier
                parts = tail.split()
                if parts:
                    candidate = parts[0].strip('"\' ,:;?!')
                    # Vérifier que ça ressemble à un nom de fichier
                    if '.' in candidate or len(candidate) < 30:
                        return candidate

    # Pattern 4: Après une préposition
    prepositions = ['de ', 'à ', 'dans ', 'sur ', 'with ', 'into ', 'to ']
    for prep in prepositions:
        if prep in lowered:
            tail = lowered.split(prep, 1)[-1].strip()
            if tail:
                words = tail.split()
                if words:
                    candidate = words[0].strip('"\' ,:;?!')
                    if candidate and len(candidate) < 50:
                        return candidate

    return None


def extract_symbol_hint(text):
    """Extrait le nom d'une fonction, classe ou méthode d'une requête.

    Reconnaît plusieurs patterns et formulations.
    """
    # Pattern 1: Mot-clé explicite (fonction, méthode, class, etc.)
    patterns = [
        r'\b(?:fonction|méthode|procédure|procedure|class|classe)\s+(\w+)\b',
        r'\b(?:function|method|proc)\s+(\w+)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    # Pattern 2: Format avec parenthèses "nom()"
    match = re.search(r'(\w+)\s*\(\)', text)
    if match:
        return match.group(1)

    # Pattern 3: "supprime/enlève/..." + nom
    action_verbs = ['supprime', 'delete', 'enlève', 'retire', 'vire', 'modifie', 'edite', 'change', 'corrige']
    lowered = text.lower()
    for verb in action_verbs:
        if verb in lowered:
            tail = lowered.split(verb, 1)[-1].strip()
            if tail:
                match = re.match(r'^[\s\'"]*(\w+)', tail)
                if match:
                    return match.group(1)

    # Pattern 4: Après "dans" ou "de" pour une fonction
    patterns = [
        r'dans\s+(?:le\s+|l\')?(?:\w+\s+)?(\w+)\s*\(',
        r'de\s+(\w+)\s*\(',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def confirm_file_target(file_hint):
    candidates = find_file_candidates(file_hint)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    print(f"Plusieurs fichiers correspondent à '{file_hint}':")
    for index, candidate in enumerate(candidates, 1):
        print(f"  {index}. {candidate}")

    try:
        choice = input("Choisis le numéro du fichier: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not choice.isdigit():
        return None
    selected = int(choice)
    if 1 <= selected <= len(candidates):
        return candidates[selected - 1]
    return None


def cmd_locate(args):
    ensure_index()
    target = args.name.strip()
    resolved = confirm_file_target(target)

    if resolved and resolved.lower().endswith('.py'):
        functions = extract_python_functions(resolved)
        if functions:
            print(f"Fonctions dans {resolved}:")
            for function_name in functions:
                print(f"- {function_name}")
            return

    results = locate_anything(target)
    if not results:
        print(f"Aucune occurrence de '{target}' trouvée.")
        return

    for result in results:
        print(f"{result['file']} : ligne {result['line']} => {result['content']}")


def cmd_edit(args):
    ensure_index()
    target = confirm_file_target(args.file)
    if not target:
        print(f"Aucun fichier correspondant à '{args.file}' n'a été trouvé ou la sélection a été annulée.")
        return

    lowered_instruction = args.instruction.lower()
    if any(token in lowered_instruction for token in ('supprime', 'supprimer', 'enlève', 'enlever', 'retire', 'retirer', 'efface', 'effacer')):
        symbol = extract_symbol_hint(args.instruction)
        if not symbol:
            print("L'IA n'a pas identifié le symbole à supprimer.")
            return
        try:
            delete_python_symbol(target, symbol)
            print(f"Symbole '{symbol}' supprimé de '{target}' avec succès.")
            return
        except RuntimeError as error:
            print(str(error))
            return

    edit_file(target, args.instruction)
    print(f"Fichier '{target}' modifié avec succès.")


def cmd_delete(args):
    ensure_index()
    target = confirm_file_target(args.file)
    if not target:
        print(f"Aucun fichier correspondant à '{args.file}' n'a été trouvé ou la sélection a été annulée.")
        return

    symbol = extract_symbol_hint(args.symbol or args.instruction or args.file)
    if not symbol:
        try:
            plan = generate_delete_plan(args.instruction or args.file, f"file={target}")
            if plan.get('action') == 'ask':
                print(plan.get('reason') or "La cible à supprimer est ambiguë.")
                return
            symbol = (plan.get('target') or '').strip()
        except Exception:
            symbol = ''

    if not symbol:
        print("L'IA n'a pas identifié le symbole à supprimer.")
        return

    try:
        deleted = delete_python_symbol(target, symbol)
        print(f"Symbole '{symbol}' supprimé de '{deleted}' avec succès.")
    except RuntimeError as error:
        print(str(error))


def cmd_read(args):
    ensure_index()
    target = confirm_file_target(args.file)
    if not target:
        print(f"Aucun fichier correspondant à '{args.file}' n'a été trouvé ou la sélection a été annulée.")
        return

    resolved, summary = summarize_file_content(target)
    if not resolved:
        print(f"Aucun fichier correspondant à '{target}' n'a été trouvé.")
        return
    if summary is None:
        resolved, content = read_file_content(target)
        if content is None:
            print(f"Impossible de lire '{resolved}'.")
            return
        print(f"--- {resolved} ---")
        print(content)
        return

    print(f"--- {resolved} ---")
    print(summary)


def cmd_append(args):
    ensure_index()
    target = confirm_file_target(args.file)
    if not target:
        print(f"Aucun fichier correspondant à '{args.file}' n'a été trouvé ou la sélection a été annulée.")
        return

    block = generate_append_block(target, args.instruction)
    resolved = append_file_block(target, args.instruction, block)
    print(f"Bloc ajouté à la fin de '{resolved}' avec succès.")


def cmd_ask(args):
    ensure_index()
    query = args.query.strip()

    # Vérification pour les commandes explicites intégrées
    explicit, rest = explicit_command(query)

    # Commandes spéciales (prompt, init, build, setup) - même sans argument
    if explicit in ('prompt', 'init', 'build', 'setup'):
        dispatch_action('prompt', {'action': 'prompt', 'instruction': ''}, rest or explicit)
        return

    if explicit and rest:
        if explicit == 'locate':
            cmd_locate(argparse.Namespace(name=rest))
            return
        if explicit == 'read':
            cmd_read(argparse.Namespace(file=rest))
            return
        if explicit == 'edit':
            file_path, _, instruction = rest.partition(' ')
            if not instruction:
                print("Usage: edit <fichier> <instruction>")
                return
            cmd_edit(argparse.Namespace(file=file_path.strip(), instruction=instruction.strip()))
            return
        if explicit == 'delete':
            file_path, _, instruction = rest.partition(' ')
            if not instruction:
                print("Usage: delete <fichier> <symbole ou instruction>")
                return
            cmd_delete(argparse.Namespace(file=file_path.strip(), instruction=instruction.strip(), symbol=''))
            return
        if explicit == 'append':
            file_path, _, instruction = rest.partition(' ')
            if not instruction:
                print("Usage: append <fichier> <instruction>")
                return
            cmd_append(argparse.Namespace(file=file_path.strip(), instruction=instruction.strip()))
            return
        if explicit == 'create':
            file_path, _, instruction = rest.partition(' ')
            if file_path:
                dispatch_action('create', {'action': 'create', 'file': file_path.strip(), 'instruction': instruction.strip()}, rest)
            else:
                dispatch_action('create', {'action': 'create', 'file': '', 'instruction': rest}, rest)
            return
        if explicit == 'ask' and rest:
            print(conversational_answer(rest))
            return

    # Routage principal via Ollama
    try:
        routed = route_query(query)
    except Exception as e:
        print(f"⚠ Erreur de routage Ollama: {e}")
        print("→ Utilisation du fallback local...")
        routed = infer_local_action(query)

    action = routed.get('action', 'answer')

    # Logique de dispatch intelligente
    result = dispatch_action(action, routed, query)
    if result:
        return

    # Si aucune action n'a été trouvée, essayer de comprendre autrement
    print(conversational_answer(query))


def dispatch_action(action, routed, original_query):
    """Dispatch l'action vers la commande appropriée."""

    # action=ask: demande de clarification
    if action == 'ask':
        reason = routed.get('reason', 'Je dois mieux comprendre ce que tu veux.')
        print(f"🤔 {reason}")
        return True

    # action=answer: réponse conversationnelle
    if action == 'answer':
        answer = routed.get('answer', '').strip()
        if answer:
            print(answer)
        else:
            print(conversational_answer(original_query))
        return True

    # action=locate: localiser un fichier ou symbole
    if action == 'locate':
        target = (routed.get('target') or routed.get('file') or extract_file_hint(original_query) or original_query).strip()
        if target:
            cmd_locate(argparse.Namespace(name=target))
            return True
        print("🤔 Je n'ai pas compris quoi chercher. Peux-tu préciser ?")
        return True

    # action=read: lire un fichier
    if action == 'read':
        file_hint = (routed.get('file') or routed.get('target') or extract_file_hint(original_query) or '').strip()
        if file_hint:
            cmd_read(argparse.Namespace(file=file_hint))
            return True
        print("🤔 Je n'ai pas identifié de fichier à lire. Peux-tu me dire lequel ?")
        return True

    # action=append: ajouter à la fin d'un fichier
    if action == 'append':
        file_path = (routed.get('file') or extract_file_hint(original_query) or '').strip()
        instruction = (routed.get('instruction') or routed.get('answer') or original_query).strip()
        if file_path:
            cmd_append(argparse.Namespace(file=file_path, instruction=instruction))
            return True
        print("🤔 Je n'ai pas identifié de fichier cible pour l'ajout.")
        return True

    # action=delete: supprimer un symbole
    if action == 'delete':
        file_path = (routed.get('file') or extract_file_hint(original_query) or '').strip()
        symbol = (routed.get('target') or extract_symbol_hint(original_query) or '').strip()
        instruction = (routed.get('instruction') or original_query).strip()
        if file_path:
            cmd_delete(argparse.Namespace(file=file_path, instruction=instruction, symbol=symbol))
            return True
        # Tenter une suppression globale
        if symbol:
            print(f"🤔 Je vais chercher '{symbol}' dans le projet...")
            cmd_locate(argparse.Namespace(name=symbol))
            return True
        print("🤔 Je n'ai pas identifié ce qu'il faut supprimer.")
        return True

    # action=edit: modifier un fichier
    if action == 'edit':
        file_path = (routed.get('file') or extract_file_hint(original_query) or '').strip()
        instruction = (routed.get('instruction') or routed.get('answer') or original_query).strip()
        if file_path:
            cmd_edit(argparse.Namespace(file=file_path, instruction=instruction))
            return True
        # Essayer de deviner le fichier
        file_hint = extract_file_hint(original_query)
        if file_hint:
            cmd_edit(argparse.Namespace(file=file_hint, instruction=instruction))
            return True
        print("🤔 Je n'ai pas identifié de fichier à modifier.")
        return True

    # action=create: créer un fichier ou projet
    if action == 'create':
        file_path = (routed.get('file') or '').strip()
        instruction = (routed.get('instruction') or original_query).strip()

        # Vérifier si c'est une demande de projet multi-fichiers
        project_keywords = ['projet', 'bot', 'application', 'api', 'site', 'website', 'app', 'discord', 'telegram', 'web']
        is_project = len(instruction) > 80 or any(kw in instruction.lower() for kw in project_keywords)

        if is_project:
            print("Génération du projet en cours...")
            try:
                files = generate_project(instruction)
                if files:
                    print(f"\nProjet créé avec {len(files)} fichiers:")
                    for f in files:
                        print(f"  - {f}")
                else:
                    print("La génération n'a créé aucun fichier.")
            except Exception as e:
                print(f"Erreur lors de la génération du projet: {e}")
        elif file_path:
            # Fichier unique
            print(f"Création de {file_path}...")
            try:
                content = generate_code_block(instruction, file_path=file_path)
                path = create_file(file_path, content)
                print(f"Fichier créé: {path}")
            except Exception as e:
                print(f"Erreur lors de la création: {e}")
        else:
            print("Je n'ai pas compris quel fichier créer. Peux-tu préciser ?")
        return True

    # action=prompt: charger et appliquer le fichier prompt du projet
    if action == 'prompt':
        prompt_path = find_project_prompt_file()
        if not prompt_path:
            print("Aucun fichier de prompt trouvé dans le répertoire.")
            print("Crée un fichier: prompt.txt, prompt.md, PROJECT.txt, PROJECT.md, ou INSTRUCTIONS.txt")
            return True

        content, filename = load_project_prompt()
        print(f"Fichier de prompt trouvé: {filename}")
        print(f"Contenu ({len(content)} caractères):")
        print("-" * 40)
        print(content[:500] + ("..." if len(content) > 500 else ""))
        print("-" * 40)

        # Demander confirmation avant d'appliquer
        try:
            confirm = input("\nAppliquer ce prompt ? (o/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("Annulé.")
            return True

        if confirm in ('o', 'oui', 'y', 'yes'):
            print("\nApplication du prompt en cours...")
            try:
                created, updated = apply_project_prompt(content)
                if created:
                    print(f"\nFichiers créés: {len(created)}")
                    for f in created:
                        print(f"  + {f}")
                if updated:
                    print(f"\nFichiers mis à jour: {len(updated)}")
                    for f in updated:
                        print(f"  ~ {f}")
                if not created and not updated:
                    print("Aucun fichier n'a été créé ou modifié.")
            except Exception as e:
                print(f"Erreur lors de l'application: {e}")
        else:
            print("Prompt non appliqué.")
        return True

    return False


def fallback_offline_answer(query, index):
    """Réponse de secours quand Ollama n'est pas disponible."""
    q = query.lower().strip()

    # Aide simple
    if any(word in q for word in ['help', 'aide', '?', 'comment']):
        return (
            "Je peux t'aider à:\n"
            "  - Lire un fichier: 'lis main.py'\n"
            "  - Trouver une fonction: 'où est parse_config'\n"
            "  - Modifier du code: 'ajoute validation email'\n"
            "  - Supprimer une fonction: 'supprime toto()'\n"
            "  - Poser une question: 'explique le projet'\n\n"
            "Lance Ollama pour des réponses plus intelligentes."
        )

    # Questions sur le projet
    if any(phrase in q for phrase in ['projet', 'structure', 'fichiers', 'code']):
        if index:
            py_files = [f for f in index if f.endswith('.py')]
            other_files = [f for f in index if not f.endswith('.py')]
            response = f"Projet composé de {len(index)} fichiers:\n"
            response += f"- {len(py_files)} fichiers Python: {', '.join(py_files[:7])}"
            if len(py_files) > 7:
                response += f" ... et {len(py_files) - 7} autres"
            if other_files:
                response += f"\n- {len(other_files)} autres fichiers"
            return response
        return "Projet non indexé. Tape 'selfdev' en interactif pour commencer."

    # Questions sur les fichiers
    if any(word in q for word in ['fichier', 'file', 'dans']):
        if index:
            return f"Fichiers disponibles: {', '.join(index[:10])}"
        return "Index non chargé. Tape 'selfdev' en interactif."

    return (
        "Je suis en mode offline. Lance Ollama pour activer le routage intelligent.\n"
        "En attendant, tu peux utiliser les commandes: locate, read, edit, append, delete, ask"
    )


def cmd_run(args=None):
    root = os.getcwd()
    _self = os.path.abspath(__file__)
    candidates = ['main.py', 'app.py', 'run.py', 'server.py', 'index.py']
    entry = None
    for name in candidates:
        path = os.path.join(root, name)
        if os.path.isfile(path) and os.path.abspath(path) != _self:
            entry = path
            break
    if not entry:
        for sub in ('src', 'app'):
            for name in candidates:
                path = os.path.join(root, sub, name)
                if os.path.isfile(path):
                    entry = path
                    break
    if not entry:
        print("Aucun point d'entrée trouvé (main.py, app.py, run.py...).")
        return
    print(f"Lancement: {os.path.relpath(entry, root)}")
    try:
        subprocess.run([sys.executable, entry], cwd=os.path.dirname(entry))
    except KeyboardInterrupt:
        print("\nArrêté.")


def repl():
    print("Mode interactif SELF_DEV_AGENT. Tapez 'help' pour l'aide, 'exit' pour quitter.")
    while True:
        try:
            cmd = input("selfdev> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSortie.")
            return

        if not cmd:
            continue
        if cmd in ("exit", "quit"):
            print("Sortie.")
            return
        if cmd == "help":
            print("Commandes disponibles :\n  ask <question libre>\n  locate <nom>\n  read <fichier>\n  edit <fichier> <instruction>\n  append <fichier> <instruction>\n  delete <fichier> <symbole>\n  create <fichier> <description>\n  prompt/init/build - Charge et applique le fichier prompt.txt\n  exit")
            continue

        command, _, rest = cmd.partition(' ')
        if command == 'exit' and not rest:
            print("Sortie.")
            return
        if command == 'help' and not rest:
            print("Commandes disponibles :\n  <requête libre>\n  locate <nom>\n  read <fichier>\n  edit <fichier> <instruction>\n  append <fichier> <instruction>\n  delete <fichier> <symbole>\n  prompt/init/build\n  exit")
            continue

        if command in ('locate', 'read', 'edit', 'append', 'delete', 'ask', 'create', 'prompt', 'init', 'build', 'setup', 'run'):
            if command == 'locate' and rest:
                cmd_locate(argparse.Namespace(name=rest.strip()))
            elif command == 'read' and rest:
                cmd_read(argparse.Namespace(file=rest.strip()))
            elif command == 'edit' and rest:
                file_path, _, instruction = rest.partition(' ')
                if not instruction:
                    print("Usage: edit <fichier> <instruction>")
                    continue
                cmd_edit(argparse.Namespace(file=file_path.strip(), instruction=instruction.strip()))
            elif command == 'append' and rest:
                file_path, _, instruction = rest.partition(' ')
                if not instruction:
                    print("Usage: append <fichier> <instruction>")
                    continue
                cmd_append(argparse.Namespace(file=file_path.strip(), instruction=instruction.strip()))
            elif command == 'delete' and rest:
                file_path, _, instruction = rest.partition(' ')
                if not instruction:
                    print("Usage: delete <fichier> <symbole ou instruction>")
                    continue
                cmd_delete(argparse.Namespace(file=file_path.strip(), instruction=instruction.strip(), symbol=''))
            elif command == 'ask' and rest:
                cmd_ask(argparse.Namespace(query=rest.strip()))
            elif command == 'create' and rest:
                file_path, _, instruction = rest.partition(' ')
                if file_path:
                    dispatch_action('create', {'action': 'create', 'file': file_path.strip(), 'instruction': instruction.strip()}, rest)
                else:
                    dispatch_action('create', {'action': 'create', 'file': '', 'instruction': rest}, rest)
            elif command in ('prompt', 'init', 'build', 'setup'):
                dispatch_action('prompt', {'action': 'prompt', 'instruction': ''}, command)
            elif command == 'run':
                cmd_run()
            else:
                print("Commande incomplète. Tapez help.")
        else:
            cmd_ask(argparse.Namespace(query=cmd))


def main():
    parser = argparse.ArgumentParser(description="SELF_DEV_AGENT - Agent autonome pour projet.")
    subparsers = parser.add_subparsers(dest='command')

    locate_parser = subparsers.add_parser('locate', help='Localiser une fonction ou un symbole.')
    locate_parser.add_argument('name', help='Nom à rechercher.')
    locate_parser.set_defaults(func=cmd_locate)

    edit_parser = subparsers.add_parser('edit', help='Éditer un fichier selon une instruction.')
    edit_parser.add_argument('file', help='Chemin relatif du fichier à éditer.')
    edit_parser.add_argument('instruction', help="Instruction d'édition.")
    edit_parser.set_defaults(func=cmd_edit)

    ask_parser = subparsers.add_parser('ask', help='Envoyer une requête libre à Ollama.')
    ask_parser.add_argument('query', help='Question ou instruction libre.')
    ask_parser.set_defaults(func=cmd_ask)

    delete_parser = subparsers.add_parser('delete', help='Supprimer une fonction, une classe ou un symbole Python.')
    delete_parser.add_argument('file', help='Chemin relatif du fichier cible.')
    delete_parser.add_argument('instruction', help='Instruction ou nom du symbole à supprimer.')
    delete_parser.set_defaults(func=cmd_delete)

    if len(sys.argv) == 1:
        repl()
        return

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()



if __name__ == '__main__':
    main()
