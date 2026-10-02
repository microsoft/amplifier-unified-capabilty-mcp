"""Inert observation presentation contracts; no client state or authority. MIT.

Extracted from Unified 0c878427 observation_presentation.py.
"""
import copy
from urllib.parse import urlsplit
from .observation_contract import text

def parsed_url(value):
    text(value, 'presentation URL', 4000)
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or '\\' in value or any(ord(c) < 33 for c in value)):
        raise ValueError('Presentation needs an http(s) URL without credentials')
    parsed.port  # Reject malformed/out-of-range ports, including in an origin.
    return parsed

def request(value):
    if value is None: return None
    if not isinstance(value, dict) or value.get('kind') != 'browser':
        raise ValueError('Only an explicitly requested browser presentation is supported')
    if set(value) == {'kind', 'urlPolicy'} and value['urlPolicy'] == 'loopback-with-explicit-port':
        return copy.deepcopy(value)
    if set(value) != {'kind', 'urlOrigins'} or not isinstance(value['urlOrigins'], list) or not 1 <= len(value['urlOrigins']) <= 8:
        raise ValueError('Review exact URL origins or the explicit-port loopback policy')
    for origin in value['urlOrigins']:
        parsed = parsed_url(origin)
        if parsed.path or parsed.query or parsed.fragment or '*' in origin:
            raise ValueError('Presentation origins cannot contain paths, queries, fragments or wildcards')
        if origin != parsed.scheme + '://' + parsed.netloc:
            raise ValueError('Use the exact normalized presentation origin')
    return copy.deepcopy(value)

def candidate(value, outcome):
    if value is None: return None
    if (outcome['status'] != 'actionable' or not isinstance(value, dict)
            or set(value) != {'kind', 'url', 'title', 'target', 'evidence'} or value['kind'] != 'browser'
            or value['target'] != outcome['target'] or value['evidence'] not in outcome['evidence']):
        raise ValueError('Presentation must identify the exact accepted target and evidence')
    parsed_url(value['url']); text(value['title'], 'presentation title', 200)
    return copy.deepcopy(value)

def permitted(value, policy):
    parsed = parsed_url(value['url'])
    if policy.get('urlPolicy') == 'loopback-with-explicit-port':
        return parsed.hostname in {'localhost', '127.0.0.1', '::1'} and parsed.port is not None
    return parsed.scheme + '://' + parsed.netloc in policy['urlOrigins']
