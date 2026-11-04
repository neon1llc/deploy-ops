# src/deploy_ops/delivery/paging_config.py - Validate operator destinations and deterministic incident routes.


import ipaddress
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Keep future integrations visible without accepting unfinished provider implementations
PROVIDERS = ('pagerduty', 'goalert', 'incident.io', 'better-stack', 'grafana-cloud-irm', 'ilert', 'json', 'slack', 'ntfy')
PLANNED = ('apprise',)
NAME = re.compile(r'^[a-z][a-z0-9-]{0,47}$')
REFERENCE = re.compile(r'^DEPLOY_OPS_PAGING_[A-Z0-9_]{1,80}$')
CAPABILITIES = {
    'pagerduty': {'trigger': True, 'acknowledge': True, 'resolve': True, 'readback': False, 'notifications': False},
    'goalert': {'trigger': True, 'acknowledge': False, 'resolve': True, 'readback': False, 'notifications': False},
    'incident.io': {'trigger': True, 'acknowledge': False, 'resolve': True, 'readback': False, 'notifications': False},
    'better-stack': {'trigger': True, 'acknowledge': True, 'resolve': True, 'readback': True, 'notifications': False},
    'grafana-cloud-irm': {'trigger': True, 'acknowledge': False, 'resolve': True, 'readback': False, 'notifications': False},
    'ilert': {'trigger': True, 'acknowledge': True, 'resolve': True, 'readback': False, 'notifications': False},
    **{name: {'trigger': False, 'acknowledge': False, 'resolve': False, 'readback': False, 'notifications': True}
       for name in ('json', 'slack', 'ntfy')},
}


# Refuse unknown settings rather than hiding unsupported behavior
class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


# Keep secrets in explicitly named worker environment variables
class Destination(Record):
    provider: Literal['pagerduty', 'goalert', 'incident.io', 'better-stack', 'grafana-cloud-irm', 'ilert', 'json', 'slack', 'ntfy']
    enabled: bool = True
    key_env: str = ''
    endpoint: str = ''
    endpoint_env: str = ''
    token_env: str = ''
    signing_env: str = ''
    alert_source_id: str = ''
    requester_email: str = ''
    policy_id: str = ''
    team_name: str = Field(default='', max_length=200)
    template_mapping_confirmed: bool = False
    priority_mode: Literal['source', 'severity'] = 'source'
    allowed_networks: list[str] = Field(default_factory=list)
    attempts: int = Field(default=5, ge=1, le=10)
    retry_seconds: int = Field(default=60, ge=1, le=3600)


    # Validate the provider's actual credential and endpoint contract
    @model_validator(mode='after')
    def valid(self):
        for reference in (self.key_env, self.endpoint_env, self.token_env, self.signing_env):
            if reference and not REFERENCE.fullmatch(reference):
                raise ValueError('Use a DEPLOY_OPS_PAGING_ environment reference, never a credential value')
        if self.provider in ('pagerduty', 'goalert', 'incident.io', 'better-stack', 'ilert'):
            if not self.key_env or self.endpoint_env or self.token_env or self.signing_env:
                raise ValueError('Native paging requires key_env and no generic transport credentials')
            if self.provider == 'pagerduty' and (self.endpoint or self.allowed_networks):
                raise ValueError('PagerDuty uses its fixed US Events API endpoint')
            if self.provider in ('incident.io', 'better-stack', 'ilert') and (self.endpoint or self.allowed_networks):
                raise ValueError('Hosted native paging uses a fixed provider endpoint and public networks')
            if self.provider == 'goalert':
                parsed = urlsplit(self.endpoint)
                if parsed.port is not None and not 1 <= parsed.port <= 65535:
                    raise ValueError('Use a valid HTTPS port')
                if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                        or parsed.query or parsed.fragment or not parsed.path.endswith('/api/v2/generic/incoming')):
                    raise ValueError('GoAlert needs a public HTTPS generic endpoint without tokens or query parameters')
        elif self.provider == 'grafana-cloud-irm':
            if (not self.endpoint_env or self.endpoint or self.key_env or self.signing_env or self.allowed_networks
                    or not self.template_mapping_confirmed):
                raise ValueError('Grafana Cloud IRM requires an endpoint reference and confirmed grouping/autoresolve mapping')
        elif not self.endpoint_env or self.endpoint or self.key_env:
            raise ValueError('Notification URLs belong in endpoint_env, not exported configuration')

        # Provider-specific fields remain explicit and cannot silently select another account or route
        if self.provider == 'incident.io':
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.alert_source_id):
                raise ValueError('incident.io requires the HTTP alert source configuration ID')
        elif self.alert_source_id:
            raise ValueError('alert_source_id is only supported by incident.io')
        if self.provider == 'better-stack':
            if (not re.fullmatch(r'[^\s@]{1,64}@[^\s@.]+(?:\.[^\s@.]+)+', self.requester_email)
                    or len(self.requester_email) > 254 or not re.fullmatch(r'[0-9]{1,32}', self.policy_id)):
                raise ValueError('Better Stack requires a requester email and existing numeric escalation policy ID')
        elif self.requester_email or self.policy_id or self.team_name:
            raise ValueError('Requester, policy and team settings are only supported by Better Stack')
        if self.template_mapping_confirmed and self.provider != 'grafana-cloud-irm':
            raise ValueError('Template mapping confirmation is only supported by Grafana Cloud IRM')
        if self.priority_mode != 'source' and self.provider != 'ilert':
            raise ValueError('Priority mapping is only supported by ilert')
        for network in self.allowed_networks:
            value = ipaddress.ip_network(network, strict=True)
            if value.is_link_local or value.is_multicast or value.is_unspecified or value.prefixlen == 0:
                raise ValueError('Allow only specific trusted networks, never metadata or all addresses')
        return self


# Match one ordered route and send to its explicit destination list
class Route(Record):
    name: str
    projects: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    severities: list[Literal['critical', 'error', 'warning', 'info']] = Field(default_factory=list)
    destinations: list[str] = Field(default_factory=list)
    fallback: str = ''
    quiet_until: str = ''


    # Require an explicit timezone and finite end for temporary suppression
    @model_validator(mode='after')
    def valid(self):
        if not NAME.fullmatch(self.name):
            raise ValueError('Use a lowercase route name with optional hyphens')
        if self.quiet_until:
            instant = datetime.fromisoformat(self.quiet_until.replace('Z', '+00:00'))
            if instant.utcoffset() is None:
                raise ValueError('Quiet-until needs an explicit timezone offset')
        return self


# Use the same model for TOML import, guided forms and durable activation
class Paging(Record):
    schema_version: Literal[1] = 1
    destinations: dict[str, Destination] = Field(default_factory=dict)
    default_destinations: list[str] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)


    # Bind every route and fallback to a distinct named destination
    @model_validator(mode='after')
    def valid(self):
        if any(not NAME.fullmatch(name) for name in self.destinations):
            raise ValueError('Use lowercase destination names with optional hyphens')
        references = list(self.default_destinations)
        names = []
        for route in self.routes:
            names.append(route.name)
            references.extend(route.destinations)
            if route.fallback:
                references.append(route.fallback)
                if route.fallback in route.destinations:
                    raise ValueError('Fallback must differ from the primary destinations')
        if len(set(names)) != len(names) or set(references) - self.destinations.keys():
            raise ValueError('Use unique route names and configured destinations')
        return self
