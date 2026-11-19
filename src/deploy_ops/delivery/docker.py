# delivery/docker.py - Bind local Compose tools and a Unix daemon outside contributor settings.


import hashlib
import json
import os
import re
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..errors import Refused
from .access import private_file
from .files import native_tool, read_regular
from .spec import load


# Select a closed local connection, never personal contexts, helpers or registry credentials
class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: int = Field(ge=1, le=1)
    workspace_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    docker_executable: str
    compose_executable: str
    endpoint: str
    endpoint_owner_uid: int = Field(ge=0)
    daemon_id: str = Field(pattern=r'^[A-Za-z0-9:_-]{1,256}$')
    projects: dict[str, str] = Field(min_length=1)


    # Keep names literal and local; the selected Unix socket is rechecked separately
    @model_validator(mode='after')
    def names(self):
        if self.endpoint_owner_uid not in {0, os.getuid()} or not self.endpoint.startswith('unix:///'):
            raise ValueError('Choose an owned local Unix endpoint')
        for value in (self.docker_executable, self.compose_executable, self.endpoint[7:]):
            if not Path(value).is_absolute() or any(character in value for character in ('\x00', '\n', '\r')):
                raise ValueError('Choose absolute native paths')
        if any(not re.fullmatch(r'[a-z][a-z0-9-]{0,47}/[a-z][a-z0-9-]{0,47}', key)
               or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', value) for key, value in self.projects.items()):
            raise ValueError('Choose exact component namespaces')
        if len(set(self.projects.values())) != len(self.projects):
            raise ValueError('Choose a distinct Compose project for each selected target')
        return self


# Contributor source cannot supply the operator profile, tools or daemon endpoint
def outside(path: Path, project: Path, spec):
    roots = [project.resolve().parent, *((project.parent / item.path).resolve() for item in spec.repositories.values())]
    if any(candidate.is_relative_to(root) for candidate in (Path(os.path.abspath(path)), path.resolve()) for root in roots):
        raise Refused('Keep the Docker profile, tools and endpoint outside workspace and source repositories', 422)


# Read the explicitly selected profile without changing any host or Docker configuration
def selection(project: Path, spec) -> tuple[Profile, dict]:
    reference = os.environ.get('DEPLOY_OPS_DOCKER_PROFILE_FILE')
    if not reference:
        raise Refused('Select an external DEPLOY_OPS_DOCKER_PROFILE_FILE before preparing Compose', 422)
    try:
        original = Path(reference).expanduser()
        if not original.is_absolute():
            raise Refused('Select the Docker profile by its absolute external path', 422)
        outside(original, project, spec)
        path = private_file(reference)
        outside(path, project, spec)
        content = read_regular(path, 64 * 1024)
        profile = Profile.model_validate_json(content)
        if profile.workspace_digest != spec.digest:
            raise Refused('Docker profile does not match this workspace; review a new profile', 409)
        for key in profile.projects:
            environment, name = key.split('/')
            if environment not in spec.environments or name not in spec.components or spec.component(name, environment).adapter != 'compose':
                raise Refused('Docker profile names an unknown Compose target', 422)
        tools = {}
        for key in ('docker_executable', 'compose_executable'):
            outside(Path(getattr(profile, key)), project, spec)
            tool = Path(native_tool(getattr(profile, key)))
            outside(tool, project, spec)
            data = read_regular(tool, 128 * 1024 * 1024)
            info = tool.stat()
            tools[key] = {'path': str(tool), 'sha256': hashlib.sha256(data).hexdigest(),
                          'device': info.st_dev, 'inode': info.st_ino, 'owner_uid': info.st_uid,
                          'owner_gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
        endpoint = Path(profile.endpoint[7:])
        outside(endpoint, project, spec)
        if str(endpoint.resolve()) != str(endpoint):
            raise Refused('Docker endpoint must use its canonical path without linked aliases', 422)
        return profile, {'profile_sha256': hashlib.sha256(content).hexdigest(), 'tools': tools,
                         'endpoint': profile.endpoint, 'endpoint_owner_uid': profile.endpoint_owner_uid,
                         'daemon_id': profile.daemon_id}
    except (OSError, ValueError, ValidationError):
        raise Refused('Docker profile or native tool is invalid or unavailable; inspect it privately', 422) from None


# Bind namespace and actual socket identity without making a daemon call
def binding(project: Path, spec, environment: str, name: str, values: dict | None = None) -> dict:
    profile, result = selection(project, spec)
    component = spec.component(name, environment)
    namespace = (values if values is not None else component.options).get('project', f'{environment}-{name}')
    if isinstance(namespace, str) and re.fullmatch(r'\$\{env\.[^{}]+\}', namespace):
        namespace = spec.environments[environment].variables[namespace[6:-1]]
    if spec.environments[environment].provider != 'local' or profile.projects.get(environment + '/' + name) != namespace:
        raise Refused('Docker profile does not select this exact local Compose project', 403)
    try:
        info = Path(profile.endpoint[7:]).lstat()
    except OSError:
        raise Refused('Selected Docker Unix socket is unavailable; no daemon was started', 422) from None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != profile.endpoint_owner_uid:
        raise Refused('Selected Docker endpoint is not the expected owned Unix socket', 403)
    return {**result, 'project': namespace, 'socket': {'device': info.st_dev, 'inode': info.st_ino,
            'owner_uid': info.st_uid, 'owner_gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}}


# Refuse changed selection before consuming an approval or using native commands
def revalidate(project: Path, spec, environment: str, name: str, values: dict, frozen: dict) -> dict:
    current = binding(project, spec, environment, name, values)
    if current != frozen:
        raise Refused('Reviewed Docker tool, profile or endpoint changed; prepare fresh evidence', 409)
    return current


# Keep all native Docker configuration inside the existing private runtime home
def configure(context, selected: dict):
    context.docker_binding = selected
    context.process.env['DOCKER_HOST'] = selected['endpoint']
    context.process.env['DOCKER_CONFIG'] = context.process.env['HOME']


# A socket's metadata alone never proves a daemon or its identity
def verify_daemon(context):
    selected = context.docker_binding
    if not selected:
        raise Refused('Compose requires a freshly reviewed Docker binding', 422)
    value = context.process.run([selected['tools']['docker_executable']['path'], 'info', '--format', '{{json .ID}}'],
        context.source, min(context.component.timeout_seconds, 10), capture=True, input_text=None)
    try:
        actual = json.loads(value)
    except ValueError:
        raise Refused('Selected Docker daemon identity is unavailable', 422) from None
    if actual != selected['daemon_id']:
        raise Refused('Selected Docker daemon identity changed; prepare fresh evidence', 409)


# CLI and browser share metadata-only readiness and never expose profile paths or credentials
def readiness(project: Path) -> dict:
    try:
        spec = load(project)
        targets = [(environment, name) for environment in spec.environments for name in spec.components
                   if spec.component(name, environment).adapter == 'compose']
        if not targets:
            return {'state': 'not-needed', 'reason': 'No Compose components in this workspace', 'daemon_verified': False}
        rows = []
        for environment, name in targets:
            try:
                binding(project, spec, environment, name)
                state, reason = 'metadata-ready', 'Tool and socket metadata match; daemon identity and health are unverified'
            except Refused as error:
                state, reason = 'unavailable', str(error)
            rows.append({'environment': environment, 'component': name, 'state': state, 'reason': reason})
        return {'state': 'metadata-ready' if all(row['state'] == 'metadata-ready' for row in rows) else 'unavailable',
                'reason': 'Metadata inspection only; no daemon query or container action', 'daemon_verified': False, 'targets': rows}
    except (Refused, OSError):
        return {'state': 'unavailable', 'reason': 'Choose a valid saved workspace for Docker readiness', 'daemon_verified': False}
