import hashlib
import os
import re
from typing import Dict, Any

def compute_file_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def resolve_work_order(override: str | None, filename: str, folder_path: str) -> str:
    if override:
        return override

    parts = os.path.basename(filename).split('_')
    if len(parts) > 0:
        first_part = parts[0]
        if not (len(first_part) == 8 and first_part.isdigit()):
            return first_part

    return os.path.basename(os.path.normpath(folder_path))

def parse_filename(filename: str) -> Dict[str, Any]:
    base = os.path.basename(filename)
    name, _ = os.path.splitext(base)
    
    parts = name.split('_')
    if len(parts) < 5:
        raise ValueError(f"Unsupported IW log filename: {filename}")

    work_order = None
    if len(parts) >= 1 and len(parts[0]) == 8 and parts[0].isdigit():
        date_idx = 0
    else:
        date_idx = next((i for i, p in enumerate(parts) if len(p) == 8 and p.isdigit()), None)
        if date_idx is None:
            raise ValueError(f"Unsupported IW log filename: {filename}")
        work_order = "_".join(parts[:date_idx]) or None

    if len(parts) <= date_idx + 4:
        raise ValueError(f"Unsupported IW log filename: {filename}")

    if date_idx == 0:
        date_str = parts[0]
        time_str = parts[1]
        mac1 = parts[2]
        mac2 = parts[3]
        result = parts[4]
    else:
        date_str = parts[date_idx]
        time_str = parts[date_idx+1]
        mac1 = parts[date_idx+2]
        mac2 = parts[date_idx+3]
        result = parts[date_idx+4]
        
    if mac1 == 'UNKNOWNMAC1':
        mac1 = None
    if mac2 == 'UNKNOWNMAC2':
        mac2 = None
        
    return {
        'work_order': work_order,
        'date': date_str,
        'time': time_str,
        'mac1': mac1,
        'mac2': mac2,
        'result': result.upper()
    }
