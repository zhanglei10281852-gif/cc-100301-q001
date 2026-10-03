from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path):
    os.environ["DEEP_TRAVEL_DATABASE_PATH"] = str(tmp_path / "test.db")
    from app.database import close_connection
    close_connection()
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client
    close_connection()


@pytest.fixture()
def admin(client: TestClient) -> dict:
    response = client.post("/api/auth/bootstrap", json={"username": "admin", "password": "Admin!23456", "client_label": "tests"})
    assert response.status_code == 201, response.text
    login = client.post("/api/auth/login", json={"username": "admin", "password": "Admin!23456", "client_label": "tests"})
    assert login.status_code == 200, login.text
    return {"token": login.json()["token"], "headers": {"Authorization": f"Bearer {login.json()['token']}"}}
