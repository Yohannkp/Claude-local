"""Desinstalleur pour SELF_DEV_AGENT.

Usage:
    python uninstall.py
    python uninstall.py --yes
"""

import subprocess
import sys
import os


def main():
    installer = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'install.py')
    args = ['--uninstall'] + [a for a in sys.argv[1:] if a in ('--yes',)]
    subprocess.run([sys.executable, installer] + args, check=False)


if __name__ == '__main__':
    main()
