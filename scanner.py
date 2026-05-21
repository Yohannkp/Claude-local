import json
import os


def scan_directory(root_dir):
    file_index = []
    excluded_dirs = {'.knowledge_base', 'SELF_DEV_AGENT'}

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [directory for directory in dirnames if directory not in excluded_dirs]
        for filename in filenames:
            if filename in excluded_dirs:
                continue
            file_index.append(os.path.relpath(os.path.join(dirpath, filename), root_dir))

    return sorted(file_index)


def save_index(index, out_path):
    with open(out_path, 'w', encoding='utf-8') as file_handle:
        json.dump(index, file_handle, ensure_ascii=False, indent=2)


def main(root_dir=None):
    root = os.path.abspath(root_dir or os.getcwd())
    kb_dir = os.path.join(root, '.knowledge_base')
    os.makedirs(kb_dir, exist_ok=True)
    index = scan_directory(root)
    save_index(index, os.path.join(kb_dir, 'file_index.json'))
    return index

if __name__ == '__main__':
    main()
