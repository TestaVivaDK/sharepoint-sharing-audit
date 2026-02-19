"""Microsoft Graph API client for collecting sharing data."""

import logging
import time

from azure.identity import ClientSecretCredential
import httpx

logger = logging.getLogger(__name__)


class GraphClient:
    def __init__(
        self, tenant_id: str, client_id: str, client_secret: str, delay_ms: int = 100
    ):
        self.delay_ms = delay_ms
        self._credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        self._token: str | None = None
        self._token_expires_at: float = 0

    def _get_token(self) -> str:
        """Get or refresh the access token."""
        if not self._token or time.time() >= self._token_expires_at - 300:
            token = self._credential.get_token("https://graph.microsoft.com/.default")
            self._token = token.token
            self._token_expires_at = token.expires_on
        return self._token

    def _make_request(self, url: str, params: dict | None = None) -> dict:
        """Make a GET request to the Graph API with retry logic."""
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        for attempt in range(4):
            try:
                resp = httpx.get(url, headers=headers, params=params, timeout=30)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(f"Rate limited. Waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    self._token = None
                    continue
                if attempt < 3 and e.response.status_code >= 500:
                    time.sleep(2**attempt)
                    continue
                raise
        return {}

    def _make_paged_request(self, url: str, params: dict | None = None) -> list[dict]:
        """Follow @odata.nextLink pagination."""
        results = []
        while url:
            data = self._make_request(url, params)
            results.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None
            if self.delay_ms > 0:
                time.sleep(self.delay_ms / 1000)
        return results

    def get_tenant_domain(self) -> str:
        """Get the default verified domain for the tenant."""
        data = self._make_request("https://graph.microsoft.com/v1.0/organization")
        orgs = data.get("value", [])
        if orgs:
            for domain in orgs[0].get("verifiedDomains", []):
                if domain.get("isDefault"):
                    return domain["name"]
        return ""

    def get_users(self, upns: list[str] | None = None) -> list[dict]:
        """Get licensed, enabled users. If upns provided, fetch those specific users."""
        if upns:
            users = []
            for upn in upns:
                try:
                    data = self._make_request(
                        f"https://graph.microsoft.com/v1.0/users/{upn}"
                    )
                    users.append(data)
                except Exception as e:
                    logger.warning(f"Could not find user {upn}: {e}")
            return users

        all_users = self._make_paged_request(
            "https://graph.microsoft.com/v1.0/users",
            {
                "$filter": "accountEnabled eq true",
                "$select": "id,displayName,userPrincipalName,accountEnabled,assignedLicenses",
            },
        )
        return [
            u
            for u in all_users
            if u.get("accountEnabled") and u.get("assignedLicenses")
        ]

    def get_user_drive(self, user_id: str) -> dict | None:
        """Get a user's default OneDrive drive."""
        try:
            return self._make_request(
                f"https://graph.microsoft.com/v1.0/users/{user_id}/drive"
            )
        except Exception as e:
            logger.warning(f"No OneDrive for user {user_id}: {e}")
            return None

    def get_all_sites(self) -> list[dict]:
        """Enumerate all SharePoint sites via getAllSites."""
        return self._make_paged_request(
            "https://graph.microsoft.com/v1.0/sites/getAllSites",
            {"$select": "id,displayName,webUrl", "$top": "1000"},
        )

    def get_site_drives(self, site_id: str) -> list[dict]:
        """Get all document libraries for a site."""
        return self._make_paged_request(
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives",
        )

    def get_drive_children(self, drive_id: str, item_id: str) -> list[dict]:
        """Get children of a drive item."""
        data = self._make_request(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children"
        )
        return data.get("value", [])

    def get_item_permissions(self, drive_id: str, item_id: str) -> list[dict]:
        """Get non-inherited permissions for a drive item."""
        data = self._make_request(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/permissions"
        )
        permissions = data.get("value", [])
        return [
            p
            for p in permissions
            if not (
                p.get("inheritedFrom", {}).get("driveId")
                or p.get("inheritedFrom", {}).get("path")
            )
        ]

    def throttle(self):
        """Pause between API calls."""
        if self.delay_ms > 0:
            time.sleep(self.delay_ms / 1000)
