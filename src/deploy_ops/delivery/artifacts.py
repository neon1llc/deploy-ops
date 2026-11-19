# delivery/artifacts.py - Collect explicit native observations of exact retained artifact bytes without changing release policy.


import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from ..client import private_json
from ..config import fingerprint
from ..errors import Refused
from ..store import require_writable
from .evidence import Digest, Identifier, Name, Public, Text
from .files import checked_path, inventory_tree, location, read_regular, tree_fingerprint
from .process import Process
from .retained import Retained

MAX_BYTES = 100_000_000
MAX_ENTRIES = 10000
MAX_REPORT = 10_000_000
RAW_FILES = {'inventory.cdx.json', 'vulnerabilities.json'}


# Keep optional native collection distinct from enforcement, signatures and current deployment health
class Profile(Public):
    inventory: bool = True
    vulnerabilities: bool = False
    allow_network: bool = False
    timeout_seconds: int = Field(default=60, ge=10, le=300)


# Record bounded facts and safe diagnostics, never arbitrary native payloads or source names
class Check(Public):
    status: Literal['not_requested', 'collected', 'no_data', 'partial', 'unavailable']
    tool: Literal['syft', 'trivy']
    version: Text | None = None
    package_count: int | None = Field(default=None, ge=0)
    file_count: int | None = Field(default=None, ge=0)
    severity_counts: dict[Literal['UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'], int] = Field(default_factory=dict)
    scanned_targets: int | None = Field(default=None, ge=0)
    database_updated_at: int | None = None
    database_next_update: int | None = None
    database_freshness: Literal['not_applicable', 'unknown', 'current', 'stale'] = 'not_applicable'
    started_at: int | None = None
    finished_at: int | None = None
    reason: Text


# Bind native report bytes separately from their allowlisted human-facing projection
class File(Public):
    sha256: Digest
    size: int = Field(ge=0, le=MAX_REPORT)


# A completed observation report grants no artifact, signature or deployment authority
class Report(Public):
    format: Literal['deploy-ops.artifact-observation'] = 'deploy-ops.artifact-observation'
    version: Literal[1] = 1
    id: Identifier
    release_id: Identifier
    component: Name
    prepared_sha256: Digest
    subject_kind: Literal['retained-static-tree'] = 'retained-static-tree'
    subject_sha256: Digest
    subject_file_count: int = Field(ge=0, le=MAX_ENTRIES)
    subject_bytes: int = Field(ge=0, le=MAX_BYTES)
    subject_digest_method: Literal['deploy-ops-canonical-tree-v1'] = 'deploy-ops-canonical-tree-v1'
    profile: Profile
    captured_at: int
    inventory: Check
    vulnerabilities: Check
    files: dict[Literal['inventory.cdx.json', 'vulnerabilities.json'], File]
    authenticity: Literal['not_checked'] = 'not_checked'
    trusted_signer: Literal['not_checked'] = 'not_checked'
    policy: Literal['not_evaluated'] = 'not_evaluated'
    authority: Literal['observation-only; no execution admission'] = 'observation-only; no execution admission'
    sha256: Digest


# Suggest a new private directory without creating it or starting a native tool
def options():
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    return {'output_directory': str(Path.home() / '.local/share/deploy-ops/artifact-evidence' / stamp),
            'profile': Profile().model_dump(), 'supported_subjects': ['retained-static-tree'],
            'max_subject_bytes': MAX_BYTES, 'max_entries': MAX_ENTRIES, 'max_report_bytes': MAX_REPORT,
            'policy': 'not_evaluated', 'authenticity': 'not_checked'}


# Authenticate the frozen artifact independently of current configuration, source or deployed pointer
def subject(state, release_id, component, *, selected_file=None, include_files=False):
    try:
        if not re.fullmatch(r'[0-9a-f]{32}', release_id) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', component):
            raise Refused('Choose an exact release and registered component name', 422)
        retained = Retained(state)
        release = retained.ledger.get(release_id)
        node = release['nodes'].get(component, {})
        if node.get('adapter') != 'static' or not node.get('artifact_digest'):
            raise Refused('Artifact collection currently supports a reviewed retained static artifact', 422)
        folder = checked_path(state, state / 'releases' / release_id / component)
        envelope = retained._reviewed_envelope(release, component, lambda path: read_regular(checked_path(state, path), MAX_REPORT))
        artifacts = checked_path(state, folder / 'artifacts')
        extent = inventory_tree(artifacts, MAX_BYTES, MAX_ENTRIES)
        if tree_fingerprint([[name, value['sha256']] for name, value in extent['files'].items()]) != envelope['artifact_tree']:
            raise Refused('Retained artifact extent changed after review; no tool was started', 409)
        site = checked_path(state, artifacts / 'site')
        if not site.is_dir():
            raise Refused('The reviewed static subject is unavailable; no source fallback was used', 422)
        selected = {name[5:]: value for name, value in extent['files'].items() if name.startswith('site/')}
        digest = tree_fingerprint([[name, value['sha256']] for name, value in selected.items()])
        if digest != envelope['prepared'].get('site_digest'):
            raise Refused('Retained static subject differs from its reviewed digest', 409)
        result = site, node['artifact_digest'], digest, len(selected), sum(value['size'] for value in selected.values())
        if selected_file is not None:
            if selected_file not in selected:
                raise Refused('Selected file is absent from the authenticated frozen inventory', 422)
            return (*result, selected[selected_file])
        if include_files:
            return (*result, selected)
        return result

    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise Refused('Reviewed artifact evidence is unavailable or unsupported; no source fallback or tool was started', 422) from None


# Parse native timestamps without treating missing or future database metadata as fresh
def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return int(parsed.timestamp()) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


# Count inventory types rather than mislabeling CycloneDX file entries as software packages
def inventory_summary(data):
    if (data.get('bomFormat') != 'CycloneDX' or data.get('specVersion') not in {'1.4', '1.5', '1.6', '1.7'}
            or not isinstance(data.get('components', []), list)):
        raise ValueError('unsupported inventory')
    components = data.get('components', [])
    files = sum(item.get('type') == 'file' for item in components)
    packages = sum(item.get('type') in {'application', 'framework', 'library', 'container', 'operating-system',
                                       'device', 'firmware'} for item in components)
    unknown = len(components) - files - packages
    return {'status': 'partial' if unknown else 'collected' if packages else 'no_data',
            'package_count': packages, 'file_count': files,
            'reason': 'Native inventory of these frozen bytes only; package discovery is not completeness, vulnerability or signature verification.'}


# Retain vulnerability counts and coverage facts without exporting native package paths or messages
def vulnerability_summary(data):
    results = data.get('Results', [])
    if data.get('SchemaVersion') != 2 or not isinstance(results, list):
        raise ValueError('unsupported vulnerability report')
    packages, severities = 0, {name: 0 for name in ('UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')}
    for result in results:
        packages += len(result.get('Packages', []))
        for item in result.get('Vulnerabilities', []):
            level = item.get('Severity')
            severities[level if level in severities else 'UNKNOWN'] += 1
    return {'status': 'collected' if packages else 'partial' if any(severities.values()) else 'no_data',
            'package_count': packages, 'severity_counts': severities, 'scanned_targets': len(results),
            'database_freshness': 'unknown',
            'reason': 'Native detection covers supported analyzers only. No findings or no packages is not proof of a safe artifact.'}


# Run fixed native commands with private configuration and no source, operator, cloud or registry credentials
def measure(process, target, out, profile, kind):
    tool = 'syft' if kind == 'inventory' else 'trivy'
    wanted = profile.inventory if kind == 'inventory' else profile.vulnerabilities
    if not wanted:
        return Check(status='not_requested', tool=tool, reason='Not selected; no conclusion recorded.')
    check = Check(status='unavailable', tool=tool, started_at=int(time.time()),
                  database_freshness='not_applicable' if tool == 'syft' else 'unknown',
                  reason='Native tool or advisory data was unavailable. No conclusion recorded; inspect local tooling before another collection.')
    try:
        if tool == 'syft':
            process.env['SYFT_CHECK_FOR_APP_UPDATE'] = 'false'
            config = out / 'syft-config.yaml'
            config.write_text('check-for-app-update: false\nenrich: []\n'); config.chmod(0o600)
            version = process.run(['syft', 'version'], out, profile.timeout_seconds, True)
            match = re.search(r'^Version:\s+([0-9A-Za-z.+-]+)$', version, re.M)
            if match:check.version = match[1]
            process.run(['syft', 'scan', '--config', str(config), 'dir:' + str(target), '-o', 'cyclonedx-json=' + str(out / 'inventory.cdx.json')],
                        out, profile.timeout_seconds, True)
            facts = inventory_summary(json.loads(read_regular(out / 'inventory.cdx.json', MAX_REPORT)))
        else:
            config = out / 'scanner-config.yaml'; config.write_text('{}\n'); config.chmod(0o600)
            cache = out / 'scanner-cache'
            cache.mkdir(mode=0o700)
            ignore = out / 'scanner-ignore.txt'; ignore.write_text(''); ignore.chmod(0o600)
            version = json.loads(process.run(['trivy', 'version', '--format', 'json', '--cache-dir', str(cache), '--config', str(config)],
                                            out, profile.timeout_seconds, True))
            if isinstance(version.get('Version'), str):check.version = version['Version'][:128]
            command = ['trivy', 'fs', '--scanners', 'vuln', '--format', 'json', '--output', str(out / 'vulnerabilities.json'),
                '--cache-dir', str(cache), '--config', str(config), '--ignorefile', str(ignore), '--disable-telemetry', '--skip-version-check',
                '--offline-scan', '--skip-java-db-update', '--skip-check-update', '--timeout', str(profile.timeout_seconds) + 's']
            if not profile.allow_network:command.append('--skip-db-update')
            process.run([*command, str(target)], out, profile.timeout_seconds, True)
            facts = vulnerability_summary(json.loads(read_regular(out / 'vulnerabilities.json', MAX_REPORT)))
            metadata = json.loads(process.run(['trivy', 'version', '--format', 'json', '--cache-dir', str(cache), '--config', str(config)],
                                             out, profile.timeout_seconds, True)).get('VulnerabilityDB', {})
            updated, next_update = timestamp(metadata.get('UpdatedAt')), timestamp(metadata.get('NextUpdate'))
            facts.update(database_updated_at=updated, database_next_update=next_update)
            now = int(time.time())
            if updated is not None and next_update is not None and updated <= now:
                facts['database_freshness'] = 'current' if next_update > now else 'stale'
        check = Check(**{**check.model_dump(), **facts})
    except (Refused, OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    check.finished_at = int(time.time())
    return check


# Share new private evidence-directory admission without overwriting current work or imported state
def new_output(state, out, target, workspace=None):
    state, out = location(state), location(out)
    if (out.exists() or out.is_relative_to(state) or target.is_relative_to(out)
            or (workspace is not None and out.is_relative_to(location(workspace)))):
        raise Refused('Choose a new private evidence directory outside workspace and state; existing work is preserved', 422)
    require_writable(out / 'report.json')
    out.mkdir(parents=True, mode=0o700)
    return out


# Collect a truthful bounded observation without modifying immutable evidence or release/authority state
def collect(state: Path, release_id: str, component: str, out: Path, profile: Profile, workspace: Path | None = None):
    if not profile.inventory and not profile.vulnerabilities:
        raise Refused('Select package inventory or vulnerability observation', 422)
    if profile.allow_network and not profile.vulnerabilities:
        raise Refused('Advisory network access applies only to the selected vulnerability observation', 422)
    state = location(state)
    target, prepared, digest, file_count, byte_count = subject(state, release_id, component)
    out = new_output(state, out, target, workspace)
    home = out / 'tool-home'; home.mkdir(mode=0o700)
    events = []
    process = Process(home, lambda name, detail: events.append({'name': name, 'detail': detail}))
    inventory = measure(process, target, out, profile, 'inventory')
    vulnerabilities = measure(process, target, out, profile, 'vulnerabilities')
    # Tools measure the same exact immutable subject; changed bytes never produce a completed report
    _, current_prepared, current_digest, current_files, current_bytes = subject(state, release_id, component)
    if (current_prepared, current_digest, current_files, current_bytes) != (prepared, digest, file_count, byte_count):
        raise Refused('The reviewed subject changed during collection; retain incomplete output and inspect the original artifact', 409)
    files = {}
    for name in sorted(RAW_FILES):
        path = out / name
        if path.exists() or path.is_symlink():
            content = read_regular(path, MAX_REPORT)
            path.chmod(0o600)
            files[name] = File(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
    value = Report(id=uuid.uuid4().hex, release_id=release_id, component=component, prepared_sha256=prepared,
        subject_sha256=digest, subject_file_count=file_count, subject_bytes=byte_count, profile=profile, captured_at=int(time.time()), inventory=inventory,
        vulnerabilities=vulnerabilities, files=files, sha256='0'*64)
    document = value.model_dump(mode='json')
    document['sha256'] = fingerprint({key: item for key, item in document.items() if key != 'sha256'})
    private_json(out / 'tool-events.json', events, durable=True)
    private_json(out / 'report.json', document, durable=True)
    return inspect(out)


# Validate the completed report and bound raw bytes without tools, paths from the report or authority bootstrap
def inspect(directory: Path):
    directory = location(directory)
    try:
        raw = json.loads(read_regular(directory / 'report.json', MAX_REPORT))
        value = Report.model_validate(raw)
        if (type(raw['version']) is not int or value.model_dump(mode='json') != raw
                or value.sha256 != fingerprint({key:item for key,item in raw.items() if key != 'sha256'})):
            raise ValueError('extent')
        for name, binding in value.files.items():
            content = read_regular(directory / name, MAX_REPORT)
            if len(content) != binding.size or hashlib.sha256(content).hexdigest() != binding.sha256:
                raise ValueError('raw binding')
        for key, filename in (('inventory','inventory.cdx.json'),('vulnerabilities','vulnerabilities.json')):
            check = getattr(value,key)
            selected = getattr(value.profile,key)
            if (check.tool != ('syft' if key == 'inventory' else 'trivy') or any(value < 0 for value in check.severity_counts.values())):
                raise ValueError('check semantics')
            if ((check.status == 'not_requested') != (not selected)
                    or check.status in {'collected','no_data','partial'} and filename not in value.files):
                raise ValueError('check extent')
        return {'verified': True, 'directory': str(directory), 'report': raw,
                'meaning': 'Observation retained. Authenticity, trusted signer and release policy were not evaluated.'}
    except (OSError, ValueError, TypeError, KeyError, ValidationError):
        raise Refused('Artifact observation is incomplete, changed or unsupported; no tool or repair was attempted', 422) from None
