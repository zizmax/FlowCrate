"""Tests for the new Spotify web-OAuth flow and SpotifyAuthRequired behavior."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class SpotifyManagerAuthRequiredTests(unittest.TestCase):
    """SpotifyManager must raise SpotifyAuthRequired when there is no cached token."""

    def _make_oauth_stub(self, cached_token=None, validated_token=None):
        """Return a SpotifyOAuth mock that simulates the cache-handler API."""
        handler = MagicMock()
        handler.get_cached_token.return_value = cached_token

        oauth = MagicMock()
        oauth.cache_handler = handler
        oauth.validate_token.return_value = validated_token
        return oauth

    def test_raises_when_no_cached_token(self):
        from flowcrate.spotify import SpotifyAuthRequired, SpotifyManager

        oauth_stub = self._make_oauth_stub(cached_token=None, validated_token=None)

        with patch("flowcrate.spotify.ensure_dirs"), \
             patch("flowcrate.spotify.load_config", return_value=MagicMock(
                 spotify_client_id="id", spotify_client_secret="secret",
                 spotify_redirect_uri="https://localhost:8443/callback",
             )), \
             patch("flowcrate.spotify.SpotifyOAuth", return_value=oauth_stub):
            with self.assertRaises(SpotifyAuthRequired):
                SpotifyManager()

    def test_raises_when_cached_token_is_expired_and_no_refresh(self):
        from flowcrate.spotify import SpotifyAuthRequired, SpotifyManager

        # get_cached_token returns something, but validate_token returns None
        # (expired with no refresh token).
        oauth_stub = self._make_oauth_stub(
            cached_token={"access_token": "old", "refresh_token": None},
            validated_token=None,
        )

        with patch("flowcrate.spotify.ensure_dirs"), \
             patch("flowcrate.spotify.load_config", return_value=MagicMock(
                 spotify_client_id="id", spotify_client_secret="secret",
                 spotify_redirect_uri="https://localhost:8443/callback",
             )), \
             patch("flowcrate.spotify.SpotifyOAuth", return_value=oauth_stub):
            with self.assertRaises(SpotifyAuthRequired):
                SpotifyManager()

    def test_proceeds_when_valid_token_present(self):
        from flowcrate.spotify import SpotifyManager

        valid_token = {"access_token": "fresh", "token_type": "Bearer"}
        oauth_stub = self._make_oauth_stub(
            cached_token=valid_token,
            validated_token=valid_token,
        )
        sp_mock = MagicMock()
        sp_mock.current_user.return_value = {
            "id": "user123",
            "display_name": "Test User",
        }

        with patch("flowcrate.spotify.ensure_dirs"), \
             patch("flowcrate.spotify.load_config", return_value=MagicMock(
                 spotify_client_id="id", spotify_client_secret="secret",
                 spotify_redirect_uri="https://localhost:8443/callback",
             )), \
             patch("flowcrate.spotify.SpotifyOAuth", return_value=oauth_stub), \
             patch("flowcrate.spotify.spotipy.Spotify", return_value=sp_mock):
            mgr = SpotifyManager()
        self.assertEqual(mgr.user_id, "user123")
        self.assertEqual(mgr.display_name, "Test User")

    def test_no_socket_bind_on_missing_token(self):
        """Constructing SpotifyManager with no token must not attempt to bind a socket."""
        import socket
        from flowcrate.spotify import SpotifyAuthRequired, SpotifyManager

        original_bind = socket.socket.bind
        bind_called = []

        def spy_bind(self, *args, **kwargs):
            bind_called.append(args)
            return original_bind(self, *args, **kwargs)

        oauth_stub = self._make_oauth_stub(cached_token=None, validated_token=None)

        with patch("flowcrate.spotify.ensure_dirs"), \
             patch("flowcrate.spotify.load_config", return_value=MagicMock(
                 spotify_client_id="id", spotify_client_secret="secret",
                 spotify_redirect_uri="https://localhost:8443/callback",
             )), \
             patch("flowcrate.spotify.SpotifyOAuth", return_value=oauth_stub), \
             patch.object(socket.socket, "bind", spy_bind):
            with self.assertRaises(SpotifyAuthRequired):
                SpotifyManager()

        self.assertEqual(bind_called, [], "socket.bind must not be called when no token is cached")


class EnsureCertTests(unittest.TestCase):
    """tls.ensure_cert() must produce a readable, valid certificate."""

    def test_generates_cert_and_key(self):
        import flowcrate.paths as paths_mod
        import flowcrate.tls as tls_mod

        with tempfile.TemporaryDirectory() as d:
            temp_dir = Path(d)
            original_state_dir = paths_mod.LOCAL_STATE_DIR
            original_logs_dir = paths_mod.LOGS_DIR
            paths_mod.LOCAL_STATE_DIR = temp_dir
            paths_mod.LOGS_DIR = temp_dir / "logs"

            try:
                cert_path, key_path = tls_mod.ensure_cert(
                    hostname="localhost.local", local_ip="127.0.0.1"
                )

                self.assertTrue(Path(cert_path).exists(), "cert file must exist")
                self.assertTrue(Path(key_path).exists(), "key file must exist")

                # Verify the cert is parseable and has SANs.
                from cryptography import x509
                pem = Path(cert_path).read_bytes()
                cert = x509.load_pem_x509_certificate(pem)
                san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                dns_names = san.value.get_values_for_type(x509.DNSName)
                self.assertIn("localhost", dns_names)
                self.assertIn("localhost.local", dns_names)
            finally:
                paths_mod.LOCAL_STATE_DIR = original_state_dir
                paths_mod.LOGS_DIR = original_logs_dir

    def test_returns_same_cert_if_not_expired(self):
        """ensure_cert returns cached paths without regenerating if valid."""
        import flowcrate.paths as paths_mod
        import flowcrate.tls as tls_mod

        with tempfile.TemporaryDirectory() as d:
            temp_dir = Path(d)
            original_state_dir = paths_mod.LOCAL_STATE_DIR
            original_logs_dir = paths_mod.LOGS_DIR
            paths_mod.LOCAL_STATE_DIR = temp_dir
            paths_mod.LOGS_DIR = temp_dir / "logs"

            try:
                cert1, key1 = tls_mod.ensure_cert("localhost.local")
                mtime1 = Path(cert1).stat().st_mtime

                cert2, key2 = tls_mod.ensure_cert("localhost.local")
                mtime2 = Path(cert2).stat().st_mtime

                self.assertEqual(cert1, cert2)
                self.assertEqual(mtime1, mtime2, "cert must not be regenerated when still valid")
            finally:
                paths_mod.LOCAL_STATE_DIR = original_state_dir
                paths_mod.LOGS_DIR = original_logs_dir


class OAuthRouteTests(unittest.TestCase):
    """Web OAuth routes /spotify/login and /callback."""

    def _client(self):
        from flowcrate.app import create_app
        app = create_app()
        app.config.update(TESTING=True, HTTP_PORT=8765, HTTPS_PORT=8443)
        return app.test_client()

    def test_login_redirects_to_spotify(self):
        from flowcrate.config import AppConfig
        cfg = AppConfig(
            spotify_client_id="test_client_id",
            spotify_client_secret="test_secret",
            spotify_redirect_uri="https://localhost:8443/callback",
        )
        with patch("flowcrate.app.load_config", return_value=cfg):
            resp = self._client().get("/spotify/login")
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["Location"]
        self.assertIn("accounts.spotify.com", location)
        self.assertIn("state=", location)

    def test_login_redirects_to_settings_if_no_credentials(self):
        from flowcrate.config import AppConfig
        cfg = AppConfig()  # no client id/secret
        with patch("flowcrate.app.load_config", return_value=cfg):
            resp = self._client().get("/spotify/login")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/settings", resp.headers["Location"])

    def test_callback_invalid_state_redirects_to_settings(self):
        resp = self._client().get("/callback?code=abc&state=invalid_state_xyz")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/settings", resp.headers["Location"])

    def test_callback_error_param_redirects_to_settings(self):
        resp = self._client().get("/callback?error=access_denied&state=whatever")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/settings", resp.headers["Location"])

    def test_callback_missing_code_redirects_to_settings(self):
        """Valid state but no code param should redirect with error."""
        from flowcrate.app import _oauth_state_add
        import secrets
        state = secrets.token_urlsafe(24)
        _oauth_state_add(state)
        resp = self._client().get(f"/callback?state={state}")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/settings", resp.headers["Location"])


if __name__ == "__main__":
    unittest.main()
