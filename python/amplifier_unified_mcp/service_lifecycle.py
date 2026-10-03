"""Service evidence binding only; authentication remains the host authority."""
_SERVICE_KEYS = ('installationId', 'dataScope', 'ownerId', 'instanceId', 'releaseDigest')

def service_identity(value):
    if not isinstance(value, dict) or set(value) != set(_SERVICE_KEYS):
        raise ValueError('Exact service identity required')
    if any(not isinstance(value[key], str) or not 1 <= len(value[key]) <= 200
           or any(ord(c) < 32 for c in value[key]) for key in _SERVICE_KEYS):
        raise ValueError('Bounded service identity required')
    return {key: value[key] for key in _SERVICE_KEYS}

def service_proof(proof, context, outcome):
    allowed = {'verified', 'fenceId', 'commandId', 'outcome', 'instanceId',
               'dataScope', 'receiptId', 'kind', 'serviceOutcome', 'expected',
               'observed', 'resumeCommandId', 'exitReceiptId', 'readyReceiptId',
               'refusalReceiptId'}
    if (not isinstance(proof, dict) or set(proof) - allowed
            or proof.get('verified') is not True
            or proof.get('kind') != 'service-lifecycle'):
        raise ValueError('Service-specific authenticated release proof required')
    expected = service_identity(proof.get('expected'))
    observed = service_identity(proof.get('observed'))
    if expected != context['serviceIdentity'] or any(
        observed[key] != expected[key] for key in _SERVICE_KEYS if key != 'instanceId'
    ):
        raise ValueError('Service release must retain installation, owner, scope and release')
    if proof.get('instanceId') != observed['instanceId']:
        raise ValueError('Service release running instance differs from observed identity')
    if outcome == 'ready':
        required = ('resumeCommandId', 'exitReceiptId', 'readyReceiptId')
        if (proof.get('serviceOutcome') != 'resumed'
                or observed['instanceId'] == expected['instanceId']
                or 'refusalReceiptId' in proof):
            raise ValueError('Service resume requires a distinct confirmed replacement')
    elif outcome == 'unchanged':
        required = ('refusalReceiptId',)
        if (proof.get('serviceOutcome') != 'stop-refused' or observed != expected
                or any(key in proof for key in ('resumeCommandId', 'exitReceiptId', 'readyReceiptId'))):
            raise ValueError('Refused service stop must retain the original instance')
    else:
        raise ValueError('Invalid service release outcome')
    for key in (*required, 'receiptId', 'instanceId'):
        if (not isinstance(proof.get(key), str) or not 1 <= len(proof[key]) <= 200
                or any(ord(c) < 32 for c in proof[key])):
            raise ValueError('Bounded service receipt identity required')
    # Detach nested values from the trusted caller. The exact complete proof is
    # durable, so a lost acknowledgement cannot later accept a changed receipt.
    return {**proof, 'expected': expected, 'observed': observed}

