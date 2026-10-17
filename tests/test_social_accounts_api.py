"""POST/PATCH /social_accounts. No tokens stored, no network."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import settings

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "social_account_upsert.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("social_account_upsert", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def drop_account(db):
    created: list[tuple[str, str]] = []

    def mark(platform: str, account_key: str) -> None:
        created.append((platform, account_key))

    yield mark
    for platform, account_key in created:
        db.execute(
            text(
                "DELETE FROM social_accounts WHERE platform = :p AND account_key = :k"
            ),
            {"p": platform, "k": account_key},
        )
    db.commit()


def _body(**extra):
    body = {
        "platform": "facebook",
        "account_key": "moli75",
        "handle": "@box",
        "status": "healthy",
        "auth_kind": "oauth",
    }
    body.update(extra)
    return body


def test_post_creates_and_duplicate_is_409(client, auth_headers, db, drop_account):
    drop_account("facebook", "moli75")
    first = client.post("/social_accounts", json=_body(), headers=auth_headers)
    assert first.status_code == 201, first.text
    row = first.json()
    assert row["platform"] == "facebook"
    assert row["account_key"] == "moli75"
    assert row["handle"] == "@box"
    assert row["status"] == "healthy"
    assert row["auth_kind"] == "oauth"
    assert "token" not in row

    second = client.post("/social_accounts", json=_body(), headers=auth_headers)
    assert second.status_code == 409, second.text

    db.execute(
        text(
            "UPDATE social_accounts SET updated_at = now() - interval '2 hours' "
            "WHERE platform = 'facebook' AND account_key = 'moli75'"
        )
    )
    db.commit()
    listed = client.get("/social_accounts", headers=auth_headers)
    assert listed.status_code == 200
    before = next(r for r in listed.json() if r["account_key"] == "moli75")

    patched = client.patch(
        "/social_accounts/facebook/moli75",
        json={"handle": "@nuevo", "status": "expired"},
        headers=auth_headers,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["handle"] == "@nuevo"
    assert body["status"] == "expired"
    assert body["auth_kind"] == "oauth"
    assert body["updated_at"] > before["updated_at"]


def test_invalid_platform_status_auth_kind_are_422(client, auth_headers):
    headers = auth_headers
    assert client.post("/social_accounts", json=_body(platform="myspace"), headers=headers).status_code == 422
    assert client.post("/social_accounts", json=_body(status="banned"), headers=headers).status_code == 422
    assert client.post("/social_accounts", json=_body(auth_kind="password"), headers=headers).status_code == 422
    assert client.post("/social_accounts", json=_body(account_key="Bad-Key"), headers=headers).status_code == 422
    assert client.patch(
        "/social_accounts/not-a-platform/default",
        json={"status": "healthy"},
        headers=headers,
    ).status_code == 422


@pytest.mark.parametrize("field", ["token", "access_token", "refresh_token", "secret"])
def test_secret_fields_are_422(client, auth_headers, field):
    created = client.post(
        "/social_accounts",
        json=_body(**{field: "nope"}),
        headers=auth_headers,
    )
    assert created.status_code == 422, created.text
    patched = client.patch(
        "/social_accounts/facebook/moli75",
        json={field: "nope"},
        headers=auth_headers,
    )
    assert patched.status_code == 422, patched.text


def test_read_token_cannot_write(client, auth_headers, monkeypatch, drop_account):
    monkeypatch.setattr(settings, "api_read_token", "read-secret-test", raising=False)
    monkeypatch.setattr(settings, "api_write_token", "", raising=False)
    read = {"Authorization": "Bearer read-secret-test"}
    assert client.get("/social_accounts", headers=read).status_code == 200
    assert client.post("/social_accounts", json=_body(), headers=read).status_code == 403
    drop_account("tiktok", "moli75_ro")
    made = client.post(
        "/social_accounts",
        json=_body(platform="tiktok", account_key="moli75_ro", handle="@x"),
        headers=auth_headers,
    )
    assert made.status_code == 201, made.text
    assert client.patch(
        "/social_accounts/tiktok/moli75_ro",
        json={"status": "disabled"},
        headers=read,
    ).status_code == 403


def test_write_token_required_when_configured(client, auth_headers, monkeypatch, drop_account):
    monkeypatch.setattr(settings, "api_write_token", "write-secret-test", raising=False)
    drop_account("instagram", "ig_ops")
    body = _body(platform="instagram", account_key="ig_ops", handle="@ig")
    assert client.post("/social_accounts", json=body, headers=auth_headers).status_code == 403
    write = {"Authorization": "Bearer write-secret-test"}
    created = client.post("/social_accounts", json=body, headers=write)
    assert created.status_code == 201, created.text
    assert client.patch(
        "/social_accounts/instagram/ig_ops",
        json={"status": "disabled"},
        headers=auth_headers,
    ).status_code == 403
    patched = client.patch(
        "/social_accounts/instagram/ig_ops",
        json={"status": "disabled"},
        headers=write,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "disabled"


def test_missing_account_patch_is_404(client, auth_headers):
    r = client.patch(
        "/social_accounts/facebook/does_not_exist",
        json={"handle": "@no"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_upsert_script_creates_and_rejects_bad_platform(db, drop_account):
    mod = _load_script()
    assert mod.main(["--platform", "myspace", "--account-key", "default"]) == 2
    drop_account("tiktok", "default")
    code = mod.main(
        ["--platform", "tiktok", "--account-key", "default", "--handle", "@x", "--status", "healthy"]
    )
    assert code == 0
    row = db.execute(
        text(
            "SELECT handle, status FROM social_accounts "
            "WHERE platform = 'tiktok' AND account_key = 'default'"
        )
    ).one()
    assert row.handle == "@x"
    assert row.status == "healthy"
    db.rollback()
    again = mod.main(
        ["--platform", "tiktok", "--account-key", "default", "--handle", "@y", "--status", "expired"]
    )
    assert again == 0
    row = db.execute(
        text(
            "SELECT handle, status FROM social_accounts "
            "WHERE platform = 'tiktok' AND account_key = 'default'"
        )
    ).one()
    assert row.handle == "@y"
    assert row.status == "expired"
    db.rollback()
    assert mod.main(["--platform", "tiktok", "--account-key", "Bad Key", "--status", "healthy"]) == 2
