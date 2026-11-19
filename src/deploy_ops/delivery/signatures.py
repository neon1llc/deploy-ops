# delivery/signatures.py - Observe an exact frozen file signature under an explicitly selected public key.


import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from ..client import private_json
from ..config import fingerprint
from ..errors import Refused
from . import artifacts
from .evidence import Digest, Identifier, Name, Public, Text
from .files import checked_path, location, read_regular
from .process import Process

MAX_KEY = 16_384
RAW_FILES = {'public-key.pem', 'bundle.sigstore.json', 'subject.blob'}


# Discovery remains command-free; availability is not signature or policy qualification
def options():
    return {'tool': 'cosign', 'available': shutil.which('cosign') is not None,
            'supported': 'stable v3.1 or later within v3', 'native_qualified_version': 'v3.1.4',
            'scope': 'selected-frozen-file-content-only', 'transparency': 'not_checked',
            'timestamp': 'not_checked', 'revocation': 'not_checked', 'policy': 'not_evaluated',
            'max_subject_bytes': artifacts.MAX_BYTES, 'max_bundle_bytes': artifacts.MAX_REPORT,
            'max_public_key_bytes': MAX_KEY, 'timeout_seconds': 60}


# Search the authenticated frozen inventory without tools, source builds or arbitrary host enumeration
def files(state, release_id, component, query='', offset=0, limit=100):
    if (not isinstance(query, str) or len(query) > 200 or type(offset) is not int or offset < 0
            or type(limit) is not int or not 1 <= limit <= 100):
        raise Refused('Use a file search up to 200 characters and a page of 1 to 100 entries', 422)
    _, prepared, tree, _, _, inventory = artifacts.subject(location(state), release_id, component, include_files=True)
    matches = [{'name': name, **value} for name, value in sorted(inventory.items()) if query.casefold() in name.casefold()]
    return {'release_id': release_id, 'component': component, 'prepared_sha256': prepared,
            'artifact_subject_sha256': tree, 'total': len(matches), 'offset': offset, 'limit': limit,
            'files': matches[offset:offset + limit]}


# Pinning is optional for interactive selection and required by any independently managed trust policy
class Profile(Public):
    expected_key_sha256: Digest | None = None
    timeout_seconds: int = Field(default=60, ge=10, le=300)
    method: Literal['cosign-v3-local-key-bundle'] = 'cosign-v3-local-key-bundle'


class File(Public):
    sha256: Digest
    size: int = Field(ge=0, le=artifacts.MAX_BYTES)


# A selected relative filename cannot become a native URI, directory traversal or a host path
def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 2048 or value in {'.', '..'}
            or Path(value).is_absolute() or '..' in Path(value).parts or '\\' in value or '\x00' in value
            or str(Path(value)) != value):
        raise Refused('Choose one exact relative file inside the frozen static artifact', 422)
    return value


# Do not copy accidental private key material, certificates, KMS references or trailing PEM content
def public_key(path, expected=None):
    try:
        data = read_regular(location(path), MAX_KEY)
    except OSError as error:
        raise Refused('The selected local public-key file is unavailable; no native verification was started', 422) from error
    if not re.fullmatch(rb'-----BEGIN PUBLIC KEY-----\r?\n[A-Za-z0-9+/=\r\n]+-----END PUBLIC KEY-----\r?\n?', data):
        raise Refused('Choose a local PEM PUBLIC KEY file only; private keys, certificates and remote key references are not supported', 422)
    digest = hashlib.sha256(data).hexdigest()
    if expected is not None and digest != expected:
        raise Refused('Public key bytes differ from the expected SHA-256 file pin', 409)
    return data, digest


# A fingerprint pins bytes; it does not decide whether their owner is trusted
def key_info(path):
    data, digest = public_key(path)
    return {'sha256': digest, 'digest_kind': 'sha256-file', 'bytes': len(data), 'trust': 'not-established'}


class Report(Public):
    format: Literal['deploy-ops.file-signature-observation'] = 'deploy-ops.file-signature-observation'
    version: Literal[1] = 1
    id: Identifier
    release_id: Identifier
    component: Name
    prepared_sha256: Digest
    artifact_subject_sha256: Digest
    subject_method: Literal['deploy-ops-canonical-tree-v1'] = 'deploy-ops-canonical-tree-v1'
    selected_file: str
    selected_file_sha256: Digest
    selected_file_bytes: int = Field(ge=0, le=artifacts.MAX_BYTES)
    selected_key_sha256: Digest
    key_digest_kind: Literal['sha256-file'] = 'sha256-file'
    profile: Profile
    captured_at: int
    status: Literal['verified', 'not_verified', 'unavailable']
    tool: Literal['cosign'] = 'cosign'
    tool_version: Text | None = None
    reason: Text
    scope: Literal['selected-frozen-file-content-only'] = 'selected-frozen-file-content-only'
    signer_trust: Literal['operator-selected-key-only'] = 'operator-selected-key-only'
    transparency: Literal['not_checked'] = 'not_checked'
    timestamp: Literal['not_checked'] = 'not_checked'
    revocation: Literal['not_checked'] = 'not_checked'
    policy: Literal['not_evaluated'] = 'not_evaluated'
    authority: Literal['observation-only'] = 'observation-only'
    files: dict[Literal['public-key.pem', 'bundle.sigstore.json', 'subject.blob'], File]
    sha256: Digest

    @field_validator('selected_file')
    @classmethod
    def selected(cls, value):
        return relative(value)


# Read reviewed frozen bytes without current configuration, grants, source builds or deployed pointers
def selection(state, release_id, component, name):
    name = relative(name)
    target, prepared, tree, _, _, expected = artifacts.subject(state, release_id, component, selected_file=name)
    file = checked_path(target, target / name)
    try:
        content = read_regular(file, artifacts.MAX_BYTES)
    except OSError as error:
        raise Refused('The selected frozen file became unavailable; inspect its artifact before verifying', 409) from error
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected['sha256'] or len(content) != expected['size']:
        raise Refused('Selected file changed after the authenticated artifact inventory', 409)
    return file, prepared, tree, digest, len(content), content


# Keep invalid input outside new output and retain a truthful completed report when a tool is unavailable
def verify(state, release_id, component, name, key, bundle, out, profile: Profile, workspace=None):
    state = location(state)
    file, prepared, tree, digest, size, subject_data = selection(state, release_id, component, name)
    key_data, key_digest = public_key(key, profile.expected_key_sha256)
    try:
        bundle_data = read_regular(location(bundle), artifacts.MAX_REPORT)
    except OSError as error:
        raise Refused('The selected local signature bundle is unavailable; no native verification was started', 422) from error
    try:
        parsed = json.loads(bundle_data)
        if (not isinstance(parsed, dict) or parsed.get('mediaType') not in {
                'application/vnd.dev.sigstore.bundle.v0.3+json', 'application/vnd.dev.sigstore.bundle.v0.3.0+json'}):
            raise ValueError('format')
    except (ValueError, TypeError):
        raise Refused('Choose a modern Sigstore bundle file; legacy detached signatures are not supported by this profile', 422) from None
    target = checked_path(state, file)
    out = artifacts.new_output(state, out, target, workspace)
    inputs = (('public-key.pem', key_data), ('bundle.sigstore.json', bundle_data), ('subject.blob', subject_data))
    for filename, content in inputs:
        path = out / filename
        descriptor = path.open('xb')
        with descriptor as stream:
            stream.write(content)
        path.chmod(0o600)
    home = out / 'tool-home'; home.mkdir(mode=0o700)
    events = []
    process = Process(home, lambda name, detail: events.append({'name': name, 'detail': detail}))
    status, version = 'unavailable', None
    reason = 'Cosign v3 verification is unavailable; no signature validity or release policy was established'
    try:
        metadata = json.loads(process.run(['cosign', 'version', '--json'], out, profile.timeout_seconds, capture=True))
        raw_version = metadata.get('gitVersion')
        version = raw_version if isinstance(raw_version, str) and re.fullmatch(r'v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}', raw_version) else None
        if version is None or not re.fullmatch(r'v3\.[1-9][0-9]*\.[0-9]+', version):
            raise Refused('This selected-key bundle profile requires stable Cosign v3.1 or later within v3')
        status = 'not_verified'
        reason = 'Native verification did not establish a signature for this exact file and selected key; this is not a clean scan or a release-policy decision'
        # This explicit local-key/no-timestamp/no-log path avoids TUF, OIDC, KMS and registry discovery.
        # The deprecated --offline flag alone does not promise no network and is deliberately not used.
        process.run(['cosign', '--timeout', str(profile.timeout_seconds) + 's', 'verify-blob',
                     '--bundle', str(out / 'bundle.sigstore.json'), '--key', str(out / 'public-key.pem'),
                     '--insecure-ignore-tlog=true', '--max-workers', '1', str(out / 'subject.blob')], out, profile.timeout_seconds, capture=True)
        status = 'verified'
        reason = 'Cosign verified the selected frozen file content under the explicitly selected public key; other files, transparency, time, revocation and release policy were not verified'
    except (Refused, OSError, ValueError, TypeError, AttributeError):
        pass
    _, now_prepared, now_tree, now_digest, now_size, _ = selection(state, release_id, component, name)
    if (now_prepared, now_tree, now_digest, now_size) != (prepared, tree, digest, size):
        raise Refused('Frozen artifact changed during verification; retain incomplete output and inspect the original artifact', 409)
    files = {filename: File(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
             for filename, data in inputs}
    value = Report(id=uuid.uuid4().hex, release_id=release_id, component=component, prepared_sha256=prepared,
        artifact_subject_sha256=tree, selected_file=name, selected_file_sha256=digest, selected_file_bytes=size,
        selected_key_sha256=key_digest, profile=profile, captured_at=int(time.time()), status=status,
        tool_version=version, reason=reason, files=files, sha256='0' * 64).model_dump(mode='json')
    value['sha256'] = fingerprint({key: item for key, item in value.items() if key != 'sha256'})
    private_json(out / 'tool-events.json', events, durable=True)
    private_json(out / 'report.json', value, durable=True)
    return inspect(out)


# Offline integrity checks reopen only this report's fixed local evidence files, never recorded state paths
def inspect(directory):
    out = location(directory)
    try:
        document = json.loads(read_regular(checked_path(out, out / 'report.json'), artifacts.MAX_REPORT))
        value = Report.model_validate(document)
        if (value.model_dump(mode='json') != document or type(document['version']) is not int
                or value.sha256 != fingerprint({key: item for key, item in document.items() if key != 'sha256'})
                or set(value.files) != RAW_FILES or value.selected_key_sha256 != value.files['public-key.pem'].sha256
                or value.selected_file_sha256 != value.files['subject.blob'].sha256
                or value.selected_file_bytes != value.files['subject.blob'].size
                or (value.profile.expected_key_sha256 is not None and value.profile.expected_key_sha256 != value.selected_key_sha256)):
            raise ValueError('extent')
        for filename, record in value.files.items():
            limit = {'subject.blob': artifacts.MAX_BYTES, 'public-key.pem': MAX_KEY, 'bundle.sigstore.json': artifacts.MAX_REPORT}[filename]
            if record.size > limit:
                raise ValueError('bound size')
            content = read_regular(checked_path(out, out / filename), limit)
            if len(content) != record.size or hashlib.sha256(content).hexdigest() != record.sha256:
                raise ValueError('bound file')
        return {'integrity_verified': True, 'report': document,
                'meaning': 'Report-byte integrity only; an editable/rehashed report is not native verification, signer trust or execution authority'}
    except (OSError, ValidationError, ValueError, TypeError, KeyError):
        raise Refused('Unsupported, incomplete or changed file-signature report', 422) from None
