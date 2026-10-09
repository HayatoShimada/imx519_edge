from imx519_edge.identity import operator_from


def test_prefers_tailscale_serve_header():
    headers = {"Tailscale-User-Login": "me@example.com", "X-Camera-Client": "100.64.0.9"}
    assert operator_from(headers, whois=lambda ip: "other@example.com") == "me@example.com"


def test_whois_for_camera_entry():
    asked = []

    def whois(ip):
        asked.append(ip)
        return "phone-owner@example.com"

    assert operator_from({"X-Camera-Client": " 100.64.0.9 "}, whois) == "phone-owner@example.com"
    assert asked == ["100.64.0.9"]


def test_ignores_bad_or_missing_client():
    assert operator_from({"X-Camera-Client": "100.64.0.9; rm -rf /"}, whois=lambda ip: "x") is None
    assert operator_from({}, whois=lambda ip: "x") is None
