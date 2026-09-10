#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$PROJECT_DIR"
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

mkdir -p "$HOME/.local/bin"
ln -sfn "$PROJECT_DIR/.venv/bin/dukielist" "$HOME/.local/bin/dukielist"

DESKTOP_DIR="$HOME/Desktop"
if [ -d "$HOME/Área de Trabalho" ]; then
    DESKTOP_DIR="$HOME/Área de Trabalho"
fi
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/DukieList.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DukieList
Comment=Gerenciador de tarefas no terminal
Exec=$PROJECT_DIR/.venv/bin/dukielist
Path=$PROJECT_DIR
Terminal=true
Categories=Utility;ProjectManagement;
StartupNotify=true
EOF
chmod +x "$DESKTOP_DIR/DukieList.desktop"

echo "DukieList instalado em $HOME/.local/bin/dukielist"
echo "Atalho criado em $DESKTOP_DIR/DukieList.desktop"
echo "Se o comando não for encontrado, abra um novo terminal ou adicione ~/.local/bin ao PATH."
