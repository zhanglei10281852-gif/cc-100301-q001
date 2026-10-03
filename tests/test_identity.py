from __future__ import annotations


def test_bootstrap_login_and_me(client, admin):
    response = client.get("/api/auth/me", headers=admin["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert "users.write" in body["permissions"]


def test_bootstrap_is_single_use(client, admin):
    response = client.post("/api/auth/bootstrap", json={"username": "second", "password": "Admin!23456", "client_label": "tests"})
    assert response.status_code == 409


def test_user_and_role_lifecycle(client, admin):
    permissions = client.get("/api/roles/permissions", headers=admin["headers"])
    assert permissions.status_code == 200
    role = client.post(
        "/api/roles",
        headers=admin["headers"],
        json={"code": "catalog.reader", "name": "目录查看员", "permission_codes": ["catalog.read", "feedback.read"]},
    )
    assert role.status_code == 201, role.text
    user = client.post(
        "/api/users",
        headers=admin["headers"],
        json={"username": "clerk.one", "password": "Clerk!23456", "display_name": "运营员甲", "role_codes": ["catalog.reader"]},
    )
    assert user.status_code == 201, user.text
    assert user.json()["roles"][0]["code"] == "catalog.reader"
    login = client.post("/api/auth/login", json={"username": "clerk.one", "password": "Clerk!23456", "client_label": "tests"})
    assert login.status_code == 200
    assert sorted(login.json()["permissions"]) == ["catalog.read", "feedback.read"]


def test_disabled_user_sessions_are_revoked(client, admin):
    created = client.post(
        "/api/users",
        headers=admin["headers"],
        json={"username": "disable.me", "password": "Clerk!23456", "display_name": "待停用人员", "role_codes": ["operator"]},
    ).json()
    login = client.post("/api/auth/login", json={"username": "disable.me", "password": "Clerk!23456", "client_label": "tests"})
    token = login.json()["token"]
    changed = client.patch(f"/api/users/{created['id']}", headers=admin["headers"], json={"status": "disabled"})
    assert changed.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


def test_audit_redacts_secrets(client, admin):
    client.post(
        "/api/users",
        headers=admin["headers"],
        json={"username": "audit.user", "password": "Audit!23456", "display_name": "审计用户", "role_codes": []},
    )
    events = client.get("/api/audit?action=user.create", headers=admin["headers"])
    assert events.status_code == 200
    assert events.json()["total"] == 1
    serialized = str(events.json())
    assert "Audit!23456" not in serialized
    assert "password_hash" not in serialized
