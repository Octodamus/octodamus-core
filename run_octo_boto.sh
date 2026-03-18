#!/bin/bash
cd /home/walli/octodamus
export ANTHROPIC_API_KEY=$(python3 -c "import sys; sys.path.insert(0,'.'); from bitwarden import _load_cache; print(_load_cache().get('ANTHROPIC_API_KEY',''))")
export OCTOBOTO_TELEGRAM_TOKEN=$(python3 -c "import sys; sys.path.insert(0,'.'); from bitwarden import _load_cache; print(_load_cache().get('OCTOBOTO_TELEGRAM_TOKEN',''))")
python3 octo_boto.py
