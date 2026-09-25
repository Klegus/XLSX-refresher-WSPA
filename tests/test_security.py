"""Security regression tests: admin panel login/CSRF, HTML escaping of plan cells,
public API input validation. No MongoDB or network needed."""
import os
import re
from unittest import mock

import pytest

from helpers import load_plans, generate, fixture_path  # noqa: F401  (sets sys.path)

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def admin_client(monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash(PASSWORD))
    monkeypatch.setenv("ADMIN_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("MONGO_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=50")
    import importlib
    import admin_auth
    importlib.reload(admin_auth)
    import admin_app
    importlib.reload(admin_app)
    admin_auth.failed_logins._state.clear()
    # audit writes go to a MagicMock instead of MongoDB
    monkeypatch.setattr(admin_app, "admin_db", mock.MagicMock())
    admin_app.admin_app.config["TESTING"] = True
    return admin_app.admin_app.test_client()


def _csrf(client, path="/login"):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def _login(client, password=PASSWORD):
    token = _csrf(client)
    return client.post("/login", data={"username": "admin", "password": password, "csrf_token": token})


def test_admin_api_requires_login(admin_client):
    assert admin_client.get("/api/session").status_code == 401
    assert admin_client.get("/").status_code == 302


def test_admin_login_and_session(admin_client):
    assert _login(admin_client).status_code == 302
    assert admin_client.get("/api/session").get_json() == {"user": "admin"}


def test_admin_wrong_password_and_lockout(admin_client):
    for _ in range(5):
        assert _login(admin_client, "wrong").status_code == 401
    # locked out even with the right password
    assert _login(admin_client).status_code == 429


def test_admin_post_without_csrf_is_rejected(admin_client):
    _login(admin_client)
    r = admin_client.post("/api/force-check")
    assert r.status_code == 403


def test_admin_login_form_requires_csrf(admin_client):
    admin_client.get("/login")
    r = admin_client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.status_code == 403


def test_admin_panel_fails_closed_without_credentials(monkeypatch, admin_client):
    monkeypatch.delenv("ADMIN_PASSWORD_HASH")
    assert admin_client.get("/login").status_code == 503


def test_admin_security_headers(admin_client):
    r = admin_client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_ack_validation_rejects_operator_object(admin_client):
    _login(admin_client)
    token = admin_client.get("/").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', token).group(1)
    r = admin_client.post("/api/validate/ack", json={"plan_id": {"$ne": ""}}, headers={"X-CSRF-Token": token})
    assert r.status_code == 400


def test_system_config_only_editable_fields():
    from routes.config import _editable_system_config
    assert _editable_system_config({"check_interval": 900}) == {"check_interval": 900}
    for bad in ({"maintenance_mode": True}, {"check_interval": 5}, {"check_interval": "900"}, ["x"]):
        with pytest.raises(ValueError):
            _editable_system_config(bad)


def test_plan_cell_html_is_escaped(tmp_path):
    """Markup typed into an Excel cell must end up as text, not as HTML."""
    import openpyxl
    from html import unescape
    plan = next(p for p in load_plans().values() if p.get('groups') and not p.get('mixed'))
    groups, _ = generate(plan)
    wb = openpyxl.load_workbook(fixture_path(plan))
    ws = wb[plan['sheet_name']]
    # replace the first lesson text found in the generated plan with a payload
    lesson = next(t for html in groups.values() for t in re.findall(r'<td>([^<]{12,})</td>', html))
    first_line = unescape(lesson.split('\n')[0].strip())
    payload = '<img src=x onerror=alert(1)>'
    hit = False
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and first_line and first_line in c.value:
                c.value = payload + ' ' + c.value
                hit = True
    assert hit, "fixture cell not found"
    path = tmp_path / "xss.xlsx"
    wb.save(path)
    plan = dict(plan, download_url='fixture://' + str(path))
    from LessonPlan import LessonPlan
    lp = LessonPlan.__new__(LessonPlan)
    lp.plan_config, lp.sheet_name, lp.last_notes = plan, plan['sheet_name'], None
    lp.groups = {k: (v['identifier'] if isinstance(v, dict) else v) for k, v in plan['groups'].items()}
    html = ''.join(lp._generate_groups_html(str(path)).values())
    assert '<img' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html


def test_changes_recorded_on_main_parser_path():
    """The sheet generator path stores a structured diff in plan_changes (it used to skip it)."""
    from LessonPlan import LessonPlan
    old = ("<table border='1'><tr><th><b>Godziny</b></th><th><b>Poniedziałek</b></th></tr>"
           "<tr><td>8<sup>00</sup> - 9<sup>30</sup></td><td>Matematyka wykład s. 101</td></tr></table>")
    new = old.replace("s. 101", "s. 202")
    lp = LessonPlan.__new__(LessonPlan)
    lp.plan_config = {"name": "Test plan"}
    lp.db = mock.MagicMock()
    collection = mock.MagicMock()
    collection.find_one.return_value = {"groups": {"g1": old}, "checksum": "old"}
    lp._record_changes(collection, "plans_test", {"g1": new}, "new")
    lp.db.plan_changes.insert_one.assert_called_once()
    saved = lp.db.plan_changes.insert_one.call_args[0][0]
    assert saved["collection"] == "plans_test" and saved["change_count"] >= 1


def test_downloads_only_from_allowed_hosts():
    from shared_utils import check_download_url, UnsafeDownload
    check_download_url("https://puw.wspa.pl/pluginfile.php/1/plan.xlsx")
    for bad in ("http://puw.wspa.pl/x.xlsx", "https://169.254.169.254/latest/meta-data",
                "https://mongodb:27017/", "https://puw.wspa.pl.evil.com/x.xlsx", "file:///etc/passwd"):
        with pytest.raises(UnsafeDownload):
            check_download_url(bad)


def test_fetch_rejects_redirect_to_other_host_and_non_xlsx():
    from shared_utils import fetch_bytes, UnsafeDownload

    class Resp:
        def __init__(self, status=200, headers=None, body=b""):
            self.status_code, self.headers, self._body = status, headers or {}, body
            self.is_redirect = status in (301, 302, 303, 307, 308)
        def raise_for_status(self):
            pass
        def iter_content(self, n):
            yield self._body
        def close(self):
            pass

    session = mock.MagicMock()
    session.get.return_value = Resp(302, {"Location": "https://internal.local/secret"})
    with pytest.raises(UnsafeDownload):
        fetch_bytes(session, "https://puw.wspa.pl/mod/resource/view.php?id=1")
    session.get.return_value = Resp(200, body=b"<html>login page</html>")
    with pytest.raises(UnsafeDownload):
        fetch_bytes(session, "https://puw.wspa.pl/plan.xlsx", require_xlsx=True)
    session.get.return_value = Resp(200, body=b"PK\x03\x04rest")
    assert fetch_bytes(session, "https://puw.wspa.pl/plan.xlsx", require_xlsx=True).startswith(b"PK")


def test_weekend_onsite_plan_gets_online_companion():
    from routes.plans import companion_pairs, natural_key
    base = "Pielęgniarstwo - studia I stopnia - st I - semestr 1 - zimowy (weekendowe)"
    db = mock.MagicMock()
    db.plans_config.find_one.return_value = {"plans": {
        "a": {"name": base + " - zajęcia w siedzibie", "faculty": "Pielęgniarstwo"},
        "b": {"name": base + " - zajęcia on-line", "faculty": "Pielęgniarstwo"},
        "c": {"name": "Informatyka - studia I stopnia - st III - semestr zimowy", "faculty": "Informatyka"},
    }}
    pairs = companion_pairs(db)
    assert len(pairs) == 1
    onsite, online = next(iter(pairs.items()))
    assert onsite.endswith("zajęcia_w_siedzibie") and online.endswith("zajęcia_on-line")
    db.plans_config.find_one.return_value = {"plans": {
        "x": {"name": "Informatyka - studia I stopnia - nst III - semestr zimowy - INF nst III - z stacjonarne", "faculty": "Informatyka"},
        "y": {"name": "Informatyka - studia I stopnia - nst III - semestr zimowy - INF nst III - z on-line", "faculty": "Informatyka"},
    }}
    assert len(companion_pairs(db)) == 1
    groups = ["Grupa 10 x", "Grupa 2 y", "Grupa 1 z"]
    assert sorted(groups, key=natural_key) == ["Grupa 1 z", "Grupa 2 y", "Grupa 10 x"]


ZJAZDY_TEXT = """Szczegółowa organizacja roku akademickiego 2026/2027 - studia niestacjonarne
PIĄTEK SOBOTA NIEDZIELA NR ZJAZDU
2026-10-02 2026-10-03 2026-10-04 1
2026-10-16 2026-10-17 2026-10-18 2
PIĄTEK SOBOTA NIEDZIELA NR ZJAZDU
2027-03-05 2027-03-06 2027-03-07 1
"""


def test_meeting_calendar_parsed_and_matched_to_plans():
    from zjazdy import parse_calendar_text, calendar_for
    seasons = parse_calendar_text(ZJAZDY_TEXT)
    assert seasons['zimowy']['2'] == ['2026-10-16', '2026-10-17', '2026-10-18']
    assert seasons['letni']['1'][0] == '2027-03-05'
    weekend = {'1': ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']}
    cals = [
        {'course': 'Strefa studenta - kierunek Informatyka - studia I stopnia', 'title': 'NST - organizacja roku akademickiego 2026-2027', 'seasons': seasons},
        {'course': 'Strefa studenta - kierunek Pielęgniarstwo', 'title': 'PIE ST - sem. 1 - organizacja roku akademickiego 2026-2027', 'seasons': {'zimowy': {'1': ['2026-10-05']}}},
        {'course': 'Strefa studenta - kierunek Pielęgniarstwo', 'title': 'PIE ST - sem. 1 weekendowy - organizacja roku akademickiego 2026-2027', 'seasons': {'zimowy': weekend}},
    ]
    assert calendar_for('Informatyka - studia I stopnia - nst III - semestr zimowy - INF nst II', cals) == seasons['zimowy']
    assert calendar_for('Pielęgniarstwo - studia I stopnia - st I - semestr 1 - zimowy (weekendowe) - zajęcia w siedzibie', cals) == weekend
    assert calendar_for('Pielęgniarstwo - studia I stopnia - st I - semestr 1 - zimowy (dzienne) - PIE ST I', cals) == {'1': ['2026-10-05']}
    # e-learning studies publish no calendar; other degree does not match
    assert calendar_for('Informatyka - studia I stopnia - nst puw II - semestr zimowy - INF II', cals) is None
    assert calendar_for('Informatyka - studia II stopnia - nst I - semestr zimowy - INF MUZ', cals) is None


def test_meeting_calendar_from_sheet_header():
    from zjazdy import parse_header_calendar
    text = ("ZARZĄDZANIE III SEMESTR rok akademicki 2026/2027\nzjazdy stacjonarne (w siedzibie Uczelni)\n\n"
            "• zjazd nr 1: 16 października 2026 r. – 18 października 2026 r. \n"
            "• zjazd nr 2:  4 grudnia 2026 r. – 6 grudnia 2026 r.\n"
            "zjazd nr 3: 31 grudnia 2026 r. - 2 stycznia 2027 r.")
    cal = parse_header_calendar(text)
    assert cal['1'] == ['2026-10-16', '2026-10-17', '2026-10-18']
    assert cal['2'] == ['2026-12-04', '2026-12-05', '2026-12-06']
    assert cal['3'] == ['2026-12-31', '2027-01-01', '2027-01-02']
    assert parse_header_calendar('INFORMATYKA V SEMESTR zjazdy on-line') is None


def test_every_elearning_fixture_with_dates_in_header_is_parsed():
    import openpyxl
    from zjazdy import parse_header_calendar
    for cfg in load_plans().values():
        if 'puw' not in cfg['name'].lower():
            continue
        header = openpyxl.load_workbook(fixture_path(cfg), read_only=True)[cfg['sheet_name']]['A1'].value or ''
        if 'zjazd nr' in header:
            assert parse_header_calendar(header), cfg['sheet_name']
