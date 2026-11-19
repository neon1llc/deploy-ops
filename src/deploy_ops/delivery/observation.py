# delivery/observation.py - Observe local deployment drift without granting repair authority.


import hashlib
import json
import os
import stat
import time
from pathlib import Path

from ..config import fingerprint
from ..errors import Refused
from .files import checked_path, read_regular


# Hash only regular files without silently skipping unreadable entries
def tree_digest(root: Path) -> str:
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise Refused('Observed artifact is not a directory')
    entries, count, total = [], 0, 0


    # Count directory entries and bytes before accumulating evidence
    def walk(folder):
        nonlocal count, total
        # scandir propagates permission errors instead of silently skipping an unreadable subtree
        with os.scandir(folder) as scan:
            for item in scan:
                count += 1
                if count > 10000:
                    raise Refused('Observed artifact exceeds the entry limit')
                mode = item.stat(follow_symlinks=False).st_mode
                path = Path(item.path)
                if stat.S_ISDIR(mode):
                    walk(path)
                elif stat.S_ISREG(mode):
                    data = read_regular(path, 100_000_000 - total)
                    total += len(data)
                    entries.append([str(path.relative_to(root)), hashlib.sha256(data).hexdigest()])
                else:
                    raise Refused('Observed artifact contains a symbolic link or unsupported file type')
    walk(root)
    return fingerprint(sorted(entries, key=lambda entry: Path(entry[0]).parts))


# Read the approval-bound baseline through the same safe file checks
def reviewed(engine, release: dict, name: str) -> dict:
    try:
        return engine._reviewed_envelope(release, name, lambda path: read_regular(checked_path(engine.state, path), 2_000_000))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise Refused('Reviewed baseline is unavailable') from error


# Find the last successful activation rather than the newest created release
def activation(engine, pointer: Path):
    # Completion event order handles releases created earlier but applied later, without a listing limit
    with engine.ledger.store.connect() as connection:
        rows = connection.execute("SELECT e.sequence,e.detail,r.data FROM release_events e JOIN releases r "
                                  "ON r.id=e.release_id WHERE e.name IN ('component.succeeded','static.route.replaced','static.route.inspected') ORDER BY e.sequence DESC")
        for row in rows:
            release, name = json.loads(row['data']), json.loads(row['detail'])['component']
            node = release['nodes'].get(name, {})
            if node.get('adapter') == 'static' and node.get('outputs', {}).get('pointer') == str(pointer):
                envelope = reviewed(engine, release, name)
                prepared = envelope['prepared']
                destination = checked_path(engine.deployment_root(release), prepared['destination'])
                if envelope['adapter'] != 'static' or destination / 'current.json' != pointer:
                    raise Refused('Activation evidence differs from its reviewed target')
                return {'release': release['id'], 'path': str(destination / 'releases' / release['id']),
                        'digest': prepared['site_digest']}
    raise Refused('No successful activation evidence exists for this target')


# Keep unknown evidence distinct from observed drift
def local_component(engine, release: dict, name: str) -> dict:
    if release.get("preview_retiring"):
        return {"status": "unsupported", "reason": "Preview retirement started; inspect its cleanup and retained-data record instead"}
    node = release['nodes'][name]
    if node.get('status') != 'succeeded':
        return {'status': 'unknown', 'reason': 'This component has no successful execution to compare'}
    if node['adapter'] not in {'static', 'directory'}:
        return {'status': 'unsupported', 'reason': 'This adapter has no read-only observation contract yet'}
    try:
        envelope = reviewed(engine, release, name)
        if envelope['adapter'] != node['adapter']:
            raise Refused('Reviewed adapter differs from the release')
        prepared = envelope['prepared']
        deployment_root = engine.deployment_root(release)
        if node['adapter'] == 'directory':
            marker = checked_path(deployment_root, f"{release['environment']}/{prepared['name']}/resource.json")
            expected = {'name': prepared['name'], 'environment': release['environment']}
            actual = json.loads(read_regular(marker, 65536))
            return {'status': 'matching' if actual == expected else 'drifted', 'scope': 'resource marker only',
                    'expected_digest': fingerprint(expected), 'observed_digest': fingerprint(actual)}
        destination = checked_path(engine.deployment_root(release), prepared['destination'])
        artifact = checked_path(deployment_root, destination / 'releases' / release['id'])
        pointer = checked_path(deployment_root, destination / 'current.json')
        expected = activation(engine, pointer)
        # Check both the selected artifact and the current activation without changing either
        first_pointer = read_regular(pointer, 65536)
        actual = json.loads(first_pointer)
        observed = tree_digest(artifact)
        if read_regular(pointer, 65536) != first_pointer:
            raise Refused('Active deployment changed during observation')
        artifact_status = 'matching' if observed == prepared['site_digest'] else 'drifted'
        pointer_status = 'matching' if actual == expected else 'drifted'
        status = 'drifted' if 'drifted' in (artifact_status, pointer_status) else 'matching'
        candidate = envelope.get('adapter_contract_version') == 3 and not node['outputs'].get('pointer')
        if status == 'matching' and expected['release'] != release['id'] and not candidate:
            status = 'superseded'
        return {'status': status, 'scope': 'selected artifact and active pointer',
                'phase': 'candidate-staged' if candidate else 'activation-recorded',
                'rollout_status': release.get('rollout', {}).get('status', 'not-prepared') if envelope.get('adapter_contract_version') == 3 else 'not-applicable',
                'artifact': {'status': artifact_status, 'expected_digest': prepared['site_digest'], 'observed_digest': observed},
                'activation': {'status': pointer_status, 'expected_release': expected['release'],
                               'selected_release_active': expected['release'] == release['id'] and pointer_status == 'matching',
                               'expected_digest': fingerprint(expected), 'observed_digest': fingerprint(actual)}}
    except FileNotFoundError:
        return {'status': 'drifted', 'reason': 'A recorded deployment file or artifact is missing'}
    except (OSError, ValueError, TypeError, KeyError, RecursionError, Refused):
        return {'status': 'unknown', 'reason': 'Evidence is unreadable, unsafe or changed. Inspect the target before acting'}


# Detect concurrent execution before recording a stable observation
def observe(engine, release_id: str) -> dict:
    release = engine.ledger.get(release_id)
    with engine.ledger.store.connect() as connection:
        generation = connection.execute('SELECT COALESCE(MAX(sequence),0) FROM audit').fetchone()[0]
        unresolved = connection.execute("SELECT 1 FROM requests WHERE status IN ('running','uncertain') LIMIT 1").fetchone()
    if unresolved:
        components = {name: {'status': 'unknown', 'reason': 'Resolve active or uncertain local executions before observing'}
                      for name in release['nodes']}
    else:
        components = {name: local_component(engine, release, name) for name in release['nodes']}
    # Reject stability if another operation changed execution evidence during inspection
    with engine.ledger.store.transaction() as connection:
        current = connection.execute('SELECT COALESCE(MAX(sequence),0) FROM audit').fetchone()[0]
        if current != generation:
            components = {name: {'status': 'unknown', 'reason': 'Execution evidence changed during observation; retry after it settles'}
                          for name in release['nodes']}
        states = {node['status'] for node in components.values()}
        status = 'drifted' if 'drifted' in states else 'unknown' if states & {'unknown', 'unsupported'} else 'matching'
        result = {'release_id': release_id, 'observed_at': int(time.time()), 'status': status, 'components': components,
                  'scope': 'Local deployment files at observation time; no application health or cloud checks', 'repairs': False}
        engine.ledger._event(connection, release_id, 'release.observed', result)
    return result
