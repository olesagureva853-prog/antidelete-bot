#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi

source venv/bin/activate
python3 main.py
