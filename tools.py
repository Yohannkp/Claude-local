"""Outils exposés à l'agent via le tool-calling Ollama.

Chaque outil a un schéma JSON (format OpenAI-compatible, supporté par Ollama)
et une fonction d'exécution qui retourne du texte (jamais d'exception brute :
les erreurs sont renvoyées comme résultat pour que l'agent puisse s'adapter).
"""

import fnmatch
import os
import re
import subprocess
import sys

import brain


TOOL_SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'list_files',
            'description': "Liste les fichiers indexés du projet, filtrés par un motif glob optionnel (ex: '*.py', 'src/*').",
            'parameters': {
                'type': 'object',
                'properties': {
                    'pattern': {'type': 'string', 'description': "Motif glob optionnel, ex '*.py'"},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'grep',
            'description': "Cherche une expression régulière dans le contenu des fichiers du projet. Retourne des lignes 'fichier:ligne: contenu'. Utilise ceci pour localiser du code avant de lire ou éditer.",
            'parameters': {
                'type': 'object',
                'properties': {
                    'pattern': {'type': 'string', 'description': 'Expression régulière ou texte littéral à chercher'},
                    'file_glob': {'type': 'string', 'description': "Motif glob optionnel pour limiter les fichiers, ex '*.py'"},
                },
                'required': ['pattern'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'read_file',
            'description': "Lit un fichier du projet avec numéros de ligne. Utilise start_line/end_line pour cibler un extrait sur un gros fichier. Toujours lire avant d'appeler edit_file.",
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Chemin ou nom du fichier'},
                    'start_line': {'type': 'integer'},
                    'end_line': {'type': 'integer'},
                },
                'required': ['path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'edit_file',
            'description': (
                "Modifie un fichier existant en remplaçant une portion exacte de texte (old_string) par new_string. "
                "old_string doit correspondre EXACTEMENT (espaces et indentation compris) à un extrait obtenu via "
                "read_file, et doit être unique dans le fichier sauf si replace_all=true. Ne réécris jamais le fichier "
                "entier : cible la plus petite portion suffisante."
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'old_string': {'type': 'string'},
                    'new_string': {'type': 'string'},
                    'replace_all': {'type': 'boolean', 'description': 'Remplacer toutes les occurrences (défaut: false)'},
                },
                'required': ['path', 'old_string', 'new_string'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'create_file',
            'description': "Crée un nouveau fichier avec le contenu fourni. Échoue si le fichier existe déjà (utilise edit_file dans ce cas).",
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'content': {'type': 'string'},
                },
                'required': ['path', 'content'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'run_python',
            'description': (
                "Exécute un script ou fichier de tests Python et retourne sa sortie (stdout/stderr) et son code de "
                "retour. Utilise ceci pour VÉRIFIER que le code généré fonctionne réellement, en particulier après "
                "avoir créé un fichier de tests. N'utilise JAMAIS ceci sur un serveur qui tourne en boucle (ex: un "
                "app.run() Flask/Django) : ça timeoutera sans donner d'information utile."
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Chemin du script ou fichier de tests à exécuter'},
                    'args': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Arguments de ligne de commande optionnels'},
                    'timeout': {'type': 'integer', 'description': 'Délai maximum en secondes (défaut 15, max 30)'},
                },
                'required': ['path'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'delete_symbol',
            'description': "Supprime une fonction, méthode ou classe Python identifiée par son nom exact dans un fichier.",
            'parameters': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string'},
                    'symbol': {'type': 'string'},
                },
                'required': ['path', 'symbol'],
            },
        },
    },
]


def _err(message):
    return f"ERREUR: {message}"


def tool_list_files(pattern=None):
    index = brain.load_index()
    if pattern:
        index = [f for f in index if fnmatch.fnmatch(f.replace('\\', '/'), pattern)]
    if not index:
        return "Aucun fichier trouvé."
    if len(index) > 300:
        return '\n'.join(index[:300]) + f"\n... et {len(index) - 300} autres fichiers"
    return '\n'.join(index)


def tool_grep(pattern, file_glob=None):
    try:
        regex = re.compile(pattern)
    except re.error as error:
        return _err(f"Expression régulière invalide: {error}")

    root = brain.project_root()
    matches = []
    for rel_path in brain.load_index():
        if file_glob and not fnmatch.fnmatch(rel_path.replace('\\', '/'), file_glob):
            continue
        abs_path = os.path.join(root, rel_path)
        if not os.path.isfile(abs_path):
            continue
        try:
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
                for line_number, line in enumerate(file_handle, 1):
                    if regex.search(line):
                        matches.append(f"{rel_path}:{line_number}: {line.strip()}")
                        if len(matches) >= 200:
                            break
        except OSError:
            continue
        if len(matches) >= 200:
            break

    if not matches:
        return "Aucune occurrence trouvée."
    return '\n'.join(matches)


def tool_read_file(path, start_line=None, end_line=None):
    resolved = brain.resolve_file_path(path)
    if not resolved:
        candidates = brain.find_file_candidates(path)
        if candidates:
            return _err(f"Fichier ambigu pour '{path}', précise parmi: {', '.join(candidates)}")
        return _err(f"Fichier introuvable: {path}")

    abs_path = os.path.join(brain.project_root(), resolved)
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            lines = file_handle.readlines()
    except OSError as error:
        return _err(str(error))

    total = len(lines)
    start = max(1, start_line or 1)
    end = min(total, end_line or total)
    if not end_line and total > 400:
        end = 400

    numbered = [f"{i}: {lines[i - 1].rstrip()}" for i in range(start, end + 1)]
    truncated_note = f" (tronqué, {total} lignes au total, précise end_line pour voir la suite)" if end < total else ""
    header = f"--- {resolved} (lignes {start}-{end} sur {total}){truncated_note} ---"
    return header + '\n' + '\n'.join(numbered)


def tool_edit_file(path, old_string, new_string, replace_all=False):
    resolved = brain.resolve_file_path(path) or path
    abs_path = os.path.join(brain.project_root(), resolved)
    if not os.path.isfile(abs_path):
        return _err(f"Fichier introuvable: {path}")

    if old_string == new_string:
        return _err("old_string et new_string sont identiques, rien à faire.")

    backup_path = brain.create_backup(abs_path)
    original_size = os.path.getsize(abs_path)
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            content = file_handle.read()

        count = content.count(old_string)
        if count == 0:
            raise RuntimeError("old_string introuvable dans le fichier. Relis le fichier pour récupérer le texte exact.")
        if count > 1 and not replace_all:
            raise RuntimeError(f"old_string apparaît {count} fois, ce n'est pas assez précis. Ajoute plus de contexte autour, ou passe replace_all=true.")

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        with open(abs_path, 'w', encoding='utf-8') as file_handle:
            file_handle.write(new_content)

        brain.validate_python_syntax(abs_path)

        if not brain.validate_written_file(original_size, abs_path):
            raise RuntimeError("le fichier résultant est trop petit ou vide")

        return f"Fichier '{resolved}' modifié avec succès ({count if replace_all else 1} remplacement(s))."
    except Exception as error:
        brain.restore_backup(backup_path, abs_path)
        return _err(f"Modification annulée: {error}")
    finally:
        try:
            os.remove(backup_path)
        except OSError:
            pass


def tool_create_file(path, content):
    abs_path = os.path.join(brain.project_root(), path.lstrip('/\\'))
    if os.path.isfile(abs_path):
        return _err(f"Le fichier '{path}' existe déjà. Utilise edit_file pour le modifier.")
    try:
        brain.create_file(path, content)
        return f"Fichier '{path}' créé avec succès."
    except Exception as error:
        return _err(str(error))


def tool_run_python(path, args=None, timeout=15):
    resolved = brain.resolve_file_path(path)
    if not resolved:
        candidates = brain.find_file_candidates(path)
        if candidates:
            return _err(f"Fichier ambigu pour '{path}', précise parmi: {', '.join(candidates)}")
        return _err(f"Fichier introuvable: {path}")

    abs_path = os.path.join(brain.project_root(), resolved)
    safe_timeout = min(max(int(timeout or 15), 1), 30)

    try:
        with open(abs_path, 'r', encoding='utf-8', errors='ignore') as file_handle:
            source = file_handle.read()
    except OSError as error:
        return _err(str(error))

    # Un fichier de tests unittest sans bloc `if __name__ == '__main__': unittest.main()`
    # s'exécute silencieusement sans rien lancer (faux positif de vérification). On le
    # lance donc toujours via `python -m unittest`, qui découvre et exécute les
    # TestCase quel que soit le contenu du fichier.
    is_unittest_file = resolved.endswith('.py') and ('unittest.TestCase' in source or 'import unittest' in source)
    if is_unittest_file:
        module_name = resolved[:-3].replace('\\', '/').replace('/', '.')
        command = [sys.executable, '-m', 'unittest', module_name, '-v'] + [str(a) for a in (args or [])]
    else:
        command = [sys.executable, abs_path] + [str(a) for a in (args or [])]

    try:
        result = subprocess.run(
            command,
            cwd=brain.project_root(),
            capture_output=True,
            text=True,
            timeout=safe_timeout,
        )
    except subprocess.TimeoutExpired:
        return _err(
            f"'{resolved}' n'a pas terminé après {safe_timeout}s (probablement un serveur ou une boucle infinie, "
            "pas forcément une erreur si c'est voulu)."
        )
    except OSError as error:
        return _err(str(error))

    output = ((result.stdout or '') + (result.stderr or ''))[-4000:]

    if is_unittest_file and not re.search(r'Ran \d+ tests?', output):
        return _err(
            f"'{resolved}' contient des tests unittest mais aucun test n'a été détecté/exécuté. "
            f"Sortie brute: {output.strip() or '(vide)'}"
        )

    status = 'succès' if result.returncode == 0 else f'échec (code retour {result.returncode})'
    return f"Exécution de '{resolved}' : {status}\n{output}".rstrip()


def tool_delete_symbol(path, symbol):
    try:
        resolved = brain.delete_python_symbol(path, symbol)
        return f"Symbole '{symbol}' supprimé de '{resolved}' avec succès."
    except RuntimeError as error:
        return _err(str(error))


_HANDLERS = {
    'list_files': tool_list_files,
    'grep': tool_grep,
    'read_file': tool_read_file,
    'edit_file': tool_edit_file,
    'create_file': tool_create_file,
    'run_python': tool_run_python,
    'delete_symbol': tool_delete_symbol,
}


def execute_tool(name, args):
    handler = _HANDLERS.get(name)
    if not handler:
        return _err(f"Outil inconnu: {name}")
    try:
        return handler(**(args or {}))
    except TypeError as error:
        return _err(f"Arguments invalides pour {name}: {error}")
    except Exception as error:
        return _err(f"Erreur inattendue dans {name}: {error}")
