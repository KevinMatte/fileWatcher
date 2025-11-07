#!/bin/bash

if [ \! -e venv ]; then
    echo "Building venv"
    python3 -m venv venv
    . venv/bin/activate
    pip install -r requirements.txt
fi

. venv/bin/activate
python ./fileWatcher.py -r "$@"

