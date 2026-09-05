"""
Security tests for Budget Buddy.

    python tests/test_security.py

Unlike the other suites this one leaves CSRF ON, because the whole point
is to prove a request without a token is refused.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as bb

bb.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
bb.app.config["TESTING"] = True
bb.app.config["WTF_CSRF_ENABLED"] = True
del bb.app.extensions["sqlalchemy"]
bb.db.init_app(bb.app)
#TESTING keeps send_email off the network entirely
for _v in ("DEV_ADMIN_PASSWORD", "DEV_ADMIN_EMAIL", "DEV_ADMIN_USER"):
    os.environ.pop(_v, None)


def check(name):
    print(f"  ok  {name}")


def token(client, page="/"):
    """ Pull the csrf token out of a rendered page, like a browser would """
    html = client.get(page).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def signed_in():
    with bb.app.app_context():
        bb.db.drop_all()
        bb.db.create_all()
    bb.limiter.enabled = False
    c = bb.app.test_client()
    t = re.search(r'name="csrf_token" value="([^"]+)"',
                  c.get("/register").get_data(as_text=True)).group(1)
    c.post("/register", data={"username": "tester", "email": "t@test.local",
                              "password": "pw12345", "confirm": "pw12345",
                              "csrf_token": t}, follow_redirects=True)
    return c


def a_bill(client):
    client.post("/add", data={"name": "Wifi", "description": "", "amount": "500",
                              "due_day": "5", "bill_type": "fixed",
                              "frequency": "monthly",
                              "csrf_token": token(client, "/add")},
                follow_redirects=True)
    with bb.app.app_context():
        return bb.Payment.query.filter_by(name="Wifi").first().id


def test_data_cannot_change_on_a_get():
    c = signed_in()
    bill = a_bill(c)
    #a prefetch, crawler or <img src> can only ever issue a GET
    for url in (f"/pay/{bill}", f"/unpaid/{bill}", f"/delete/{bill}",
                f"/reminders/read/1"):
        assert c.get(url).status_code == 405, f"GET {url} should be refused"
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        assert p is not None, "a GET must never delete a bill"
        assert not p.is_paid, "a GET must never mark a bill paid"
    check("GET on pay / unpaid / delete / read is refused, data untouched")


def test_a_form_without_a_token_is_refused():
    c = signed_in()
    bill = a_bill(c)
    #exactly what another website's form would send: no token
    r = c.post(f"/delete/{bill}")
    assert r.status_code == 400, f"expected 400, got {r.status_code}"
    with bb.app.app_context():
        assert bb.db.session.get(bb.Payment, bill) is not None, \
            "a request with no csrf token must not delete anything"
    check("a POST with no csrf token is rejected and changes nothing")

    r = c.post(f"/delete/{bill}", data={"csrf_token": "not-a-real-token"})
    assert r.status_code == 400
    with bb.app.app_context():
        assert bb.db.session.get(bb.Payment, bill) is not None
    check("a POST with a forged token is rejected too")


def test_the_real_form_still_works():
    c = signed_in()
    bill = a_bill(c)
    r = c.post(f"/delete/{bill}", data={"csrf_token": token(c)},
               follow_redirects=True)
    assert r.status_code == 200
    with bb.app.app_context():
        assert bb.db.session.get(bb.Payment, bill) is None, \
            "the genuine form must still delete"
    check("the app's own form, with its token, works normally")


def test_login_is_rate_limited():
    c = signed_in()
    c.get("/logout")
    bb.limiter.enabled = True
    bb.limiter.reset()
    codes = []
    for _ in range(12):
        r = c.post("/login", data={"username": "tester", "password": "wrong",
                                   "csrf_token": token(c, "/login")})
        codes.append(r.status_code)
    bb.limiter.enabled = False
    assert 429 in codes, f"guessing should be throttled, got {set(codes)}"
    check(f"password guessing is throttled after {codes.index(429)} tries")


def test_the_scheduled_task_is_still_reachable():
    #the cron service can't send a csrf token, so this route is exempt,
    #and the secret token is what protects it
    c = bb.app.test_client()
    assert c.get("/tasks/run-daily").status_code == 403, "no token = forbidden"
    assert c.get("/tasks/run-daily?token=wrong").status_code == 403
    check("the daily task route is exempt from csrf but still token-locked")


def test_secret_key_must_be_set_in_production():
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    env["DATABASE_URL"] = "sqlite:///whatever.db"
    env["SECRET_KEY"] = ""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=here, env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr, \
        "a deployed app with no SECRET_KEY must refuse to start"
    check("a deployed app refuses to start without SECRET_KEY")


def test_session_cookie_is_locked_down():
    assert bb.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert bb.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    check("session cookie is http-only and same-site")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"\n{fn.__name__.replace('_', ' ')}")
        fn()
    print(f"\nALL {len(tests)} SECURITY GROUPS PASSED")
