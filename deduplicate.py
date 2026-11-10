import os
import glob
import json
from difflib import SequenceMatcher

OBS_DIR = "lessons/observations"
files = sorted(glob.glob(os.path.join(OBS_DIR, "*.json")))

seen = []
deleted = 0

for filepath in files:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    cause = str(data.get("root_cause", "")).lower()
    details = json.dumps(data.get("details", {}), sort_keys=True).lower()
    
    is_dup = False
    for s_cause, s_details in seen:
        if SequenceMatcher(None, cause, s_cause).ratio() > 0.8 or SequenceMatcher(None, details, s_details).ratio() > 0.8:
            is_dup = True
            break
            
    if is_dup:
        os.remove(filepath)
        deleted += 1
    else:
        seen.append((cause, details))

print(f"Cleaned up {deleted} duplicate observation files.")