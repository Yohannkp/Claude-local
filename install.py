"""Installeur cross-platform pour SELF_DEV_AGENT.

Usage:
    python install.py            # interactif
    python install.py --yes      # tout automatique
    python install.py --uninstall

Stdlib uniquement. Compatible Windows, Linux, macOS.
Telecharge automatiquement le projet depuis GitHub si necessaire.
"""

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

GITHUB_REPO = 'https://github.com/Yohannkp/Claude-local.git'
REPO_NAME = 'Claude-local'

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
OLLAMA_API = 'http://localhost:11434/api/tags'
OLLAMA_DOWNLOADS = {
    'Windows': 'https://ollama.com/download/OllamaSetup.exe',
    'Linux': 'https://ollama.com/install.sh',
    'Darwin': 'https://ollama.com/download/Ollama-darwin.zip',
}
ENV_VAR_NAME = 'SELF_DEV_AGENT_MODEL'
RC_FILES = ['.bashrc', '.zshrc', '.profile']
RC_BLOCK_START = '# >>> SELF_DEV_AGENT >>>'
RC_BLOCK_END = '# <<< SELF_DEV_AGENT <<<'


# ----------------------------- helpers -----------------------------

def log(msg, level='info'):
    prefix = {
        'info': '[*]',
        'ok': '[OK]',
        'warn': '[!]',
        'err': '[X]',
        'step': '\n==>',
    }.get(level, '[*]')
    print(f"{prefix} {msg}")


def confirm(question, auto_yes=False):
    if auto_yes:
        return True
    try:
        ans = input(f"{question} [o/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ('o', 'oui', 'y', 'yes')


def run(cmd, check=True, capture=False, shell=False):
    try:
        if capture:
            result = subprocess.run(cmd, check=check, shell=shell, capture_output=True, text=True)
            return result.stdout.strip()
        subprocess.run(cmd, check=check, shell=shell)
        return ''
    except subprocess.CalledProcessError as e:
        if check:
            raise
        return e.stdout if capture else ''


# ----------------------------- detection -----------------------------

def get_total_ram_gb():
    system = platform.system()
    try:
        if system == 'Windows':
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_uint32),
                    ('dwMemoryLoad', ctypes.c_uint32),
                    ('ullTotalPhys', ctypes.c_uint64),
                    ('ullAvailPhys', ctypes.c_uint64),
                    ('ullTotalPageFile', ctypes.c_uint64),
                    ('ullAvailPageFile', ctypes.c_uint64),
                    ('ullTotalVirtual', ctypes.c_uint64),
                    ('ullAvailVirtual', ctypes.c_uint64),
                    ('ullAvailExtendedVirtual', ctypes.c_uint64),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
        if system == 'Linux':
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2)
        if system == 'Darwin':
            out = run(['sysctl', '-n', 'hw.memsize'], capture=True)
            return int(out) / (1024 ** 3)
    except Exception as e:
        log(f"Detection RAM impossible: {e}", 'warn')
    return 8.0


def get_free_disk_gb(path=None):
    path = path or SCRIPT_DIR
    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except Exception:
        return 100.0


def detect_system():
    info = {
        'os': platform.system(),
        'os_release': platform.release(),
        'arch': platform.machine(),
        'python': platform.python_version(),
        'cpu_count': os.cpu_count() or 1,
        'ram_gb': round(get_total_ram_gb(), 1),
        'disk_free_gb': round(get_free_disk_gb(), 1),
    }
    return info


def check_python_version():
    if sys.version_info < (3, 8):
        log(f"Python 3.8+ requis, version actuelle: {platform.python_version()}", 'err')
        sys.exit(1)


# ----------------------------- ollama -----------------------------

def ollama_running():
    try:
        with urllib.request.urlopen(OLLAMA_API, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ollama_installed():
    return shutil.which('ollama') is not None


def ollama_list_models():
    if not ollama_running():
        return []
    try:
        with urllib.request.urlopen(OLLAMA_API, timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
            return [m['name'] for m in data.get('models', [])]
    except Exception:
        return []


# ----------------------------- model recommendation -----------------------------

def recommend_models(ram_gb):
    if ram_gb < 6:
        return {
            'router': 'qwen2.5-coder:1.5b',
            'coder': 'deepseek-coder:1.3b',
            'estimated_size_gb': 2,
            'tier': 'low',
        }
    if ram_gb < 12:
        return {
            'router': 'qwen2.5-coder:3b',
            'coder': 'deepseek-coder:1.3b',
            'estimated_size_gb': 3,
            'tier': 'medium',
        }
    if ram_gb < 24:
        return {
            'router': 'qwen2.5-coder:7b',
            'coder': 'deepseek-coder:6.7b',
            'estimated_size_gb': 8,
            'tier': 'high',
        }
    return {
        'router': 'qwen2.5-coder:14b',
        'coder': 'deepseek-coder:6.7b',
        'estimated_size_gb': 13,
        'tier': 'very_high',
    }


def model_match(installed_name, target_name):
    """Compare 'qwen2.5-coder:7b' avec 'qwen2.5-coder:7b-instruct-q4_0'."""
    a = installed_name.split(':')[0]
    b = target_name.split(':')[0]
    if a != b:
        return False
    a_tag = installed_name.split(':')[1] if ':' in installed_name else ''
    b_tag = target_name.split(':')[1] if ':' in target_name else ''
    return a_tag.startswith(b_tag) or b_tag.startswith(a_tag)


# ----------------------------- ollama install -----------------------------

def install_ollama_windows(auto_yes):
    if not confirm("Installer Ollama (Windows, ~600 MB) ?", auto_yes):
        log("Installation Ollama annulee.", 'warn')
        return False
    url = OLLAMA_DOWNLOADS['Windows']
    target = os.path.join(os.environ.get('TEMP', SCRIPT_DIR), 'OllamaSetup.exe')
    log(f"Telechargement: {url}")
    urllib.request.urlretrieve(url, target)
    log("Lancement de l'installeur (silencieux)...")
    run([target, '/SILENT'], check=False)
    return shutil.which('ollama') is not None


def install_ollama_linux(auto_yes):
    if not confirm("Installer Ollama (Linux, via curl | sh) ?", auto_yes):
        log("Installation Ollama annulee.", 'warn')
        return False
    log("Execution: curl -fsSL https://ollama.com/install.sh | sh")
    run('curl -fsSL https://ollama.com/install.sh | sh', shell=True, check=True)
    return shutil.which('ollama') is not None


def install_ollama_macos(auto_yes):
    if not confirm("Installer Ollama (macOS, ~300 MB) ?", auto_yes):
        log("Installation Ollama annulee.", 'warn')
        return False
    url = OLLAMA_DOWNLOADS['Darwin']
    target = os.path.join('/tmp', 'Ollama-darwin.zip')
    log(f"Telechargement: {url}")
    urllib.request.urlretrieve(url, target)
    log("Extraction dans /Applications/...")
    run(['unzip', '-o', target, '-d', '/Applications/'], check=False)
    run(['open', '/Applications/Ollama.app'], check=False)
    return shutil.which('ollama') is not None


def install_ollama(auto_yes):
    system = platform.system()
    if system == 'Windows':
        return install_ollama_windows(auto_yes)
    if system == 'Linux':
        return install_ollama_linux(auto_yes)
    if system == 'Darwin':
        return install_ollama_macos(auto_yes)
    log(f"OS non supporte: {system}", 'err')
    return False


def start_ollama_service():
    if ollama_running():
        return True
    log("Demarrage du service Ollama...")
    system = platform.system()
    try:
        if system == 'Windows':
            subprocess.Popen(['ollama', 'serve'], creationflags=0x00000008)  # DETACHED_PROCESS
        else:
            subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        log("Binaire 'ollama' introuvable.", 'err')
        return False
    import time
    for _ in range(15):
        if ollama_running():
            return True
        time.sleep(1)
    return False


def pull_model(name):
    log(f"Pull du modele {name} (cela peut prendre plusieurs minutes)...")
    run(['ollama', 'pull', name], check=True)
    log(f"Modele {name} pret.", 'ok')


# ----------------------------- launcher -----------------------------

def create_unix_launcher():
    if platform.system() == 'Windows':
        return None
    path = os.path.join(SCRIPT_DIR, 'selfdev')
    content = (
        '#!/usr/bin/env bash\n'
        'exec python3 "$(dirname "$0")/main.py" "$@"\n'
    )
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    os.chmod(path, 0o755)
    return path


# ----------------------------- PATH (Windows) -----------------------------

def windows_get_user_path():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_READ) as key:
        try:
            value, _ = winreg.QueryValueEx(key, 'Path')
            return value
        except FileNotFoundError:
            return ''


def windows_set_user_env(name, value):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    # Broadcast WM_SETTINGCHANGE pour propager sans reboot
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    result = ctypes.c_long()
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, 'Environment',
        SMTO_ABORTIFHUNG, 5000, ctypes.byref(result)
    )


def windows_delete_user_env(name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def windows_add_to_path(directory):
    current = windows_get_user_path()
    parts = [p for p in current.split(';') if p]
    if directory in parts:
        log(f"PATH deja a jour: {directory}", 'ok')
        return
    parts.append(directory)
    new_path = ';'.join(parts)
    windows_set_user_env('Path', new_path)
    log(f"Ajoute au PATH utilisateur: {directory}", 'ok')


def windows_remove_from_path(directory):
    current = windows_get_user_path()
    parts = [p for p in current.split(';') if p and p != directory]
    new_path = ';'.join(parts)
    windows_set_user_env('Path', new_path)


# ----------------------------- PATH (Unix) -----------------------------

def unix_rc_paths():
    home = os.path.expanduser('~')
    return [os.path.join(home, name) for name in RC_FILES]


def unix_block(directory, model):
    return (
        f"\n{RC_BLOCK_START}\n"
        f'export PATH="$PATH:{directory}"\n'
        f'export {ENV_VAR_NAME}="{model}"\n'
        f"{RC_BLOCK_END}\n"
    )


def unix_strip_block(content):
    if RC_BLOCK_START not in content:
        return content, False
    start = content.index(RC_BLOCK_START)
    end_marker_pos = content.find(RC_BLOCK_END, start)
    if end_marker_pos == -1:
        return content, False
    end = end_marker_pos + len(RC_BLOCK_END)
    while end < len(content) and content[end] == '\n':
        end += 1
    new_content = content[:start].rstrip('\n') + '\n' + content[end:]
    return new_content, True


def unix_add_to_path(directory, model):
    block = unix_block(directory, model)
    for rc_path in unix_rc_paths():
        try:
            existing = ''
            if os.path.exists(rc_path):
                with open(rc_path, 'r', encoding='utf-8') as f:
                    existing = f.read()
            stripped, had_block = unix_strip_block(existing)
            new_content = stripped.rstrip('\n') + '\n' + block if stripped.strip() else block
            with open(rc_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            verb = "Mise a jour" if had_block else "Ajout"
            log(f"{verb} dans {rc_path}", 'ok')
        except Exception as e:
            log(f"Echec {rc_path}: {e}", 'warn')


def unix_remove_from_path():
    for rc_path in unix_rc_paths():
        if not os.path.exists(rc_path):
            continue
        try:
            with open(rc_path, 'r', encoding='utf-8') as f:
                content = f.read()
            new_content, had_block = unix_strip_block(content)
            if had_block:
                with open(rc_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                log(f"Bloc retire de {rc_path}", 'ok')
        except Exception as e:
            log(f"Echec {rc_path}: {e}", 'warn')


# ----------------------------- bootstrap (clone depuis GitHub) -----------------------------

def git_available():
    return shutil.which('git') is not None


def bootstrap(auto_yes):
    """Clone le repo si ce script est lance hors du projet."""
    # Si main.py est deja present a cote de ce script, rien a faire
    if os.path.exists(os.path.join(SCRIPT_DIR, 'main.py')):
        return SCRIPT_DIR

    log("Projet non detecte localement. Clonage depuis GitHub...", 'step')

    if not git_available():
        log("git est requis pour telecharger le projet. Installe-le depuis https://git-scm.com", 'err')
        sys.exit(1)

    home = os.path.expanduser('~')
    default_dest = os.path.join(home, REPO_NAME)

    if auto_yes:
        dest = default_dest
    else:
        try:
            ans = input(f"Dossier d'installation [{default_dest}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ''
        dest = ans if ans else default_dest

    if os.path.exists(dest):
        log(f"Le dossier {dest} existe deja. Mise a jour (git pull)...", 'warn')
        run(['git', '-C', dest, 'pull'], check=False)
    else:
        log(f"Clonage dans {dest}...")
        run(['git', 'clone', GITHUB_REPO, dest], check=True)

    log(f"Projet disponible dans {dest}", 'ok')

    # Relancer install.py depuis le projet clone
    new_installer = os.path.join(dest, 'install.py')
    if not os.path.exists(new_installer):
        log("install.py introuvable dans le repo clone.", 'err')
        sys.exit(1)

    log("Relancement de l'installeur depuis le projet...", 'step')
    os.execv(sys.executable, [sys.executable, new_installer] + sys.argv[1:])


# ----------------------------- orchestration -----------------------------

def do_install(args):
    log("Detection du systeme...", 'step')
    info = detect_system()
    for k, v in info.items():
        log(f"  {k}: {v}")
    check_python_version()

    log("Recommandation de modele...", 'step')
    rec = recommend_models(info['ram_gb'])
    router_model = args.model or rec['router']
    coder_model = rec['coder']
    log(f"  Tier: {rec['tier']}")
    log(f"  Modele routage: {router_model}")
    log(f"  Modele code: {coder_model}")
    log(f"  Taille estimee: ~{rec['estimated_size_gb']} GB")

    free = info['disk_free_gb']
    if free < rec['estimated_size_gb'] + 2:
        log(f"Espace disque insuffisant: {free} GB libres, ~{rec['estimated_size_gb'] + 2} GB requis", 'err')
        return 1

    if not args.skip_ollama:
        log("Verification d'Ollama...", 'step')
        if ollama_installed():
            log("Ollama deja installe.", 'ok')
        else:
            if not install_ollama(args.yes):
                log("Echec installation Ollama. Lien manuel: https://ollama.com/download", 'err')
                return 1

        if not start_ollama_service():
            log("Service Ollama non demarre. Lance 'ollama serve' manuellement.", 'err')
            return 1
        log("Service Ollama actif.", 'ok')

        log("Verification des modeles...", 'step')
        installed = ollama_list_models()
        for target in (router_model, coder_model):
            already = next((m for m in installed if model_match(m, target)), None)
            if already:
                log(f"Modele {target} deja present (sous le nom {already}).", 'ok')
                continue
            if not confirm(f"Pull du modele {target} ?", args.yes):
                log(f"Skip {target}", 'warn')
                continue
            try:
                pull_model(target)
            except subprocess.CalledProcessError as e:
                log(f"Echec pull {target}: {e}", 'err')

    if platform.system() != 'Windows':
        log("Creation du launcher Unix...", 'step')
        path = create_unix_launcher()
        if path:
            log(f"Launcher cree: {path}", 'ok')

    if not args.no_path:
        log("Configuration PATH et variables d'environnement...", 'step')
        if platform.system() == 'Windows':
            windows_add_to_path(SCRIPT_DIR)
            windows_set_user_env(ENV_VAR_NAME, router_model)
            log(f"{ENV_VAR_NAME}={router_model} (utilisateur)", 'ok')
        else:
            unix_add_to_path(SCRIPT_DIR, router_model)

    log("Recapitulatif", 'step')
    print(f"  [OK] OS: {info['os']} {info['os_release']}")
    print(f"  [OK] Python: {info['python']}")
    if not args.skip_ollama:
        print(f"  [OK] Ollama actif sur localhost:11434")
        print(f"  [OK] Modele routage: {router_model}")
        print(f"  [OK] Modele code: {coder_model}")
    if not args.no_path:
        print(f"  [OK] PATH utilisateur configure")
        print(f"  [OK] {ENV_VAR_NAME}={router_model}")
    print()
    print("=> Ouvre un NOUVEAU terminal et lance: selfdev")
    return 0


def do_uninstall(args):
    log("Desinstallation (Ollama et modeles conserves)", 'step')
    if platform.system() == 'Windows':
        windows_remove_from_path(SCRIPT_DIR)
        windows_delete_user_env(ENV_VAR_NAME)
        log("PATH utilisateur nettoye et variable supprimee.", 'ok')
    else:
        unix_remove_from_path()
        launcher = os.path.join(SCRIPT_DIR, 'selfdev')
        if os.path.exists(launcher):
            try:
                os.remove(launcher)
                log(f"Launcher retire: {launcher}", 'ok')
            except OSError as e:
                log(f"Echec suppression launcher: {e}", 'warn')
    log("Desinstallation terminee.", 'ok')
    return 0


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--yes', action='store_true', help="Confirmer automatiquement.")
    p.add_argument('--model', help="Forcer un modele de routage specifique.")
    p.add_argument('--skip-ollama', action='store_true', help="Ne pas toucher a Ollama.")
    p.add_argument('--no-path', action='store_true', help="Ne pas modifier le PATH.")
    p.add_argument('--uninstall', action='store_true', help="Mode desinstallation.")
    return p.parse_args()


def main():
    args = parse_args()
    try:
        if not args.uninstall:
            bootstrap(args.yes)
        if args.uninstall:
            return do_uninstall(args)
        return do_install(args)
    except KeyboardInterrupt:
        log("Interrompu par l'utilisateur.", 'warn')
        return 130


if __name__ == '__main__':
    sys.exit(main())
