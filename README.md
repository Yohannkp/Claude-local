# SELF_DEV_AGENT

Agent autonome de développement local alimenté par Ollama. Comprend tes intentions, lit ton code, le modifie, l'édite et le supprime — en langage naturel.

## Installation

### Installation rapide (recommandée)

Un seul script gère tout : détection de la machine, installation d'Ollama, téléchargement des modèles adaptés à ta RAM, configuration du PATH et des variables d'environnement.

```bash
# Linux / macOS / Windows (Python 3.8+ requis)
python install.py --yes
```

Puis dans un **nouveau terminal** :

```bash
selfdev
```

Options utiles :

| Flag | Effet |
|------|-------|
| `--yes` | Tout confirmer automatiquement (vrai one-click) |
| `--model qwen2.5-coder:7b` | Forcer un modèle de routage |
| `--skip-ollama` | Ne pas installer/configurer Ollama |
| `--no-path` | Ne pas modifier le PATH utilisateur |
| `--uninstall` | Retirer SELF_DEV_AGENT du PATH et nettoyer les variables (laisse Ollama et les modèles intacts) |

### Installation manuelle

1. Copie le dossier `SELF_DEV_AGENT` à la racine de ton projet
2. Assure-toi que Python 3.8+ et Ollama sont disponibles
3. Pull manuellement un modèle : `ollama pull qwen2.5-coder:7b`
4. (Optionnel) Ajoute le dossier à ton `PATH` pour accès global

```powershell
# Exemple avec selfdev.bat dans PATH
selfdev
```

## Utilisation

Lance `selfdev` en interactif et parle librement :

```powershell
selfdev> salut
selfdev> il y a quoi dans main.py ?
selfdev> lis le fichier scanner.py
selfdev> supprime la fonction old_handler dans utils.py
selfdev> où est la logique d'auth ?
selfdev> modifie le parser pour mieux gérer les erreurs
selfdev> ajoute une fonction de logging à la fin de brain.py
selfdev> corrige le bug dans le système de routing
selfdev> explique ce que fait ce projet
```

Commandes explicites également disponibles :

| Commande | Exemple |
|----------|---------|
| `locate <nom>` | `locate parse` |
| `read <fichier>` | `read main.py` |
| `edit <fichier> <instruction>` | `edit utils.py ajoute validation` |
| `append <fichier> <instruction>` | `append brain.py nouvelle fonction` |
| `delete <fichier> <symbole>` | `delete main.py old_func` |
| `ask <question>` | `ask pourquoi ça plante ?` |

## Comment ça fonctionne

### Architecture

```
Utilisateur (requête en français)
         │
         ▼
┌─────────────────┐
│  Routage        │ ◄── Inference locale (regex) + Ollama
│  (comprendre)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Actions        │ ◄── read, edit, append, delete, locate, answer
│  (exécuter)     │
└─────────────────┘
```

### Flux de traitement

1. **Indexation** : Au premier lancement, le projet est scanné et indexé dans `.knowledge_base/file_index.json`

2. **Compréhension** : La requête est analysée en 2 phases :
   - Détection locale (regex) pour les cas évidents
   - Delegation à Ollama pour les cas complexes ou ambigus

3. **Exécution** : L'action appropriée est exécutée avec sécurité (backup, validation)

### Types de requêtes comprises

| Type | Exemples |
|------|----------|
| **Lecture** | `lis main.py`, `contenu de utils.py`, `c quoi dans le fichier auth.py ?` |
| **Suppression** | `supprime toto()`, `vire la fonction old_handler`, `delete process_data` |
| **Édition** | `modifie le fichier parser`, `corrige le bug dans routing`, `ajoute validation email` |
| **Ajout** | `ajoute une fonction à la fin de main.py`, `append dans brain.py` |
| **Localisation** | `où est la logique d'auth ?`, `trouve le fichier de config`, `localise process` |
| **Question** | `explique le projet`, `comment fonctionne le router ?` |

### Compréhension contextuelle

L'agent déduit l'intention derrière les mots, pas juste les mots eux-mêmes :

- `"corrige le bug"` → identifie le fichier pertinent, génère une correction
- `"supprime cette fonction"` → comprend de quelle fonction il s'agit
- `"ajoute de la validation"` → trouve où ajouter le code et génère un pattern approprié
- `"le fichier d'auth"` → cherche `auth.py`, `login.py`, etc.

## Configuration

### Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `SELF_DEV_AGENT_MODEL` | `qwen2.5-coder:7b` | Modèle Ollama à utiliser |

### Ollama

- Serveur attendu sur `http://localhost:11434`
- Fonctionne en mode dégradé si Ollama indisponible (détection locale uniquement)
- Modèle recommandé : `qwen2.5-coder:7b` ou équivalent

## Sécurité

- **Backup automatique** : `.bak` créé avant toute modification
- **Validation syntaxe** : vérification Python après génération de code
- **Restauration automatique** : rollback si erreur détectée
- **Vérification taille** : refuse les fichiers vidés accidentellement

## Structure du projet

```
SELF_DEV_AGENT/
├── main.py       # Point d'entrée CLI + REPL interactif
├── brain.py      # Cerveau : routage, Ollama, édition, suppression
├── scanner.py    # Indexation du projet
├── install.py    # Installeur cross-platform (Windows/Linux/macOS)
├── selfdev.bat   # Lanceur Windows
├── selfdev       # Lanceur Unix (créé par install.py)
└── README.md     # Ce fichier
```

Données locales :

```
.knowledge_base/
└── file_index.json  # Index des fichiers du projet
```

## Prérequis

- Python 3.8+
- Ollama installé et accessible localement (ou utiliser `install.py` pour le faire automatiquement)
- Modèle LLM disponible dans Ollama (recommandation auto par `install.py` selon ta RAM)