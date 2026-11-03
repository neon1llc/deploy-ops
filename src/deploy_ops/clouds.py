# src/deploy_ops/clouds.py - Exchange short-lived workflow identity for provider credentials.

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import quote

import httpx

from .errors import Refused
from .models import Cloud


def environment(cloud: Cloud, identity: dict, token: Callable[[str], str],
                session: str, client: httpx.Client) -> dict[str, str]:
    """Return credentials only to the runner, never to the approval request or ledger."""
    if cloud == "local":
        return {}
    try:
        if cloud == "aws":
            region = identity["region"]
            role = identity["role_arn"]
            valid_region = re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", region)
            valid_role = re.fullmatch(r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+", role)
            if not valid_region or not valid_role:
                raise ValueError("Unsupported AWS region or role")
            response = client.post(f"https://sts.{region}.amazonaws.com/", data={
                "Action": "AssumeRoleWithWebIdentity", "Version": "2011-06-15",
                "RoleArn": role, "RoleSessionName": "deploy_ops-" + session[:32],
                "WebIdentityToken": token("sts.amazonaws.com"), "DurationSeconds": "900",
            })
            response.raise_for_status()
            root = ET.fromstring(response.content)
            fields = {element.tag.rsplit("}", 1)[-1]: element.text for element in root.iter()}
            result = {
                "AWS_ACCESS_KEY_ID": fields["AccessKeyId"], "AWS_SECRET_ACCESS_KEY": fields["SecretAccessKey"],
                "AWS_SESSION_TOKEN": fields["SessionToken"], "AWS_REGION": region,
                "AWS_DEFAULT_REGION": region, "AWS_EC2_METADATA_DISABLED": "true",
            }
        elif cloud == "azure":
            for key in ("client_id", "tenant_id", "subscription_id"):
                if not re.fullmatch(r"[0-9a-fA-F-]{36}", identity[key]):
                    raise ValueError("Invalid Azure identity")
            # The AzureRM provider exchanges this assertion and caches its access token
            result = {
                "ARM_USE_OIDC": "true", "ARM_USE_CLI": "false", "ARM_USE_MSI": "false",
                "ARM_CLIENT_ID": identity["client_id"], "ARM_TENANT_ID": identity["tenant_id"],
                "ARM_SUBSCRIPTION_ID": identity["subscription_id"],
                "ARM_OIDC_TOKEN": token("api://AzureADTokenExchange"),
                "ARM_USE_AZUREAD": "true",
            }
        else:
            provider = identity["workload_identity_provider"]
            account = identity["service_account"]
            if not re.fullmatch(
                r"projects/[0-9]+/locations/global/workloadIdentityPools/"
                r"[a-z0-9-]+/providers/[a-z0-9-]+", provider
            ):
                raise ValueError("Invalid Google identity provider")
            if not re.fullmatch(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", account):
                raise ValueError("Invalid Google service account")
            response = client.post("https://sts.googleapis.com/v1/token", json={
                "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
                "audience": "//iam.googleapis.com/" + provider,
                "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
                "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
                "subjectToken": token("https://iam.googleapis.com/" + provider),
                "scope": "https://www.googleapis.com/auth/cloud-platform",
            })
            response.raise_for_status()
            federated = response.json()["access_token"]
            response = client.post(
                "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                + quote(account, safe="") + ":generateAccessToken",
                headers={"Authorization": "Bearer " + federated},
                json={"scope": ["https://www.googleapis.com/auth/cloud-platform"], "lifetime": "900s"},
            )
            response.raise_for_status()
            result = {"GOOGLE_OAUTH_ACCESS_TOKEN": response.json()["accessToken"],
                      "GOOGLE_PROJECT": identity["project_id"], "GOOGLE_CLOUD_PROJECT": identity["project_id"]}
        if any(not isinstance(value, str) or not value for value in result.values()):
            raise ValueError("Cloud returned empty credentials")
        return result
    except (httpx.HTTPError, ValueError, KeyError, ET.ParseError) as error:
        raise Refused("Cloud federation failed. Check the target's trust policy and scoped identity", 503) from error
