"""Sharing projects directly with users (issue #178; spec: docs/architecture/specs/personal-workspaces.md, ADR-0101).

Only the project owner shares, as viewer or editor, with any known user (by id or by a trusted, unambiguous e-mail
address, ADR-0091) and revokes. The effective role: owner of the project's workspace → owner; a share → that role;
otherwise none (404).
"""

from __future__ import annotations

import pytest

from arcviz.projects import Project, ProjectError
from arcviz.services import Forbidden, ServiceRoot
from arcviz.services.access import ProjectNotFound
from arcviz.workspaces import Identity, Workspace, allows, personal_project_id, personal_workspace_id, project_role
from tests.conftest import proxy_users

ADA, BOB, EVE, ZOE = (
    Identity(n, n.capitalize(), f"{n}@example.org", email_trusted=True) for n in ("ada", "bob", "eve", "zoe")
)


def personal(owner: str) -> Workspace:
    return Workspace(id=personal_workspace_id(owner), title="Persönlich", owner=owner, personal=True)


def test_project_role_table():
    home = personal("ada")
    project = Project(id="shop", title="Shop", workspace=home.id, roles={"bob": "editor", "eve": "viewer"})
    assert project_role("ada", home, project) == "owner"  # the owner of the personal workspace
    assert project_role("bob", home, project) == "editor"  # a share
    assert project_role("eve", home, project) == "viewer"
    assert project_role("zoe", home, project) is None  # no default role: nothing shared, nothing visible
    assert project_role("ada", home, project.model_copy(update={"workspace": "other"})) is None
    assert project_role("ada", None, project) is None  # unassigned data is invisible
    m20_owner = project.model_copy(update={"roles": {"bob": "owner"}})
    assert project_role("bob", home, m20_owner) is None  # exactly one owner: an "owner" entry grants nothing
    assert project_role("bob", home, project.model_copy(update={"roles": {"bob": "admin"}})) is None
    assert allows("owner", "editor") and allows("editor", "viewer") and not allows("viewer", "editor")
    assert not allows(None, "viewer")


def test_nobody_has_access_outside_a_personal_workspace():
    """ADR-0103: an M20 team workspace (not yet migrated) gives nobody access, its owner and members included."""
    team = Workspace(id="team", title="Team", owner="ada", members=[{"user": "bob", "added_at": "t"}])
    project = Project(id="shop", title="Shop", workspace="team", roles={"bob": "editor"})
    assert project_role("ada", team, project) is None and project_role("bob", team, project) is None
    forged = Workspace(id="team", title="Team", owner="ada", personal=True)  # personal flag, but not ada's id
    assert project_role("ada", forged, project) is None


@pytest.fixture
def root(store):
    root = ServiceRoot(store)
    for identity in (ADA, BOB, EVE, ZOE):
        root.users.seen(identity)
    return root


def as_(root: ServiceRoot, identity: Identity):
    return root.for_actor(identity).projects


def test_a_new_project_belongs_to_its_creator_alone(root):
    created = as_(root, BOB).create("Shop")
    assert created.workspace == personal_workspace_id("bob") and created.roles == {}
    assert as_(root, BOB).view("shop").role == "owner"
    for other in (ADA, EVE, ZOE):
        with pytest.raises(ProjectNotFound, match="not found"):
            as_(root, other).get("shop")
        assert as_(root, other).list_projects() == []


def test_the_owner_shares_and_revokes(root, store):
    as_(root, BOB).create("Shop")
    access = as_(root, BOB).share("shop", "eve", "viewer")
    assert [(s.id, s.role) for s in access.shares] == [("eve", "viewer")]
    assert as_(root, EVE).view("shop").role == "viewer"
    with pytest.raises(Forbidden, match="Read-only access"):
        as_(root, EVE).apply("shop", "rename_project", {"title": "Mine"})
    as_(root, BOB).share("shop", "eve@example.org", "editor")  # by trusted e-mail: changes the share
    assert store.read_project("shop")[0].roles == {"eve": "editor"}
    as_(root, EVE).apply("shop", "rename_project", {"title": "Shop 2"})  # now allowed
    summary = as_(root, EVE).list_projects()[0]
    assert (summary.id, summary.role, summary.shared, summary.owner) == ("shop", "editor", True, "bob")
    own = as_(root, BOB).list_projects()[0]
    assert (own.role, own.shared, own.owner) == ("owner", False, "bob")
    access = as_(root, BOB).revoke("shop", "eve")
    assert access.shares == [] and store.read_project("shop")[0].roles == {}
    with pytest.raises(ProjectNotFound):
        as_(root, EVE).get("shop")
    as_(root, BOB).revoke("shop", "eve")  # revoking again changes nothing


def test_only_the_owner_shares(root):
    as_(root, BOB).create("Shop")
    as_(root, BOB).share("shop", "eve", "editor")
    with pytest.raises(Forbidden):
        as_(root, EVE).share("shop", "zoe", "viewer")  # editors cannot re-share
    with pytest.raises(Forbidden):
        as_(root, EVE).revoke("shop", "eve")
    with pytest.raises(Forbidden):
        as_(root, EVE).delete("shop")
    with pytest.raises(ProjectNotFound):
        as_(root, ZOE).share("shop", "zoe", "editor")  # an outsider learns nothing


def test_what_can_be_shared(root):
    as_(root, BOB).create("Shop")
    owner = as_(root, BOB)
    with pytest.raises(ProjectError, match="Cannot share with 'nobody'"):
        owner.share("shop", "nobody", "viewer")  # only users who signed in once
    with pytest.raises(ProjectError, match="role"):
        owner.share("shop", "eve", "owner")  # owner is never granted through the API
    with pytest.raises(ProjectError, match="role"):
        owner.share("shop", "eve", "admin")
    with pytest.raises(ProjectError, match="owns"):
        owner.share("shop", "bob", "viewer")
    with pytest.raises(ProjectError, match="owns"):
        owner.revoke("shop", "bob")


def test_an_untrusted_or_ambiguous_e_mail_is_refused(root):
    root.users.seen(Identity("mal", "Mal", "eve2@example.org", email_trusted=False))  # an address from a name claim
    as_(root, BOB).create("Shop")
    with pytest.raises(ProjectError, match="Cannot share with"):
        as_(root, BOB).share("shop", "eve2@example.org", "viewer")
    root.users.seen(Identity("eve-twin", "Eve", "eve@example.org", email_trusted=True))
    with pytest.raises(ProjectError, match="Cannot share with"):  # the same answer as for an unknown user
        as_(root, BOB).share("shop", "eve@example.org", "viewer")


def test_others_see_the_owner_and_their_own_share_only(root):
    as_(root, BOB).create("Shop")
    as_(root, BOB).share("shop", "eve", "viewer")
    as_(root, BOB).share("shop", "zoe", "editor")
    mine = as_(root, EVE).access("shop")
    assert (mine.owner, mine.role, [(s.id, s.role) for s in mine.shares]) == ("bob", "viewer", [("eve", "viewer")])
    full = as_(root, BOB).access("shop")
    assert [(s.id, s.role) for s in full.shares] == [("eve", "viewer"), ("zoe", "editor")]


# ---- HTTP -------------------------------------------------------------------------------------------


def test_sharing_routes(store, monkeypatch):
    users = proxy_users(store, monkeypatch)
    for name in ("bob", "eve", "zoe"):
        users(name, "GET", "/api/me")
    created = users("bob", "POST", "/api/projects", json={"title": "Shop"})
    assert created.status_code == 201 and created.json()["role"] == "owner"
    assert users("eve", "GET", "/api/projects/shop").status_code == 404
    shared = users("bob", "POST", "/api/projects/shop/access", json={"user": "eve@example.org", "role": "viewer"})
    assert shared.status_code == 200
    body = shared.json()
    assert body["owner"] == {"id": "bob", "name": "Bob", "email": "bob@example.org"}
    assert body["shares"] == [{"id": "eve", "name": "Eve", "role": "viewer"}]
    assert body["added"] == {"user": "eve@example.org", "name": "Eve"}  # what bob typed, and the name
    listed = [p for p in users("eve", "GET", "/api/projects").json() if p["shared"]]  # besides her own default project
    assert [(p["id"], p["role"], p["shared"], p["owner"]["name"]) for p in listed] == [("shop", "viewer", True, "Bob")]
    assert users("eve", "POST", "/api/projects/shop/access", json={"user": "zoe", "role": "viewer"}).status_code == 403
    assert users("zoe", "GET", "/api/projects/shop/access").status_code == 404
    assert users("bob", "POST", "/api/projects/shop/access", json={"user": "zoe", "role": "owner"}).status_code == 400
    assert (
        users("bob", "POST", "/api/projects/shop/access", json={"user": "ghost", "role": "viewer"}).status_code == 400
    )
    revoked = users("bob", "DELETE", "/api/projects/shop/access/eve")
    assert revoked.status_code == 200 and revoked.json()["shares"] == []
    assert users("eve", "GET", "/api/projects/shop").status_code == 404
    assert [p["id"] for p in users("eve", "GET", "/api/projects").json()] == [personal_project_id("eve")]
    for removed in (("GET", "/api/workspaces"), ("POST", "/api/projects/shop/workspace")):
        assert users("bob", *removed, json={}).status_code in (404, 405)
