# src/deploy_ops/delivery/paging_providers.py - Send bounded native paging requests without retaining secrets.


import hashlib
import hmac
import http.client
import ipaddress
import io
import json
import os
import queue
import socket
import ssl
import threading
import time
import uuid
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from ..errors import Refused
from .paging_config import Destination


# Bound resolver concurrency separately from authenticated socket activity
RESOLVERS = threading.BoundedSemaphore(4)


# Stop slow response headers and trickled bodies at one transport deadline
class DeadlineReader(io.RawIOBase):
    # Bind the verified socket to the total request deadline
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
        self.stream = sock.makefile("rb", buffering=0)


    # Let the HTTP parser buffer bounded reads without resetting the total deadline
    def readable(self):
        return True


    # Apply the remaining lifetime before every underlying socket read
    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Paging transport deadline expired')
        self.sock.settimeout(min(10, remaining))
        return self.stream.readinto(buffer)


    # Keep the native file reference alive when HTTP closes its connection before body consumption
    def close(self):
        self.stream.close()
        super().close()


# Preserve HTTPSConnection framing while enforcing the shared read and write deadline
class DeadlineSocket:
    # Keep TLS ownership and the deadline together
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline


    # Bound an entire request write by its remaining transport lifetime
    def sendall(self, data):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Paging transport deadline expired')
        self.sock.settimeout(min(10, remaining))
        self.sock.sendall(data)


    # Supply the native response parser with a deadline-aware buffered stream
    def makefile(self, mode):
        return io.BufferedReader(DeadlineReader(self.sock, self.deadline))


    # Release the verified TLS socket through the connection's normal owner
    def close(self):
        self.sock.close()


# Resolve without letting a stalled resolver create unbounded workers or sends
def resolve(host, port):
    if not RESOLVERS.acquire(blocking=False):
        raise TimeoutError('Paging resolver capacity reached')
    result = queue.Queue(maxsize=1)


    # The resolver thread cannot send authenticated requests after its caller times out
    def work():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as error:
            result.put(error)
        finally:
            RESOLVERS.release()

    threading.Thread(target=work, daemon=True).start()
    try:
        addresses = result.get(timeout=10)
    except queue.Empty:
        raise TimeoutError('Paging name resolution timed out') from None
    if isinstance(addresses, OSError):
        raise addresses
    return addresses


# Read only the explicitly scoped worker reference at the transport boundary
def credential(reference):
    value = os.environ.get(reference, '') if reference else ''
    if reference and not value:
        raise Refused('A required paging environment reference is unavailable', 422)
    return value


# Reject metadata, redirects and DNS rebinding before opening an authenticated connection
def request(url, body, headers, networks):
    try:
        parsed = urlsplit(url)
        port = parsed.port if parsed.port is not None else 443
        if port < 1:
            raise ValueError("Invalid port")
    except ValueError:
        raise Refused('Paging endpoint or port is invalid', 422) from None
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment):
        raise Refused('Paging endpoints must use HTTPS without embedded credentials', 422)
    allowed = [ipaddress.ip_network(network) for network in networks]
    try:
        addresses = resolve(parsed.hostname, port)
    except OSError:
        return -1, {}, b''
    candidates = []
    for family, socktype, protocol, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        normalized = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
        if (normalized.is_link_local or normalized.is_multicast or normalized.is_unspecified
                or normalized.is_reserved or str(normalized) in {'168.63.129.16', '100.100.100.200'}):
            raise Refused('Paging cannot contact metadata or reserved addresses', 422)
        if not normalized.is_global and not any(normalized in network for network in allowed):
            raise Refused('Approve the destination private network before sending', 422)
        candidates.append((family, socktype, protocol, address))
    if not candidates:
        raise Refused('Paging endpoint has no approved address', 422)

    # Pin the resolved socket while preserving the hostname for TLS and HTTP
    family, socktype, protocol, address = candidates[0]
    connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=10)
    raw = socket.socket(family, socktype, protocol)
    raw.settimeout(10)
    sent = False
    response = None
    deadline = time.monotonic() + 30
    try:
        raw.connect(address)
        secured = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        connection.sock = DeadlineSocket(secured, deadline)
        sent = True
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        connection.request('POST', path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(65537)
        return response.status, dict(response.getheaders()), data[:65536]
    except (OSError, http.client.HTTPException):
        # Once HTTP writing began, loss of the response cannot establish rejection
        return (0 if sent else -1), {}, b''
    finally:
        if response is not None:
            response.close()
        connection.close()
        raw.close()


# Classify provider acceptance separately from human response or remote readback
def send(settings, payload, action, dedup, identity='', transport=None):
    destination = Destination.model_validate(settings)
    transport = transport or request
    headers = {'Content-Type': 'application/json'}
    provider = destination.provider
    key = credential(destination.key_env)
    key_digest = hashlib.sha256(key.encode()).hexdigest() if key else ''
    if identity and key_digest != identity:
        return {'state': 'failed', 'reason': 'Originating integration credential changed', 'retryable': False}
    summary = f"DeployOps {'TEST ' if payload.get('synthetic') else ''}{payload['event']} for {payload['release_id']}"[:1024]

    # Keep vendor lifecycle fields inside the native adapter boundary
    if provider == 'pagerduty':
        url = 'https://events.pagerduty.com/v2/enqueue'
        data = {'routing_key': key, 'dedup_key': dedup, 'event_action': action}
        if action == 'trigger':
            data['payload'] = {'summary': summary, 'source': payload.get('project') or 'DeployOps',
                               'severity': payload.get('severity', 'error')}
    elif provider == 'goalert':
        url = destination.endpoint
        headers.update(Authorization='Bearer ' + key, Accept='application/json')
        data = {'summary': summary, 'dedup': dedup}
        if action == 'resolve':
            data['action'] = 'close'
        if action not in ('trigger', 'resolve'):
            raise Refused('GoAlert generic API does not support acknowledgement', 422)
    else:
        url = credential(destination.endpoint_env)
        data = {'text': summary} if provider == 'slack' else payload
        token = credential(destination.token_env)
        if token:
            headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
    if provider == 'ntfy':
        body = summary.encode()
        headers.update({'Content-Type': 'text/plain', 'Title': 'DeployOps'})
    signing = credential(destination.signing_env)
    if signing:
        headers['X-DeployOps-Signature'] = 'sha256=' + hmac.new(signing.encode(), body, hashlib.sha256).hexdigest()

    # Only normalized facts may escape the transport owner
    code, response_headers, raw = transport(url, body, headers, destination.allowed_networks)
    result = {'state': 'failed', 'reason': 'Request rejected', 'retryable': False,
              'http_status': max(0, code), 'identity': key_digest}
    if code == 0:
        result.update(state='unknown', reason='Response lost after sending; remote outcome is unknown')
    elif code == -1:
        result.update(reason='Connection failed before sending', retryable=True)
    elif code == 429:
        result.update(reason='Provider throttled the request', retryable=True)
    elif 500 <= code < 600:
        result.update(state='unknown', reason='Provider error may follow acceptance')
    elif (provider == 'pagerduty' and code == 202) or (provider != 'pagerduty' and 200 <= code < 300):
        result.update(state='accepted', reason='Provider accepted the request; remote response is unverified')
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            response = {}
        if provider == 'pagerduty' and (not isinstance(response, dict) or response.get('dedup_key') != dedup
                                       or response.get('status') != 'success'):
            result.update(state='unknown', reason='Acceptance response did not identify the requested alert')
        if provider == 'goalert' and isinstance(response, dict):
            if type(response.get('AlertID')) is int and response['AlertID'] > 0:
                result['remote_id'] = str(response['AlertID'])
            try:
                result['service_id'] = str(uuid.UUID(response.get('ServiceID', '')))
            except (ValueError, TypeError, AttributeError):
                pass

    # Honor bounded numeric or HTTP-date backoff without retaining response headers
    retry = next((value for name, value in response_headers.items() if name.lower() == 'retry-after'), '')
    try:
        result['retry_after'] = max(0, min(86400, int(retry)))
    except (TypeError, ValueError):
        try:
            result['retry_after'] = max(0, min(86400, int(parsedate_to_datetime(retry).timestamp() - time.time())))
        except (TypeError, ValueError, OverflowError):
            pass
    return result
