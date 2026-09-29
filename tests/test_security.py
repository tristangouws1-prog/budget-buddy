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
                              "password": "pw123456", "confirm": "pw123456",
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


def boot(**extra):
    """ Import the app in a fresh process with these env vars, and report
    whether the session cookie came out https-only """
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if k not in ("SECRET_KEY", "DATABASE_URL", "BB_DEPLOYED")}
    env.update(extra)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(
        [sys.executable, "-c",
         "import app; print('SECURE:', app.app.config['SESSION_COOKIE_SECURE'])"],
        cwd=here, env=env, capture_output=True, text=True)


def test_the_deploy_flag_locks_the_app_down():
    #the real server runs SQLite, so DATABASE_URL is never set there and the
    #SECRET_KEY guard used to never fire. BB_DEPLOYED is what says "live" now
    r = boot(BB_DEPLOYED="true", SECRET_KEY="")
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr, \
        "BB_DEPLOYED with no SECRET_KEY must refuse to start"
    check("BB_DEPLOYED alone refuses to start without SECRET_KEY")

    r = boot(BB_DEPLOYED="true", SECRET_KEY="a-real-key")
    assert "SECURE: True" in r.stdout, f"cookie not secured: {r.stdout}{r.stderr}"
    check("the deployed site gets an https-only session cookie")

    r = boot(SECRET_KEY="a-real-key")
    assert "SECURE: False" in r.stdout, "locally the cookie must stay non-secure"
    check("locally it stays off, so plain http still logs in")


def test_session_cookie_is_locked_down():
    assert bb.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert bb.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    check("session cookie is http-only and same-site")


def test_asking_for_a_reset_is_rate_limited():
    #every POST sends a real email, so this is the one worth throttling
    signed_in()                      #gives us a clean database
    c = bb.app.test_client()         #but a logged-out client, or /forgot redirects
    sent_before = len(bb.sent_emails)
    bb.limiter.enabled = True
    bb.limiter.reset()
    codes = []
    for _ in range(8):
        r = c.post("/forgot", data={"email": "nobody@test.local",
                                    "csrf_token": token(c, "/forgot")})
        codes.append(r.status_code)
    bb.limiter.enabled = False
    assert 429 in codes, f"reset emails should be throttled, got {set(codes)}"
    assert len(bb.sent_emails) == sent_before, "no such account, so nothing to send"
    check(f"reset emails are throttled after {codes.index(429)} tries")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"\n{fn.__name__.replace('_', ' ')}")
        fn()
    print(f"\nALL {len(tests)} SECURITY GROUPS PASSED")
