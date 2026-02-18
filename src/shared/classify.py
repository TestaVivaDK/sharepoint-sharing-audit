"""Classification helpers for sharing permissions."""

import re

SENSITIVE_PATTERN = re.compile(r"(?i)(ledelse[n]?|datarum|l[øo]n)(/|$)")


def get_sharing_type(permission: dict) -> str:
    """Classify a Graph API permission object into a sharing type string."""
    if "link" in permission:
        link = permission["link"]
        scope = link.get("scope", "")
        match scope:
            case "anonymous":
                return "Link-Anyone"
            case "organization":
                return "Link-Organization"
            case "users":
                return "Link-SpecificPeople"
            case _:
                return "Link-SpecificPeople"

    granted = permission.get("grantedToV2", {})
    if granted.get("group"):
        return "Group"
    if granted.get("user"):
        return "User"

    if permission.get("grantedTo", {}).get("user"):
        return "User"

    return "Unknown"


def get_shared_with_info(permission: dict, tenant_domain: str) -> dict:
    """Extract who the item is shared with and classify audience type."""
    shared_with = ""
    shared_with_type = "Unknown"

    link = permission.get("link")
    if link:
        scope = link.get("scope", "")
        if scope == "anonymous":
            return {"shared_with": "Anyone with the link", "shared_with_type": "Anonymous"}
        if scope == "organization":
            return {"shared_with": "All organization members", "shared_with_type": "Internal"}

        identities_v2 = permission.get("grantedToIdentitiesV2", [])
        if identities_v2:
            names: list[str] = []
            emails: list[str] = []
            for identity in identities_v2:
                user = identity.get("user", {})
                email = user.get("email", "")
                display = user.get("displayName", "")
                if email:
                    names.append(email)
                    emails.append(email)
                elif display:
                    names.append(display)
            shared_with = "; ".join(names)

            has_guest = any("#EXT#" in e for e in emails)
            has_external = any(
                tenant_domain and not e.endswith(f"@{tenant_domain}")
                for e in emails
                if "#EXT#" not in e
            )
            if has_guest:
                shared_with_type = "Guest"
            elif has_external:
                shared_with_type = "External"
            else:
                shared_with_type = "Internal"
            return {"shared_with": shared_with, "shared_with_type": shared_with_type}

        return {"shared_with": "Specific people (details unavailable)", "shared_with_type": "Internal"}

    granted = permission.get("grantedToV2", {})
    group = granted.get("group")
    if group:
        return {"shared_with": group.get("displayName", "Unknown Group"), "shared_with_type": "Internal"}

    user = granted.get("user") or permission.get("grantedTo", {}).get("user")
    if user:
        email = user.get("email", "")
        display = user.get("displayName", "Unknown User")
        shared_with = email or display

        if "#EXT#" in email:
            shared_with_type = "Guest"
        elif email and tenant_domain and not email.endswith(f"@{tenant_domain}"):
            shared_with_type = "External"
        else:
            shared_with_type = "Internal"
        return {"shared_with": shared_with, "shared_with_type": shared_with_type}

    return {"shared_with": shared_with, "shared_with_type": shared_with_type}


def get_risk_level(sharing_type: str, shared_with_type: str, item_path: str) -> str:
    """Assign HIGH/MEDIUM/LOW risk based on sharing type, audience, and file path."""
    if shared_with_type in ("Anonymous", "External", "Guest") or sharing_type == "Link-Anyone":
        return "HIGH"
    if SENSITIVE_PATTERN.search(item_path):
        return "HIGH"
    if sharing_type == "Link-Organization":
        return "MEDIUM"
    return "LOW"


def get_permission_role(permission: dict) -> str:
    """Extract role (Read, Write, Owner) from a permission object."""
    roles = permission.get("roles", [])
    if "owner" in roles:
        return "Owner"
    if "write" in roles:
        return "Write"
    if "read" in roles:
        return "Read"
    link = permission.get("link", {})
    if link.get("type") == "edit":
        return "Write"
    if link.get("type") == "view":
        return "Read"
    return ", ".join(roles) if roles else "Unknown"


def is_teams_chat_file(item_path: str) -> bool:
    """Check if an item path belongs to the Teams chat files folder."""
    return bool(re.search(r"Microsoft Teams[ -]chatfiler|Microsoft Teams Chat Files", item_path, re.IGNORECASE))
