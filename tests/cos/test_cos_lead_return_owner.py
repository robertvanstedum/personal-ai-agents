"""CoS helper return names the owner the way the task service does, and only the owner."""
import pytest
from domains.cos.lead_return import lead_principal, owner_accounts

ENV = {"LEAD_RETURN_PRINCIPAL": "robert", "LEAD_RETURN_OWNER_ACCOUNTS": " 7 ,robert,"}


def test_owner_accounts_lists_the_principal_then_his_portal_ids():
    assert owner_accounts(ENV) == ("robert", "7")
    assert owner_accounts({"LEAD_RETURN_PRINCIPAL": "robert"}) == ("robert",)


@pytest.mark.parametrize("account,want", [("7", "robert"), ("robert", "robert"), ("8", "8"), ("u-guest-abc123", "u-guest-abc123")])
def test_only_the_owners_accounts_map_to_the_principal(account, want):
    # Another account keeps its own name, so inline.offer refuses it: no owner return for anyone else.
    assert lead_principal(account, ENV) == want
