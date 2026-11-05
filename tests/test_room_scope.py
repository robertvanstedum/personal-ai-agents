import pytest
from core.room_scope import meeting_scope, owner_private


@pytest.mark.parametrize('members,expected', [
    ([{'id': 'robert'}, {'id': 'mc'}, {'id': 'cos'}], True),
    ([{'id': 'robert'}, {'id': 'cos'}], True),
    ([{'id': 'robert'}, {'id': 'guest'}], False),
    ([], False), (None, False), ('robert', False), ([{'name': 'robert'}], False), (['robert'], False),
])
def test_owner_private(members, expected):
    assert owner_private(members) is expected
    assert meeting_scope(members) == ('owner_private' if expected else 'closed')


def test_principal_is_not_hardcoded():
    assert owner_private([{'id': 'ana'}, {'id': 'mc'}], principal='ana')
    assert not owner_private([{'id': 'robert'}], principal='ana')
    assert not owner_private([{'id': 'mc'}], principal='')
