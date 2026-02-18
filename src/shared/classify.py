"""Classification helpers for sharing permissions."""

import os
import re

# Sensitive Danish keywords — matched against both folder names and filenames
SENSITIVE_KEYWORDS = re.compile(
    r"(?i)"
    r"(l[øo]n"              # løn, lønseddel, lønoplysninger
    r"|ledelse"             # ledelse, ledelsen
    r"|direktion"           # direktion, direktionen
    r"|bestyrelse"          # bestyrelse, bestyrelsesmøde
    r"|datarum"             # data room
    r"|personale"           # personale, personalemappe
    r"|ans[æa]tt"           # ansættelse, ansættelseskontrakt
    r"|opsigelse"           # opsigelse, opsigelser
    r"|fratr[æa]d"          # fratrædelse
    r"|regnskab"            # regnskab, regnskaber
    r"|budget"              # budget, budgetter
    r"|[øo]konomi"          # økonomi, ekonomi
    r"|faktura"             # faktura, fakturaer
    r"|kontrakt"            # kontrakt, kontrakter
    r"|fortrolig"           # fortrolig, fortroligt
    r"|hemmelig"            # hemmelig, hemmeligt
    r"|persondata"          # persondata
    r"|cpr"                 # CPR-nummer
    r"|personfølsom"        # personfølsom, personfølsomme
    r"|sundhed"             # sundhed, sundhedsoplysninger
    r"|syge"                # syge, sygefravær, sygedagpenge
    r"|gdpr"                # GDPR
    r"|pension"             # pension, pensionsordning
    r"|ferie"               # ferie, ferieregnskab
    r"|revision"            # revision (audit)
    r"|inkasso"             # inkasso (debt collection)
    r"|gæld"                # gæld (debt)
    r"|erstatning"          # erstatning (compensation/damages)
    r"|disciplin[æa]r"      # disciplinær, disciplinærsag
    r"|advarsel"            # advarsel (warning)
    r"|klage"               # klage (complaint)
    r")"
)

# High-risk file extensions (contain structured/sensitive data)
SENSITIVE_EXTENSIONS = {
    ".xlsx", ".xls", ".csv", ".pdf", ".docx", ".doc",
    ".pptx", ".ppt", ".accdb", ".mdb",
}

# Low-risk file extensions (media/images rarely contain sensitive data)
LOW_RISK_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".ico",
    ".mp4", ".mov", ".avi", ".mp3", ".wav",
}


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


def is_sensitive_path(item_path: str) -> bool:
    """Check if a file/folder path contains sensitive Danish keywords."""
    return bool(SENSITIVE_KEYWORDS.search(item_path))


def get_risk_level(sharing_type: str, shared_with_type: str, item_path: str) -> str:
    """Assign HIGH/MEDIUM/LOW risk based on sharing type, audience, and file path."""
    if shared_with_type in ("Anonymous", "External", "Guest") or sharing_type == "Link-Anyone":
        return "HIGH"
    if is_sensitive_path(item_path):
        return "HIGH"
    if sharing_type == "Link-Organization":
        return "MEDIUM"
    return "LOW"


def compute_risk_score(
    shared_with_type: str,
    sharing_type: str,
    item_path: str,
    role: str,
    item_type: str = "File",
    recipient_count: int = 1,
) -> int:
    """Compute a 0–100 risk score based on weighted factors.

    Factors:
      Audience scope  (0–30): who can access the item
      Recipient count (0–15): how many people have access
      Sensitive path  (0–20): keywords in folder/file name
      File type       (0–15): extension indicates data risk
      Permission      (0–10): edit vs read-only
      Asset type      (0–10): folder exposes more than a single file
    """
    score = 0

    # 1. Audience scope (max 30)
    if shared_with_type == "Anonymous" or sharing_type == "Link-Anyone":
        score += 30
    elif shared_with_type in ("External", "Guest"):
        score += 25
    elif sharing_type == "Link-Organization":
        score += 15
    else:
        score += 5

    # 2. Recipient count (max 15)
    if recipient_count >= 20 or shared_with_type == "Anonymous":
        score += 15
    elif recipient_count >= 6:
        score += 10
    elif recipient_count >= 2:
        score += 5
    else:
        score += 2

    # 3. Sensitive path (max 20)
    if is_sensitive_path(item_path):
        score += 20

    # 4. File extension (max 15)
    ext = os.path.splitext(item_path)[1].lower()
    if ext in SENSITIVE_EXTENSIONS:
        score += 15
    elif ext in LOW_RISK_EXTENSIONS:
        score += 3
    else:
        score += 8

    # 5. Permission level (max 10)
    if role in ("Write", "Owner"):
        score += 10
    elif role == "Read":
        score += 3
    else:
        score += 5

    # 6. Asset type (max 10)
    if item_type == "Folder":
        score += 10
    else:
        score += 3

    return min(score, 100)


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


def get_granted_by(permission: dict) -> str:
    """Extract who granted this permission. Returns email or empty string."""
    granted_by = permission.get("grantedByV2", {}) or permission.get("grantedBy", {})
    user = granted_by.get("user", {})
    return user.get("email", "")
