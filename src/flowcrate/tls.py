"""TLS certificate generation for the HTTPS callback listener.

Generates a self-signed certificate under LOCAL_STATE_DIR (respects
FLOWCRATE_STATE_DIR) using the ``cryptography`` library. The cert covers
localhost, the local mDNS hostname (e.g. mymac.local), 127.0.0.1, and the
machine's primary LAN IP (if discoverable), with a ~10-year validity period.
"""

import datetime
import ipaddress
import logging

from . import paths as _paths

_VALIDITY_DAYS = 3650  # ~10 years


def ensure_cert(hostname: str, local_ip: str | None = None) -> tuple[str, str]:
    """Return (cert_path, key_path), generating files if missing or expired.

    Args:
        hostname: Local mDNS name (e.g. ``mymac.local``) to add as a SAN.
        local_ip: Primary LAN IP to add as an IP SAN (optional).

    Returns:
        Tuple of string paths ``(cert_path, key_path)``.

    Raises:
        Exception: If the ``cryptography`` package is not installed or cert
            generation fails for any reason.
    """
    # Read LOCAL_STATE_DIR at call time so FLOWCRATE_STATE_DIR env overrides work.
    cert_path = _paths.LOCAL_STATE_DIR / "flowcrate-cert.pem"
    key_path = _paths.LOCAL_STATE_DIR / "flowcrate-key.pem"

    _paths.ensure_dirs()

    if cert_path.exists() and key_path.exists():
        if not _cert_expired(cert_path):
            return str(cert_path), str(key_path)
        logging.info("TLS: existing cert is expired, regenerating.")

    _generate_cert(cert_path, key_path, hostname, local_ip)
    return str(cert_path), str(key_path)


def _cert_expired(cert_path) -> bool:
    try:
        from cryptography import x509  # noqa: PLC0415

        pem = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(pem)
        return cert.not_valid_after_utc < datetime.datetime.now(datetime.timezone.utc)
    except Exception as exc:
        logging.warning("TLS: could not check cert expiry (%s), will regenerate.", exc)
        return True


def _generate_cert(cert_path, key_path, hostname: str, local_ip: str | None) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    logging.info("TLS: generating self-signed cert for %s ...", hostname)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, hostname)]
    )

    now = datetime.datetime.now(datetime.timezone.utc)

    # Build SubjectAltNames
    san_dns = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
    ]
    san_ip = [x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]
    if local_ip:
        try:
            san_ip.append(x509.IPAddress(ipaddress.IPv4Address(local_ip)))
        except ValueError:
            pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(san_dns + san_ip),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # Restrict private-key permissions on POSIX systems.
    try:
        import os

        os.chmod(key_path, 0o600)
    except Exception:
        pass
    logging.info("TLS: cert written to %s", cert_path)
