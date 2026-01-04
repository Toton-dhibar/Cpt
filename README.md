# Cpt

Unified Termux-friendly TRX/SOL hunter with a single script (`CheckORG.py`) and minimal dependencies (`requests`, `bip_utils`).

## Quick start
```bash
pip install requests bip_utils
# Auto mode with lightweight CPU usage
python CheckORG.py --mode auto --threads 2 --delay 0.05
# Save wallets with value > $0 to a file
python CheckORG.py --mode auto --hits-file hits.txt
# Manual check for a known phrase
python CheckORG.py --mode manual --phrase "word1 word2 ... word12"
# Generate sample phrases only
python CheckORG.py --mode generate --count 3
# Health/info
python CheckORG.py --mode health
```
