from pathlib import Path
import json,os,re,tempfile
from urllib.parse import urlsplit

def safe_label(url):
    parsed = urlsplit(url)
    return (parsed.hostname or 'Git source') + '/' + parsed.path.strip('/').removesuffix('.git')


def pinned(ref):
    return bool(re.fullmatch(r'[0-9a-fA-F]{7,40}', ref) or re.match(r'^(refs/tags/|v?\d+\.)', ref))
