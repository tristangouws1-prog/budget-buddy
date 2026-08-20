"""
Regression tests for Budget Buddy.

    python tests/test_budget_buddy.py


"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as bb

bb.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
bb.app.config["TESTING"] = True
del bb.app.extensions["sqlalchemy"]
bb.db.init_app(bb.app)

for _var in ("DEV_ADMIN_PASSWORD", "DEV_ADMIN_EMAIL", "DEV_ADMIN_USER"):
    os.environ.pop(_var, None)

EMAIL = "tester@test.local"


def fresh(hatched=True):
    """ Empty database with one registered user, returns a logged-in client """
    with bb.app.app_context():
        bb.db.drop_all()
        bb.db.create_all()
    client = bb.app.test_client()
    client.post("/register", data={
        "username": "tester", "email": EMAIL,
        "password": "pw12345", "confirm": "pw12345",
    }, follow_redirects=True)
    if hatched:
        with bb.app.app_context():
            b = bb.Buddy.query.first()
            b.stage = "hatched"
            bb.db.session.commit()
    return client


def add_bill(client, name, amount, day=5, kind="fixed", freq="monthly", **extra):
    data = {"name": name, "description": "", "amount": str(amount),
            "due_day": str(day), "bill_type": kind, "frequency": freq}
    data.update({k: str(v) for k, v in extra.items()})
    client.post("/add", data=data, follow_redirects=True)
    with bb.app.app_context():
        return bb.Payment.query.filter_by(name=name).first().id


def buddy():
    with bb.app.app_context():
        bb.db.session.expire_all()
        return bb.Buddy.query.filter_by(is_active=True).first()


def check(name):
    print(f"  ok  {name}")


# ---------- the buddy
def test_buddy_appears_and_reacts():
    client = fresh()
    html = client.get("/").get_data(as_text=True)
    assert 'id="buddy-dock"' in html
    assert "buddy-mood-neutral" in html
    check("buddy shows on the dashboard, neutral with no bills")

    today = datetime.date.today()
    if today.day > 3:
        add_bill(client, "Overdue Wifi", 499, day=today.day - 3)
        html = client.get("/").get_data(as_text=True)
        assert "buddy-mood-worried" in html and "Overdue Wifi" in html
        check("an overdue bill worries the buddy and is named")

    with bb.app.app_context():
        for p in bb.Payment.query.all():
            p.is_paid = True
            p.amount_paid = p.amount
        bb.db.session.commit()
    bill = add_bill(client, "Spotify", 99.99)
    client.get(f"/pay/{bill}")
    assert "buddy-mood-happy" in client.get("/").get_data(as_text=True)
    check("everything paid makes the buddy happy")

    html = client.get("/logout", follow_redirects=True).get_data(as_text=True)
    assert 'id="buddy-dock"' not in html
    check("logging out hides the buddy")


def test_levels_and_xp_cannot_be_farmed():
    for points, level in [(0, 1), (49, 1), (50, 2), (199, 2), (200, 3), (450, 4)]:
        assert bb.buddy_level(points) == level, points
    assert bb.xp_for_level(3) == 200
    check("level boundaries: 50, 200, 450")

    client = fresh()
    assert buddy().xp == 5, "logging in should give the daily check-in"
    client.get("/"); client.get("/")
    assert buddy().xp == 5, "the check-in only counts once a day"
    check("daily check-in awards once")

    bill = add_bill(client, "Gym", 200)
    assert buddy().xp == 15
    client.get(f"/pay/{bill}")
    assert buddy().xp == 30
    for _ in range(5):
        client.get(f"/unpaid/{bill}")
        client.get(f"/pay/{bill}")
    assert buddy().xp == 30, "paying and un-paying must never farm xp"
    check("add +10, pay +15, and no farming by re-paying")

    client.get(f"/unpaid/{bill}")
    assert buddy().xp == 15, "undo takes the payment xp back"
    check("undo returns the xp and coins")

    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        p.carried_over = 120.0
        bb.db.session.commit()
    before = buddy().xp
    client.post(f"/carryover_paid/{bill}")
    assert buddy().xp == before + 20
    client.post(f"/carryover_paid/{bill}")
    assert buddy().xp == before + 20, "clearing an empty carryover earns nothing"
    check("clearing carried-over debt awards once")


def test_eggs_hatch():
    client = fresh(hatched=False)
    assert buddy().stage == "egg", "new accounts start as an egg"
    html = client.get("/").get_data(as_text=True)
    assert "buddy-egg" in html and "???" in html
    assert "buddy-eyes" not in html, "the animal must stay hidden inside the egg"
    check("a new account starts with a hidden egg")

    for points, crack in [(20, 0), (30, 1), (55, 2), (80, 3)]:
        with bb.app.app_context():
            bb.Buddy.query.first().xp = points
            bb.db.session.commit()
        assert f"buddy-crack-{crack}" in client.get("/").get_data(as_text=True)
    check("the crack widens at 25%, 50% and 75%")

    with bb.app.app_context():
        bb.Buddy.query.first().xp = 95
        bb.db.session.commit()
    client.post("/add", data={"name": "Netflix", "description": "", "amount": "199",
                              "due_day": "3", "bill_type": "fixed",
                              "frequency": "monthly"})
    b = buddy()
    assert b.stage == "hatched" and b.species in bb.BUDDY_SPECIES
    html = client.get("/").get_data(as_text=True)
    assert "buddy-hatching" in html, "the hatch animation should play"
    assert "buddy-hatching" not in client.get("/").get_data(as_text=True), \
        "and only once"
    check(f"hatches at 100 xp into a {b.species}, animation plays once")


def test_species_and_sprites():
    assert bb.BUDDY_SPECIES == ["blobcat", "mintcat", "peachcat", "blackcat",
                                "frog", "purplefrog"]
    assert bb.FROG_SPECIES == ("frog", "purplefrog")
    check("six species, two of them frogs")

    client = fresh()

    def as_species(name):
        with bb.app.app_context():
            b = bb.Buddy.query.first()
            b.species, b.xp, b.coins = name, 300, 900
            bb.db.session.commit()
        return client.get("/").get_data(as_text=True)

    html = as_species("frog")
    assert '<rect x="1" y="4" width="14" height="9"/>' in html, "frog body"
    assert "buddy-eye-fill" in html, "frogs have white bulging eyes"
    assert "buddy-whisker" not in html, "frogs have no whiskers"
    check("the frog has its own body, white eyes and no whiskers")

    assert '<rect x="1" y="4" width="14" height="9"/>' in as_species("purplefrog")
    check("the purple frog shares the frog drawing")

    html = as_species("blackcat")
    assert "buddy-whisker" in html and '<rect x="2" y="4" width="12" height="9"/>' in html
    check("the black cat is still a cat")

    
    as_species("frog")
    for item in ("witch_hat", "top_hat", "party_hat"):
        with bb.app.app_context():
            bb.OwnedCosmetic.query.delete()
            bb.db.session.commit()
        client.post(f"/buddy/buy/{item}", follow_redirects=True)
        assert '<rect x="3" y="1" width="2" height="2"/>' in \
            client.get("/").get_data(as_text=True), f"{item} hides the frog's eyes"
    check("every hat clears the frog's eye bumps")


def test_shop_and_room():
    client = fresh()
    with bb.app.app_context():
        b = bb.Buddy.query.first()
        b.xp, b.coins = 300, 1000
        bb.db.session.commit()

    def worn():
        with bb.app.app_context():
            return sorted(c.item_key for c in
                          bb.OwnedCosmetic.query.filter_by(equipped=True).all())

    client.post("/buddy/buy/party_hat", follow_redirects=True)
    assert worn() == ["party_hat"] and buddy().coins == 1000 - 60
    check("buying wears the item and charges the coins")

    html = client.post("/buddy/buy/party_hat", follow_redirects=True).get_data(as_text=True)
    assert "already own" in html and buddy().coins == 1000 - 60
    check("buying twice is refused and costs nothing")

    client.post("/buddy/buy/flower", follow_redirects=True)
    assert worn() == ["flower"], "a second hat bumps the first"
    client.post("/buddy/buy/bow_tie", follow_redirects=True)
    assert worn() == ["bow_tie", "flower"], "different slots stack"
    check("one hat at a time, but a hat and an accessory together")

    decor = {k: v["slot"] for k, v in bb.BUDDY_SHOP.items()
             if v["slot"] not in bb.WEARABLE_SLOTS}
    assert decor == {"window": "wall_left", "poster": "wall_right",
                     "plant": "floor_left", "lamp": "floor_right",
                     "rug": "floor"}
    for key in ("window", "poster", "plant", "lamp", "rug"):
        client.post(f"/buddy/buy/{key}", follow_redirects=True)
    html = client.get("/buddy").get_data(as_text=True)
    for css in ("room-window", "room-poster", "room-plant", "room-lamp", "room-rug"):
        assert css in html, f"{css} missing from a fully furnished room"
    check("all four corners and the rug can be filled at once")

    
    with bb.app.app_context():
        bb.Buddy.query.first().xp = 0
        bb.db.session.commit()
    html = client.post("/buddy/buy/top_hat", follow_redirects=True).get_data(as_text=True)
    assert "unlocks at level 3" in html
    check("items are locked until the right level")


def test_the_house():
    client = fresh()
    assert bb.MAX_BUDDIES == 1 + len(bb.EGG_LEVELS), \
        "the cap should fit the starting egg plus every milestone egg"
    check(f"house cap {bb.MAX_BUDDIES} wastes no milestone egg")

    with bb.app.app_context():
        bb.Buddy.query.first().xp = 195       # just below level 3
        bb.db.session.commit()
    add_bill(client, "Rent", 6000)            # +10 crosses it
    with bb.app.app_context():
        buddies = bb.Buddy.query.order_by(bb.Buddy.id).all()
    assert len(buddies) == 2 and buddies[1].stage == "egg"
    assert buddies[0].is_active, "the original buddy stays out front"
    check("a milestone level earns a new egg")

    egg_id = buddies[1].id
    client.post(f"/buddy/activate/{egg_id}", follow_redirects=True)
    assert buddy().id == egg_id
    assert "buddy-egg" in client.get("/").get_data(as_text=True)
    check("bringing another buddy out changes every page")

    with bb.app.app_context():
        resting = bb.db.session.get(bb.Buddy, buddies[0].id).xp
    add_bill(client, "Water", 300)
    with bb.app.app_context():
        assert bb.db.session.get(bb.Buddy, buddies[0].id).xp == resting, \
            "xp must only go to the buddy that is out front"
    check("only the active buddy earns xp")

    client.get("/logout")
    client.post("/register", data={"username": "other", "email": "other@test.local",
                                   "password": "pw12345", "confirm": "pw12345"},
                follow_redirects=True)
    assert client.post(f"/buddy/activate/{egg_id}").status_code == 404
    check("you cannot bring out someone else's buddy")


# ---------------- billing
def test_weekly_bills_cost_the_whole_month():
    client = fresh()
    bill = add_bill(client, "Groceries", 100, day=1, freq="weekly")
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        weeks = bb.weeks_in_month(p)
        assert bb.month_obligation(p) == 100 * weeks
        assert bb.remaining_this_month(p) == 100 * weeks
    check(f"a weekly bill costs {weeks} weeks, not the 4.33 average")

    client.get(f"/pay/{bill}")
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, bill)
        assert bb.remaining_this_month(p) == 100 * (weeks - 1)
        assert bb.weeks_paid_this_month(p) == 1
    check("marking a week paid clears exactly one week")

    bb.create_weekly_reminder()
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, bill)
        assert not p.is_paid, "Monday makes it tickable again"
        assert not p.carried_over, "a missed week must not become debt mid-month"
        assert bb.remaining_this_month(p) == 100 * (weeks - 1)
    check("the Monday reset keeps the month's arithmetic intact")

    html = client.get("/").get_data(as_text=True)
    assert f"1 of {weeks} weeks" in html
    check("the dashboard shows how many weeks are done")


def test_month_end_carries_the_shortfall_over():
    client = fresh()
    bill = add_bill(client, "Transport", 50, day=3, freq="weekly")
    with bb.app.app_context():
        year, month = bb.previous_month()
        p = bb.db.session.get(bb.Payment, bill)
        owed = bb.month_obligation(p, year, month)
        bb.db.session.add(bb.PaymentLog(
            bill_name="Transport", amount_paid=50, payment_id=bill,
            user_id=p.user_id, paid_at=datetime.datetime(year, month, 15)))
        bb.db.session.commit()
    bb.create_monthly_reminders()
    with bb.app.app_context():
        bb.db.session.expire_all()
        assert bb.db.session.get(bb.Payment, bill).carried_over == round(owed - 50, 2)
    check("an unpaid week rolls over when the month closes")


def test_once_off_bills_persist_then_archive():
    client = fresh()
    bill = add_bill(client, "New Fridge", 8000, day=20, kind="once_off")

    bb.create_monthly_reminders()
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, bill)
        assert not p.is_paid and not p.carried_over, \
            "an unpaid once-off must be left completely alone"
    check("the monthly reset skips once-off bills")

    html = client.post(f"/archive/{bill}", follow_redirects=True).get_data(as_text=True)
    assert "Only paid once-off" in html
    check("an unpaid once-off cannot be archived")

    client.get(f"/pay/{bill}")
    bb.create_monthly_reminders()
    with bb.app.app_context():
        bb.db.session.expire_all()
        assert bb.db.session.get(bb.Payment, bill).is_paid, \
            "a paid once-off stays paid into the new month"
    client.post(f"/archive/{bill}", follow_redirects=True)
    assert f'data-id="{bill}"' not in client.get("/").get_data(as_text=True)
    check("a paid once-off archives away off the dashboard")


def test_paying_ticks_off_reminders():
    client = fresh()
    bill = add_bill(client, "Wifi", 500)
    with bb.app.app_context():
        uid = bb.User.query.first().id
        bb.db.session.add(bb.Reminder(message="'Wifi' (R500.00) is overdue!",
                                      category="overdue", payment_id=bill, user_id=uid))
        bb.db.session.add(bb.Reminder(message="Monthly reminder: 'Wifi' is due soon",
                                      category="monthly", user_id=uid))
        bb.db.session.add(bb.Reminder(message="Weekly Check in! Anything new?",
                                      category="weekly", user_id=uid))
        bb.db.session.commit()
    client.get(f"/pay/{bill}")
    with bb.app.app_context():
        read = {r.category: r.is_read for r in bb.Reminder.query.all()}
    assert read["overdue"] and read["monthly"], "the bill's reminders should tick"
    assert not read["weekly"], "unrelated reminders must stay unread"
    check("paying ticks off that bill's reminders only")


def test_page_updates_without_a_reload():
    client = fresh()
    html = client.get("/").get_data(as_text=True)
    assert "softRefresh" in html and 'cache: "no-store"' in html, \
        "the totals go stale if the browser is allowed to cache the page"
    check("the soft reload never serves a cached page")


# ----------- dev account
def test_local_test_account():
    with bb.app.app_context():
        bb.db.drop_all()
        bb.db.create_all()
        bb.seed_dev_admin()
        assert bb.User.query.count() == 0, "no password set means no account"
    check("without DEV_ADMIN_PASSWORD nothing is created")

    os.environ["DEV_ADMIN_PASSWORD"] = "letmein"
    with bb.app.app_context():
        bb.seed_dev_admin()
        user = bb.User.query.first()
        assert user.check_password("letmein")
        assert user.password_hash != "letmein", "must be hashed"
    client = bb.app.test_client()
    assert "Welcome back" in client.post(
        "/login", data={"username": user.username, "password": "letmein"},
        follow_redirects=True).get_data(as_text=True)
    check("the seeded account logs in normally")

    os.environ["DEV_ADMIN_PASSWORD"] = "different"
    with bb.app.app_context():
        bb.seed_dev_admin()
        assert bb.User.query.count() == 1, "must not make a second account"
        assert bb.User.query.first().check_password("different")
    check("running again resets the password, no duplicate")

    client.get("/logout")
    for bad in ("wrong", ""):
        assert "Wrong username or password" in client.post(
            "/login", data={"username": "Buddy", "password": bad},
            follow_redirects=True).get_data(as_text=True)
    check("a wrong password is still refused - there is no back door")
    os.environ.pop("DEV_ADMIN_PASSWORD")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for func in tests:
        print(f"\n{func.__name__.replace('_', ' ')}")
        func()
    print(f"\nALL {len(tests)} GROUPS PASSED")
