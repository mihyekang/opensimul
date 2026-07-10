"""SecurityHeadersMiddleware — 모든 응답에 보안 헤더가 포함되는지 검증."""
import pytest


REQUIRED_HEADERS = [
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
]


class TestSecurityHeaders:
    def test_index_has_security_headers(self, client):
        resp = client.get("/", follow_redirects=False)
        for header, value in REQUIRED_HEADERS:
            assert header in resp.headers, f"응답에 {header} 헤더 없음"
            assert resp.headers[header] == value, (
                f"{header}: 기대={value!r}, 실제={resp.headers[header]!r}"
            )

    def test_csp_header_present(self, client):
        resp = client.get("/", follow_redirects=False)
        assert "content-security-policy" in resp.headers
        csp = resp.headers["content-security-policy"]
        assert "default-src" in csp
        assert "frame-ancestors 'none'" in csp

    def test_permissions_policy_present(self, client):
        resp = client.get("/", follow_redirects=False)
        assert "permissions-policy" in resp.headers

    def test_api_endpoint_has_security_headers(self, authed_client):
        """API 엔드포인트도 동일한 보안 헤더를 반환해야 함."""
        resp = authed_client.get("/grocery/history")
        for header, value in REQUIRED_HEADERS:
            assert header in resp.headers, f"API 응답에 {header} 헤더 없음"
