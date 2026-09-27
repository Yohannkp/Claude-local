# SELF_DEV_AGENT

**Un agent de développement autonome qui tourne entièrement sur votre machine.** Vous décrivez ce que vous voulez en français ; l'agent explore le projet, lit les fichiers, modifie le code, exécute les tests et vérifie son propre travail — sans aucun appel réseau, sans clé d'API, sans envoyer une ligne de votre code à un service tiers.

Le moteur est [Ollama](https://ollama.com) en local. C'est aussi ce qui fait l'intérêt du problème : un modèle de 7 milliards de paramètres qui tient sur un portable n'a pas la fiabilité d'un modèle hébergé, donc l'agent ne peut pas se contenter de générer du code et d'espérer. Il doit **vérifier**. L'essentiel du travail est là : boucle d'outils, exécution réelle des tests, détection de blocage, rollback.

```
selfdev> corrige le bug dans la fonction divide
  → grep divide          → trouvé dans calc.py:42
  → read_file calc.py    → lecture ciblée
  → edit_file calc.py    → division par zéro gérée
  → run_python tests.py  → 4 tests passés
  ✓ Corrigé et vérifié.
```

### Ce que ce projet démontre

| Sujet | Mise en œuvre |
|---|---|
| Boucle agentique | tool-calling natif Ollama, 20 étapes maximum, arrêt sur blocage ([`agent.py`](agent.py)) |
| Outils sûrs | édition par remplacement d'extrait exact, suppression de symbole par AST ([`tools.py`](tools.py)) |
| Garde-fous | sauvegarde `.bak`, validation syntaxique, rollback automatique, refus des fichiers vidés |
| Dégradation contrôlée | routage local par expressions régulières si Ollama est injoignable |
| Déploiement | installeur multi-plateforme qui dimensionne le modèle selon la RAM détectée ([`install.py`](install.py)) |

> Modèles recommandés : un modèle généraliste *instruct* (`qwen3`, `qwen2.5:*-instruct`, `llama3.1`) pour le mode agent — ils suivent mieux le format de tool-calling — et `qwen2.5-coder` pour la génération de code. `install.py` sélectionne automatiquement la taille adaptée à la RAM disponible.

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

## Mode agent

Toute requête en langage naturel qui n'est pas une commande explicite (`locate`, `read`, `edit`...) passe par une **boucle agentique** ([agent.py](agent.py)), inspirée du fonctionnement de Claude Code : le modèle explore, agit et vérifie lui-même, en enchaînant plusieurs appels d'outils sans intervention, jusqu'à ce que la tâche soit vraiment terminée (ou après 20 étapes maximum).

```
Requête utilisateur
         │
         ▼
┌──────────────────────────┐
│  Boucle agentique         │  ← Ollama tool-calling natif
│  (agent.py)               │
└────────────┬──────────────┘
             │  boucle jusqu'à réponse finale
             ▼
┌──────────────────────────┐
│  Outils (tools.py)        │
│  list_files · grep        │  ← exploration
│  read_file                │  ← lecture ciblée
│  edit_file · create_file  │  ← modification par diff
│  delete_symbol            │  ← suppression sûre (AST)
│  run_python                │  ← vérification (exécute les tests)
└──────────────────────────┘
```

### Outils disponibles pour l'agent

| Outil | Rôle |
|-------|------|
| `list_files` | Liste les fichiers du projet (motif glob optionnel) |
| `grep` | Cherche une regex dans le contenu des fichiers (`fichier:ligne: contenu`) |
| `read_file` | Lit un fichier avec numéros de ligne, extrait ciblé sur les gros fichiers |
| `edit_file` | Remplace un extrait exact (`old_string` → `new_string`), jamais le fichier entier |
| `create_file` | Crée un nouveau fichier |
| `delete_symbol` | Supprime une fonction/classe Python par son nom (AST) |
| `run_python` | Exécute un script ou un fichier de tests pour vérifier que le code fonctionne réellement |

L'agent enchaîne ces outils de façon autonome : par exemple pour "corrige le bug dans divide", il va typiquement `grep` → `read_file` → `edit_file` → `run_python` (si un fichier de tests existe) avant de répondre. Si une édition échoue (texte introuvable, ambigu), il relit le fichier et corrige son prochain essai au lieu d'abandonner.

Pour la génération de projets multi-fichiers ("crée une API Flask complète..."), l'agent enchaîne plusieurs `create_file` puis, quand c'est possible, exécute les tests générés avec `run_python` pour vérifier que le résultat fonctionne avant de conclure — plutôt que de s'arrêter à du code non testé.

### Choix du modèle

Les modèles `-coder` (ex: `qwen2.5-coder`) restent excellents pour la qualité du code généré, mais suivent moins bien le format de tool-calling structuré d'Ollama : ils répondent parfois en texte libre au lieu d'appeler un outil. Le mode agent sélectionne donc en priorité un modèle généraliste "instruct" fiable pour le tool-calling (`qwen3`, `qwen2.5:*-instruct`, `llama3.1`), et ne retombe sur les variantes `-coder` qu'en dernier recours. `SELF_DEV_AGENT_MODEL` reste prioritaire si défini et installé.

### Limites connues

- Les petits modèles locaux (7-8B) peuvent générer du code avec des bugs subtils dès la première tentative (ex: un objet non sérialisable en JSON). L'outil `run_python` permet à l'agent de s'en rendre compte et de se corriger lui-même s'il existe un test à exécuter — mais sans test, une revue humaine du résultat reste recommandée pour du code destiné à la production.
- `run_python` ne doit jamais être utilisé (et l'agent est instruit d'éviter ça) sur un serveur qui tourne en boucle (Flask/Django `app.run()`) : le script sera juste tué après le délai (15s par défaut).
- **Gros projets multi-fichiers (5+ fichiers avec dépendances croisées)** : quand la requête nomme plusieurs fichiers explicitement, l'agent établit d'abord un plan (rôle + imports attendus de chaque fichier, voir `_plan_files` dans [agent.py](agent.py)) avant d'écrire du code, pour limiter le risque qu'un fichier redéfinisse en double une classe déjà définie ailleurs. Ce n'est pas une garantie : avec un modèle 7B, le plan n'est pas toujours respecté à la lettre, et le résultat sur un projet conséquent reste probabiliste (testé : parfois entièrement correct et vérifié, parfois avec des classes dupliquées incohérentes). Une détection de blocage (`_is_stuck`) interrompt l'agent s'il tourne en boucle sur le même fichier (4 tentatives similaires sans succès) au lieu de épuiser tout le budget d'étapes en silence — mais elle ne corrige pas le problème, elle évite juste de gaspiller du temps. Pour un projet volumineux, une supervision par petites étapes reste plus fiable qu'un seul lâcher-prise.

### Architecture globale

```
Utilisateur (requête en français)
         │
         ▼
┌─────────────────┐
│  Commande        │ ◄── explicite (locate/read/edit/append/delete) ou libre
│  explicite ?     │
└────────┬────────┘
         │ non → langage naturel
         ▼
┌─────────────────┐
│  Boucle agentique│ ◄── voir "Mode agent" ci-dessus
└─────────────────┘
```

### Flux de traitement

1. **Indexation** : Au premier lancement, le projet est scanné et indexé dans `.knowledge_base/file_index.json`

2. **Compréhension** : Les commandes explicites sont traitées directement ; tout le reste part dans la boucle agentique, qui décide elle-même des outils à utiliser.

3. **Exécution** : Chaque action (édition, suppression, création) reste protégée par les mêmes garde-fous que le mode explicite (backup, validation syntaxique, rollback).

4. **Mode dégradé** : si Ollama est injoignable ou timeout, le CLI retombe automatiquement sur le routage local par regex (moins capable, mais fonctionnel hors-ligne).

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
├── main.py        # Point d'entrée CLI + REPL interactif
├── agent.py       # Boucle agentique : tool-calling, planification, détection de blocage
├── tools.py       # Outils exposés au modèle (list_files, grep, read_file, edit_file…)
├── brain.py       # Routage explicite, appels Ollama, édition et suppression protégées
├── scanner.py     # Indexation du projet
├── config.py      # Configuration
├── install.py     # Installeur multi-plateforme (Windows / Linux / macOS)
├── uninstall.py   # Désinstallation (laisse Ollama et les modèles intacts)
├── selfdev.bat    # Lanceur Windows
├── selfdev        # Lanceur Unix (créé par install.py)
└── README.md      # Ce fichier
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