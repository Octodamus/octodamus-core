import sys
sys.path.insert(0, r'C:\Users\walli\octodamus')
import bitwarden
bitwarden.load_all_secrets()
import os
k = os.getenv('ANTHROPIC_API_KEY', '')
print(k[:20] + '...' + k[-4:])
