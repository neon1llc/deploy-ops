# delivery/serving.py - Serve exact retained static revisions through bounded process-owned sessions.


import hashlib
import json
import mimetypes
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..config import fingerprint
from ..errors import Refused
from .observation import activation, checked_path, read_regular, reviewed
from .previews import _scan, active, namespace


# Own listeners only in this process and never adopt another console or CLI's sessions
class Sessions:
    # Fix the operator origin so application content cannot share its authority boundary
    def __init__(self, console_port=None):
        self.console_port = console_port
        self.handles = {}
        self.closed = False
        self.lock = threading.RLock()

    # Revalidate the approval-bound revision without consulting its moving current pointer
    @staticmethod
    def selection(engine, release_id, component):
        try:
            release = engine.ledger.get(release_id)
            node = release['nodes'].get(component, {})
            if node.get('status') != 'succeeded' or node.get('adapter') != 'static':
                raise Refused('Choose a successful local static component', 422)
            if release.get('preview'):
                active(engine, release)
            envelope = reviewed(engine, release, component)
            if envelope['adapter'] != 'static':
                raise Refused('Reviewed adapter differs from selected component')
            prepared = envelope['prepared']
            root = engine.deployment_root(release)
            site = checked_path(root, checked_path(root, prepared['destination']) / 'releases' / release_id)
            if node['outputs'].get('path') != str(site) or node['outputs'].get('digest') != prepared['site_digest']:
                raise Refused('Deployed revision differs from reviewed evidence')
            return release, site, prepared['site_digest']
        except (OSError, KeyError, TypeError, AttributeError, ValueError) as error:
            raise Refused('Selected revision evidence is unavailable') from error


    # Follow only this logical owner's reviewed physical target and successful activation
    @classmethod
    def target(cls, engine, release_id, component):
        release, site, digest = cls.selection(engine, release_id, component)
        envelope = reviewed(engine, release, component)
        pointer = site.parent.parent / 'current.json'
        binding = {'path': str(pointer), 'owner': [release['project'], release['environment'], component]}
        if (release.get('preview') or envelope.get('adapter_contract_version', 1) < 2
                or envelope.get('physical_target') != binding
                or release['nodes'][component].get('physical_target') != binding
                or release['nodes'][component]['outputs'].get('pointer') != str(pointer)):
            raise Refused('Active-target serving requires a reviewed regular local target; use a fixed revision for previews or legacy evidence', 422)
        if (envelope.get('adapter_contract_version') == 3
                and release.get('rollout', {}).get('status') not in {'post-check', 'succeeded', 'switched-inspected'}):
            raise Refused('The separately staged candidate has no confirmed or inspected route; inspect its rollout')
        return binding, site, digest


    # Pointer bytes alone cannot admit a failed, foreign or externally reverted revision
    @classmethod
    def routed(cls, engine, anchor, component):
        try:
            binding, _, _ = cls.target(engine, anchor, component)
            pointer = checked_path(engine.state, binding['path'])
            current = json.loads(read_regular(pointer, 65536))
            if (not isinstance(current, dict) or set(current) != {'release', 'path', 'digest'}
                    or not isinstance(current['release'], str) or not re.fullmatch(r'[0-9a-f]{32}', current['release'])):
                raise Refused('Active target has no exact revision pointer')
            selected, site, digest = cls.target(engine, current['release'], component)
            if (selected != binding or current != {'release': current['release'], 'path': str(site), 'digest': digest}
                    or activation(engine, pointer) != current):
                raise Refused('Active target differs from its successful owned activation')
            return current['release'], site, digest
        except (OSError, KeyError, TypeError, AttributeError, ValueError) as error:
            raise Refused('Active target evidence is unavailable') from error


    # Read current lifetime separately from whether the listener still exists
    @staticmethod
    def status(handle):
        state = 'serving'
        observed = {}
        try:
            if handle['mode'] == 'target':
                current, _, digest = Sessions.routed(handle['engine'], handle['release'], handle['component'])
                observed = {'active_release': current, 'active_digest': digest}
            release = handle['engine'].ledger.get(handle['release'])
            if release.get('preview'):
                namespace(handle['engine'], release)
                if release.get('preview_retiring'):
                    state = 'retired'
                elif int(time.time()) >= release['preview']['expires_at']:
                    state = 'expired'
        except (Refused, OSError, KeyError, TypeError, AttributeError, ValueError):
            state = 'evidence-unavailable'
        return {key: handle[key] for key in ('id', 'release', 'component', 'url', 'digest', 'mode')} | {'status': state} | observed

    # Repeated starts read back the same listener; failures leave no phantom handle
    def start(self, engine, release, component, port=0, mode='revision'):
        if mode not in ('revision', 'target'):
            raise Refused('Choose revision or target serving mode', 422)
        if (not isinstance(release, str) or not re.fullmatch(r'[0-9a-f]{32}', release)
                or not isinstance(component, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', component)):
            raise Refused('Choose a release ID and registered component name', 422)
        if type(port) is not int or (port != 0 and not 1024 <= port <= 65535) or port == self.console_port:
            raise Refused('Choose an unprivileged content port different from the console, or zero for automatic', 422)
        with self.lock:
            if self.closed:
                raise Refused('This serving owner has shut down', 409)
            binding = self.target(engine, release, component)[0] if mode == 'target' else None
            for handle in self.handles.values():
                if (handle['mode'] == mode and ((mode == 'target' and handle['binding'] == binding)
                        or (mode == 'revision' and (handle['release'], handle['component']) == (release, component)))):
                    return self.status(handle)
            if len(self.handles) >= 8:
                raise Refused('Stop an owned serving session before starting another', 409)
            try:
                _, site, digest = (self.routed(engine, release, component) if mode == 'target'
                                   else self.selection(engine, release, component))
                inventory = _scan(site)['files']
                actual = fingerprint(sorted([[name, value] for name, value in inventory.items()], key=lambda entry: Path(entry[0]).parts))
                if actual != digest:
                    raise Refused('Deployed artifact changed after review')
                handler = self.handler(engine, release, component, site, inventory, mode)
                server = ThreadingHTTPServer(('127.0.0.1', port), handler)
                if server.server_port == self.console_port:
                    server.server_close()
                    raise Refused('Content must use a different port from the console', 422)
            except OSError as error:
                raise Refused('Cannot read the selected revision or bind the requested local port', 409) from error
            server.daemon_threads = True
            server.block_on_close = False
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
            handle = {'id': uuid.uuid4().hex, 'release': release, 'component': component,
                      'url': f'http://127.0.0.1:{server.server_port}', 'digest': digest,
                      'mode': mode, 'binding': binding,
                      'engine': engine, 'server': server, 'thread': thread}
            thread.start()
            self.handles[handle['id']] = handle
            return self.status(handle)

    # Limit content to the reviewed file inventory with fresh ownership and safe reads
    @classmethod
    def handler(cls, engine, release, component, site, inventory, mode='revision'):
        inventories = {str(site): inventory}
        inventory_lock = threading.Lock()

        # Keep at most two digest-only inventories; every requested file is still read and hashed
        def selection():
            if mode == 'revision':
                cls.selection(engine, release, component)
                return site, inventory
            _, selected, digest = cls.routed(engine, release, component)
            with inventory_lock:
                key = str(selected)
                if key not in inventories:
                    files = _scan(selected)['files']
                    actual = fingerprint(sorted([[name, value] for name, value in files.items()], key=lambda entry: Path(entry[0]).parts))
                    if actual != digest:
                        raise Refused('Active artifact changed after review')
                    if len(inventories) >= 2:
                        inventories.pop(next(iter(inventories)))
                    inventories[key] = files
                return selected, inventories[key]

        # Capture only this selected revision and its immutable file allowlist
        class Handler(BaseHTTPRequestHandler):
            # Bound slow header/body handling on this read-only content listener
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            # Keep local application requests out of operator logs
            def log_message(self, *args):
                pass

            # Send no cookies, CORS grants or control credentials with application content
            def content(self, head=False):
                if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                    self.send_error(403, 'Use the loopback content address'); return
                try:
                    selected_site, selected_inventory = selection()
                except (Refused, OSError, KeyError, ValueError):
                    self.send_error(410, 'Revision evidence unavailable, expired or retired'); return
                try:
                    parsed = urlsplit(self.path)
                    if parsed.scheme or parsed.netloc:
                        self.send_error(403); return
                    requested = unquote(parsed.path)
                    if '..' in Path(requested).parts or '\\' in requested or '\x00' in requested:
                        self.send_error(403); return
                    name = requested.lstrip('/')
                    if not name or name.endswith('/'):
                        name += 'index.html'
                    if any(part in {'prepared.json', 'operator.token', '.ownership.json', '.git', '.deploy-ops', '.deploy_ops'} for part in Path(name).parts):
                        self.send_error(404); return
                    if name not in selected_inventory:
                        self.send_error(404); return
                    data = read_regular(checked_path(selected_site, selected_site / name), 100_000_000)
                    if hashlib.sha256(data).hexdigest() != selected_inventory[name]:
                        self.send_error(410, 'Selected file changed'); return
                except (Refused, OSError, ValueError):
                    self.send_error(410, 'Selected file unavailable'); return
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if not head:
                    self.wfile.write(data)

            # Refusals and successful reads share the same nonpersistent content boundary
            def end_headers(self):
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                super().end_headers()

            # Preserve ordinary GET and HEAD behavior without directory listings
            def do_GET(self):
                self.content()

            # HEAD uses the same path and lifetime checks without a response body
            def do_HEAD(self):
                self.content(True)
        return Handler

    # List only this process's bounded handles, without exposing host paths
    def list(self):
        with self.lock:
            return [self.status(handle) for handle in self.handles.values()]

    # Idempotent stop never uses caller-supplied process identifiers or deletes content
    def stop(self, identifier):
        with self.lock:
            handle = self.handles.pop(identifier, None)
        if handle:
            handle['server'].shutdown()
            handle['server'].server_close()
            handle['thread'].join(timeout=3)
        return {'id': identifier, 'status': 'stopped' if handle else 'not-owned-or-stopped'}

    # Close only this owner's listeners during console shutdown or foreground termination
    def close(self):
        with self.lock:
            self.closed = True
            identifiers = list(self.handles)
        for identifier in identifiers:
            self.stop(identifier)
