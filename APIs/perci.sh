#!/usr/bin/env bash

if [[ -z $VIRTUAL_ENV ]]; then
    echo -e "\033[31mERROR\033[0m: Must be run from a Python Virtual Environment!" >&2;
    exit 1
fi

python ./PerCI/file_validator.py --target-dir $(pwd) --config-file ./PerCI/config.json $@