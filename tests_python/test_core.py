import base64
import json
import time
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from httpx import ASGITransport, AsyncClient
from vps_one.main import app, encrypted_access, instance_access, instance_card, instance_mail_text
from vps_one.models import Instance
from vps_one.security import decrypt, encrypt, hash_password, verify_password
from datetime import datetime, timezone
from vps_one.services.clicd import CLICD, CLICDError, container_details, container_items, container_status, expiration_date, extract_access, plan_payload, reset_password_value
from vps_one.services.hashpay import HashPay


def test_security_roundtrip():
    password_hash = hash_password("StrongPassword123")
    assert verify_password(password_hash, "StrongPassword123")
    assert not verify_password(password_hash, "wrong")
    assert decrypt(encrypt("secret-value")) == "secret-value"


def test_instance_credentials_are_encrypted_at_rest():
    credentials = {"username": "user-test", "password": "initial-secret", "access_code": "code-1"}
    encrypted = encrypted_access(credentials)
    assert "initial-secret" not in encrypted
    instance = Instance(user_id=1, order_id=1, plan_id=1, name="test", access_json=encrypted)
    assert instance_access(instance) == credentials
    instance.access_json = '{"legacy": true}'
    assert instance_access(instance) == {}


def test_clicd_status_and_sub_user_contract():
    response = {"success": True, "data": {"container": {"id": "ct-1", "state": "online"}, "sub_user": {"username": "user-d7db054c", "initial_password": "temporary-secret", "access_code": "a877d569", "login_url": "http://192.0.2.10:8999/login?code=a877d569"}}}
    assert container_status(response["data"]["container"]) == "running"
    assert container_status({"data": {"power_status": "offline"}}) == "stopped"
    assert extract_access(response) == {"username": "user-d7db054c", "password": "temporary-secret", "access_code": "a877d569", "management_url": "http://192.0.2.10:8999/login?code=a877d569"}


def test_real_clicd_container_contract():
    response = {"success": True, "data": [{"id": 27, "uuid": "d25b9ba6", "name": "KVM-S-1", "ip": "192.168.122.85", "public_ipv4s": [{"address": "192.151.158.3"}], "ipv6": "2001:db8::1", "ssh_port": 0, "ssh_password": "secret", "status": "running", "template": "kvm-debian-bookworm"}]}
    item = container_items(response)[0]
    details = container_details(item)
    assert details == {"id": "d25b9ba6", "name": "KVM-S-1", "status": "running", "ip": "192.151.158.3", "ipv6": "2001:db8::1", "ssh_port": 22, "ssh_password": "secret", "operating_system": "kvm-debian-bookworm"}


def test_reset_password_contract():
    assert reset_password_value({"success": True, "data": {"password": "NewPass123456"}}) == "NewPass123456"
    with pytest.raises(CLICDError):
        reset_password_value({"success": False, "message": "failed"})
    with pytest.raises(CLICDError):
        reset_password_value({"success": True, "data": {}})


@pytest.mark.asyncio
async def test_sub_user_and_reset_request_contract(monkeypatch):
    calls = []
    async def request(self, method, path, data=None, params=None):
        calls.append((method, path, data))
        if path == "/sub-user/create":
            return {"success": True, "data": {"username": "user-1", "password": "initial", "access_code": "code-1"}}
        return {"success": True, "data": {"password": data["password"]}}
    monkeypatch.setattr(CLICD, "request", request)
    client = CLICD("https://panel.example.com", "token")
    access = await client.create_sub_user("example-vm")
    assert access["management_url"] == "https://panel.example.com/login?code=code-1"
    assert await client.reset_password("ct-1", "NewPass123456") == "NewPass123456"
    assert calls == [("POST", "/sub-user/create", {"container_name": "example-vm"}), ("POST", "/containers/ct-1/reset-password", {"password": "NewPass123456"})]


def test_clicd_payload_contract():
    class Plan:
        virtualization = "lxc"; clicd_image = "debian-bookworm"; cpu = 2; memory_mb = 2048; disk_gb = 40
        assign_nat = True; port_mapping_count = 2; assign_ipv4 = False; ipv4_count = 0
        assign_ipv6 = True; ipv6_count = 1; network_down_mbps = 200; network_up_mbps = 100
        io_read_mbps = 120; io_write_mbps = 80; traffic_gb = 1000
    payload = plan_payload(Plan(), "VP123", "2097-01-01T00:00:00Z")
    assert payload["vcpu"] == 2
    assert payload["template_id"] == "debian-bookworm"
    assert payload["assign_nat"] is True
    assert payload["network_up_mbps"] == 100
    assert payload["ssh_password"] == ""
    assert payload["ssh_public_key"] == ""
    assert payload["expires_at"] == "2097-01-01"
    assert "monthly_traffic_gb" not in payload


def test_expiration_date_contract():
    assert expiration_date(datetime(2098, 2, 3, 4, 5, tzinfo=timezone.utc)) == "2098-02-03"
    assert expiration_date("2098-02-03T04:05:06+08:00") == "2098-02-03"
    with pytest.raises(CLICDError):
        expiration_date("not-a-date")
    with pytest.raises(CLICDError):
        expiration_date("2020-01-01")


@pytest.mark.asyncio
async def test_hashpay_create_order_contract(monkeypatch):
    calls = []
    async def request(self, method, path, payload=None):
        calls.append((method, path, payload))
        return {"data": {"orderId": "hp-1", "payUrl": "https://pay.example.com/order/hp-1"}}
    monkeypatch.setattr(HashPay, "request", request)
    result = await HashPay("https://hashpay.example.com", "merchant", "private-key").create({"merchantNo": "VP1", "amount": "19.99"})
    assert result["data"]["payUrl"] == "https://pay.example.com/order/hp-1"
    assert calls == [("POST", "/api/merchant/new", {"merchantNo": "VP1", "amount": "19.99"})]


def test_hashpay_encrypted_callback():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    aes_key = AESGCM.generate_key(bit_length=256)
    iv = b"123456789012"
    message = json.dumps({"timestamp": int(time.time()), "payload": {"merchantNo": "VP1", "amount": 10, "status": "paid"}}).encode()
    encrypted = AESGCM(aes_key).encrypt(iv, message, None)
    wrapped = private.public_key().encrypt(aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    envelope = {"alg": "RSA-OAEP-256+A256GCM", "key": base64.b64encode(wrapped).decode(), "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(encrypted).decode()}
    assert HashPay("", "", pem).decrypt_callback(envelope)["merchantNo"] == "VP1"


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
