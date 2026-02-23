"""Tests for sharing classification helpers."""

from shared.classify import get_sharing_type, get_shared_with_info, get_risk_level


class TestGetSharingType:
    def test_anonymous_link(self):
        perm = {"link": {"scope": "anonymous"}}
        assert get_sharing_type(perm) == "Link-Anyone"

    def test_organization_link(self):
        perm = {"link": {"scope": "organization"}}
        assert get_sharing_type(perm) == "Link-Organization"

    def test_specific_people_link(self):
        perm = {"link": {"scope": "users"}}
        assert get_sharing_type(perm) == "Link-SpecificPeople"

    def test_link_no_scope(self):
        perm = {"link": {}}
        assert get_sharing_type(perm) == "Link-SpecificPeople"

    def test_group_permission(self):
        perm = {"grantedToV2": {"group": {"displayName": "Marketing"}}}
        assert get_sharing_type(perm) == "Group"

    def test_user_permission(self):
        perm = {"grantedToV2": {"user": {"email": "a@test.dk"}}}
        assert get_sharing_type(perm) == "User"


class TestGetSharedWithInfo:
    def test_anonymous(self):
        perm = {"link": {"scope": "anonymous"}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with"] == "Anyone with the link"
        assert info["shared_with_type"] == "Anonymous"

    def test_organization(self):
        perm = {"link": {"scope": "organization"}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Internal"

    def test_external_email(self):
        perm = {"grantedToV2": {"user": {"email": "ext@gmail.com"}}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "External"

    def test_internal_email(self):
        perm = {"grantedToV2": {"user": {"email": "a@test.dk"}}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Internal"

    def test_display_name_only_is_internal(self):
        """Display names without @ should not be classified as external."""
        perm = {
            "link": {"scope": "users"},
            "grantedToIdentitiesV2": [{"user": {"displayName": "Algoritmen"}}],
        }
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Internal"

    def test_guest_ext_hash(self):
        perm = {"grantedToV2": {"user": {"email": "ext_gmail.com#EXT#@test.dk"}}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Guest"


class TestGetRiskLevel:
    def test_anonymous_is_high(self):
        assert get_risk_level("Link-Anyone", "Anonymous", "") == "HIGH"

    def test_external_is_high(self):
        assert get_risk_level("Link-SpecificPeople", "External", "") == "HIGH"

    def test_guest_is_high(self):
        assert get_risk_level("User", "Guest", "") == "HIGH"

    def test_sensitive_folder_is_high(self):
        assert (
            get_risk_level(
                "Link-SpecificPeople", "Internal", "/Documents/Ledelse/Budget.xlsx"
            )
            == "HIGH"
        )

    def test_sensitive_folder_løn(self):
        assert (
            get_risk_level(
                "Link-SpecificPeople", "Internal", "/Documents/Løn/salaries.xlsx"
            )
            == "HIGH"
        )

    def test_sensitive_folder_datarum(self):
        assert get_risk_level("User", "Internal", "/Datarum/contracts.pdf") == "HIGH"

    def test_org_wide_is_medium(self):
        assert get_risk_level("Link-Organization", "Internal", "") == "MEDIUM"

    def test_specific_internal_is_low(self):
        assert (
            get_risk_level("Link-SpecificPeople", "Internal", "/Documents/report.xlsx")
            == "LOW"
        )

    def test_user_internal_is_low(self):
        assert get_risk_level("User", "Internal", "/Documents/notes.docx") == "LOW"
