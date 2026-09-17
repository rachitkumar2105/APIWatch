class TestRegister:
    def test_register_returns_201_with_user_body(self, client):
        response = client.post(
            "/auth/register", json={"username": "newuser", "password": "a-strong-password"}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["username"] == "newuser"
        assert "id" in body
        assert "password" not in body
        assert "hashed_password" not in body

    def test_register_duplicate_username_returns_409(self, client):
        client.post(
            "/auth/register", json={"username": "dupeuser", "password": "a-strong-password"}
        )
        response = client.post(
            "/auth/register", json={"username": "dupeuser", "password": "different-pw-1"}
        )
        assert response.status_code == 409

    def test_register_short_password_returns_422(self, client):
        response = client.post("/auth/register", json={"username": "shortpw", "password": "short"})
        assert response.status_code == 422

    def test_register_short_username_returns_422(self, client):
        response = client.post(
            "/auth/register", json={"username": "ab", "password": "a-strong-password"}
        )
        assert response.status_code == 422

    def test_register_race_condition_on_duplicate_username_returns_409(self, client, monkeypatch):
        """Two concurrent registrations for the same username could both pass the
        initial existence check before either commits. The DB's unique constraint
        is the real safety net — this confirms the IntegrityError it raises is
        translated into a clean 409, not a raw 500."""
        from sqlalchemy.exc import IntegrityError

        from app import crud

        def raise_integrity_error(*args, **kwargs):
            raise IntegrityError("statement", "params", Exception("UNIQUE constraint failed"))

        monkeypatch.setattr(crud, "create_user", raise_integrity_error)
        response = client.post(
            "/auth/register", json={"username": "raceduser", "password": "a-strong-password"}
        )
        assert response.status_code == 409


class TestLogin:
    def test_login_with_correct_credentials_returns_token(self, client):
        client.post(
            "/auth/register", json={"username": "loginuser", "password": "correct-password-1"}
        )
        response = client.post(
            "/auth/login", data={"username": "loginuser", "password": "correct-password-1"}
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_login_with_wrong_password_returns_401(self, client):
        client.post(
            "/auth/register", json={"username": "wrongpwuser", "password": "correct-password-1"}
        )
        response = client.post(
            "/auth/login", data={"username": "wrongpwuser", "password": "totally-wrong-password"}
        )
        assert response.status_code == 401

    def test_login_with_nonexistent_user_returns_401(self, client):
        response = client.post(
            "/auth/login", data={"username": "ghost-user", "password": "whatever-password"}
        )
        assert response.status_code == 401

    def test_wrong_password_and_nonexistent_user_get_identical_error(self, client):
        """Prevents username enumeration: both failure modes must look the same."""
        client.post(
            "/auth/register", json={"username": "realuser", "password": "correct-password-1"}
        )
        wrong_pw = client.post(
            "/auth/login", data={"username": "realuser", "password": "wrong-password-999"}
        )
        no_user = client.post(
            "/auth/login", data={"username": "does-not-exist", "password": "wrong-password-999"}
        )
        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json()["detail"] == no_user.json()["detail"]


class TestProtectedRouteAuth:
    def test_invalid_token_returns_401(self, client):
        client.headers.update({"Authorization": "Bearer this-is-not-a-real-token"})
        response = client.get("/tasks")
        assert response.status_code == 401

    def test_missing_token_returns_401(self, client):
        response = client.get("/tasks")
        assert response.status_code == 401

    def test_token_for_deleted_user_returns_401(self, client, monkeypatch):
        client.post(
            "/auth/register", json={"username": "willbedeleted", "password": "a-strong-password"}
        )
        client.post(
            "/auth/login", data={"username": "willbedeleted", "password": "a-strong-password"}
        )

        # Simulate the user no longer existing by forging a token for a user id that
        # was never actually created.
        from app import auth as auth_module

        forged_token = auth_module.create_access_token(subject="999999")
        client.headers.update({"Authorization": f"Bearer {forged_token}"})
        response = client.get("/tasks")
        assert response.status_code == 401
