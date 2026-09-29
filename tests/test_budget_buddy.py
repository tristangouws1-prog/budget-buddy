"""
Regression tests for Budget Buddy.

    python tests/test_budget_buddy.py


"""
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as bb

bb.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
bb.app.config["TESTING"] = True
#the test client has no browser to carry a csrf token, and the login
#limiter would trip on repeated test logins
bb.app.config["WTF_CSRF_ENABLED"] = False
bb.app.config["RATELIMIT_ENABLED"] = False
bb.limiter.enabled = False
#belt and braces: TESTING already stops send_email touching the network,
#and this proves no test ever slips a real message out
assert not bb.sent_emails
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
        "password": "pw123456", "confirm": "pw123456",
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


class counting_queries:
    """ Count the SQL statements a block of code runs.
    Used to prove a page's cost doesn't grow with the number of bills """

    def __enter__(self):
        from sqlalchemy import event, engine
        self.n = 0
        self._event, self._engine = event, engine

        def tick(*args, **kwargs):
            self.n += 1

        self._tick = tick
        event.listen(engine.Engine, "after_cursor_execute", tick)
        return self

    def __exit__(self, *exc):
        self._event.remove(self._engine.Engine, "after_cursor_execute", self._tick)
        return False


# ---------- the buddy
def test_buddy_appears_and_reacts():
    client = fresh()
    html = client.get("/").get_data(as_text=True)
    assert 'id="buddy-dock"' in html
    assert "buddy-mood-neutral" in html
    check("buddy shows on the dashboard, neutral with no bills")

    today = bb.local_today()
    if today.day > 3:
        add_bill(client, "Overdue Wifi", 499, day=today.day - 3)
        html = client.get("/").get_data(as_text=True)
        assert "buddy-mood-worried" in html and "Overdue Wifi" in html
        check("an overdue bill worries the buddy and is named")

    with bb.app.app_context():
        for p in bb.Payment.query.all():
            p.is_paid = True
            p.amount_paid_cents = p.amount_cents
        bb.db.session.commit()
    bill = add_bill(client, "Spotify", 99.99)
    client.post(f"/pay/{bill}")
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
    client.post(f"/pay/{bill}")
    assert buddy().xp == 30
    for _ in range(5):
        client.post(f"/unpaid/{bill}")
        client.post(f"/pay/{bill}")
    assert buddy().xp == 30, "paying and un-paying must never farm xp"
    check("add +10, pay +15, and no farming by re-paying")

    client.post(f"/unpaid/{bill}")
    assert buddy().xp == 15, "undo takes the payment xp back"
    check("undo returns the xp and coins")

    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        p.carried_over_cents = 12000
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
                                   "password": "pw123456", "confirm": "pw123456"},
                follow_redirects=True)
    assert client.post(f"/buddy/activate/{egg_id}").status_code == 404
    check("you cannot bring out someone else's buddy")


def test_reminders_are_built_but_never_posted():
    client = fresh()
    add_bill(client, "Wifi", 500, day=1)
    before = len(bb.sent_emails)
    bb.create_monthly_reminders()
    with bb.app.app_context():
        assert bb.Reminder.query.count() > 0, "the monthly job should write reminders"
    #the job still assembles the message, it just never reaches a mail server
    assert len(bb.sent_emails) > before, "the email path should still be exercised"
    assert all("@" in e["to"] for e in bb.sent_emails)
    check(f"the monthly job wrote reminders and queued "
          f"{len(bb.sent_emails) - before} email(s) without sending")


# ---------- money
def test_money_is_exact_integer_cents():
    #the whole point: no float can hold 41.67, so cents are stored instead
    assert bb.parse_cents("41.67") == 4167
    assert bb.parse_cents("0.1") + bb.parse_cents("0.2") == bb.parse_cents("0.3")
    assert bb.parse_cents("199,99") == 19999, "comma decimals still accepted"
    assert bb.parse_cents("") is None and bb.parse_cents(None) is None
    assert bb.money(4167) == "41.67" and bb.money(0) == "0.00"
    assert bb.money(None) == "0.00", "a missing amount shows as zero, not a crash"
    assert bb.rands(4167) == "41.67" and bb.rands(None) == ""
    check("rands parse to exact cents and format back")

    client = fresh()
    bill = add_bill(client, "Odd", 41.67, day=1, freq="weekly")
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        assert isinstance(p.amount_cents, int) and p.amount_cents == 4167
        weeks = bb.weeks_in_month(p)
        #weeks of 41.67 must total exactly, which floats got wrong
        assert bb.month_obligation(p) == 4167 * weeks
    check(f"41.67 stored as 4167 cents, {weeks} weeks = {4167 * weeks} cents exactly")

    for w in range(1, weeks + 1):
        client.post(f"/week/{bill}/{w}")
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, bill)
        assert bb.remaining_this_month(p) == 0, "every week must clear exactly"
        assert bb.weeks_paid_this_month(p) == weeks
    check("paying every week clears the month to exactly zero")

    #percentages are not money and stay fractional
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        p.interest_rate = 12.5
        bb.db.session.commit()
        assert bb.db.session.get(bb.Payment, bill).interest_rate == 12.5
    assert bb.percent_of(4100000, 12.5 / 12) == 42708
    check("percentages stay fractional; interest rounds to whole cents")


# ---------------- billing
def test_weekly_loan_traffic_lights():
    """ A loan paid weekly is red while nothing is paid, amber part way
    through the month, and green only once every week is ticked off """
    client = fresh()
    #due on today's weekday, so at least one week is always already due
    today_weekday = bb.local_today().weekday() + 1
    loan = add_bill(client, "Weekly loan", 250, day=today_weekday,
                    kind="loan", freq="weekly",
                    total_value=60000, current_balance=42000)
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, loan)
        weeks = bb.weeks_in_month(p)
        assert bb.get_status(p) == "overdue", "an untouched weekly loan is red"
    html = client.get("/").get_data(as_text=True)
    assert "bill-overdue" in html
    check(f"nothing paid -> red, {weeks} weeks still owing")

    client.post(f"/week/{loan}/1")
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, loan)
        assert bb.get_status(p) == "partial", "one week paid is amber, not green"
    html = client.get("/").get_data(as_text=True)
    assert "bill-partial" in html and "part paid" in html
    assert f"1 of {weeks} week" in html
    check("week 1 paid, the rest waiting -> amber")

    #every week but the last still reads amber, never green
    for w in range(2, weeks):
        client.post(f"/week/{loan}/{w}")
        with bb.app.app_context():
            bb.db.session.expire_all()
            assert bb.get_status(bb.db.session.get(bb.Payment, loan)) == "partial",                 f"week {w} of {weeks} must still be amber"
    check(f"still amber all the way to week {weeks - 1}")

    client.post(f"/week/{loan}/{weeks}")
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, loan)
        assert bb.get_status(p) == "paid", "every week paid is green"
        assert bb.remaining_this_month(p) == 0
        assert not bb.weeks_behind(p), "nothing is outstanding once it is green"
    html = client.get("/").get_data(as_text=True)
    assert "bill-paid" in html and "bill-partial" not in html
    check("every week paid -> green, nothing left owing this month")

    #unticking the last week takes it back to amber, not straight to red
    client.post(f"/week/{loan}/{weeks}")
    with bb.app.app_context():
        bb.db.session.expire_all()
        assert bb.get_status(bb.db.session.get(bb.Payment, loan)) == "partial"
    check("undoing the last week goes back to amber")

    #plain weekly bills get the same boxes and colours now (#61)
    plain = add_bill(client, "Groceries", 100, day=today_weekday, freq="weekly")
    with bb.app.app_context():
        assert bb.get_status(bb.db.session.get(bb.Payment, plain)) == "overdue"
    client.post(f"/pay/{plain}")
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, plain)
        assert bb.weeks_paid_this_month(p) == 1, "Mark paid ticks the next week"
        assert bb.get_status(p) == "partial", "one week of groceries is amber, not green"
    html = client.get("/").get_data(as_text=True)
    assert f"/week/{plain}/1" in html and f"/pay/{plain}" not in html
    check("plain weekly bills get the week boxes and the same colours")


def test_part_of_a_week_can_be_paid():
    """ #62 - a weekly bill can take part of a week's instalment """
    client = fresh()
    today_weekday = bb.local_today().weekday() + 1
    loan = add_bill(client, "Weekly loan", 200, day=today_weekday, kind="loan",
                    freq="weekly", total_value=60000, current_balance=42000)

    def paid():
        with bb.app.app_context():
            bb.db.session.expire_all()
            p = bb.db.session.get(bb.Payment, loan)
            return bb.paid_in_month(p), bb.weeks_paid_this_month(p), bb.get_status(p)

    client.post(f"/week_part/{loan}", data={"part_amount": "50"})
    assert paid() == (5000, 0, "partial"), paid()
    html = client.get("/").get_data(as_text=True)
    assert "week-box-part" in html and "--part: 25%" in html
    check("R50 of a R200 week reads amber, the next box a quarter full")

    xp = buddy().xp
    client.post(f"/week_part/{loan}", data={"part_amount": "150"})
    assert paid()[:2] == (20000, 1) and buddy().xp == xp + 15
    check("topping the week up to R200 ticks it and earns its xp")

    client.post(f"/week_part/{loan}", data={"part_amount": "60"})
    client.post(f"/week/{loan}/2")
    assert paid()[:2] == (40000, 2), "ticking a part paid box only tops it up"
    check("ticking a part paid box pays just the rest of that week")

    client.post(f"/week_part/{loan}", data={"part_amount": "60"})
    client.post(f"/week/{loan}/2")
    assert paid()[:2] == (26000, 1), paid()
    check("undoing a week keeps the part payment")

    html = client.post(f"/week_part/{loan}", data={"part_amount": "99999"},
                       follow_redirects=True).get_data(as_text=True)
    assert "is left to pay" in html and paid()[0] == 26000
    check("can't pay more than the month still owes")


def test_weekly_bills_cost_the_whole_month():
    client = fresh()
    bill = add_bill(client, "Groceries", 100, day=1, freq="weekly")
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        weeks = bb.weeks_in_month(p)
        assert bb.month_obligation(p) == 10000 * weeks   # cents
        assert bb.remaining_this_month(p) == 10000 * weeks
    check(f"a weekly bill costs {weeks} weeks, not the 4.33 average")

    client.post(f"/pay/{bill}")
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, bill)
        assert bb.remaining_this_month(p) == 10000 * (weeks - 1)
        assert bb.weeks_paid_this_month(p) == 1
    check("marking a week paid clears exactly one week")

    bb.create_weekly_reminder()
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, bill)
        assert not p.is_paid, "one week doesn't finish the month"
        assert not p.carried_over_cents, "a missed week must not become debt mid-month"
        assert bb.remaining_this_month(p) == 10000 * (weeks - 1)
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
            bill_name="Transport", amount_paid_cents=5000, payment_id=bill,
            user_id=p.user_id, paid_at=datetime.datetime(year, month, 15)))
        bb.db.session.commit()
    bb.create_monthly_reminders()
    with bb.app.app_context():
        bb.db.session.expire_all()
        assert bb.db.session.get(bb.Payment, bill).carried_over_cents == owed - 5000
    check("an unpaid week rolls over when the month closes")


def test_part_paid_bar_and_the_carryover_divider():
    """ #2 - a bill's own progress bar, with a dark divider where the debt
    carried over from earlier months begins """
    client = fresh()
    bill = add_bill(client, "Water", 400, day=10, kind="variable")

    #a partial payment must show on the bar. this used to read a field the
    #model doesn't have (amount_paid), so the bar never appeared at all
    client.post(f"/partial_pay/{bill}", data={"paid_amount": "100"})
    html = client.get("/").get_data(as_text=True)
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        assert p.amount_paid_cents == 10000 and not p.is_paid
    assert "bill-progress-fill" in html, "a part paid bill must show its bar"
    assert "R100.00 paid of R400.00" in html
    assert 'value="100.00"' in html, "the box should remember what was paid"
    assert "bill-progress-divider" not in html, "no old debt, so no divider yet"
    check("a partial payment fills the bill's own progress bar")

    #now give it debt from an earlier month
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        p.carried_over_cents = 40000        # R400 from before
        bb.db.session.commit()
    html = client.get("/").get_data(as_text=True)
    assert "bill-progress-divider" in html and "bill-progress-carried" in html
    #R400 this month + R400 carried = the divider sits halfway along the bar
    assert "left: 50.0%" in html, "the divider marks where this month ends"
    #R100 of the R800 total is paid, so the fill is an eighth of the whole bar
    assert 'style="width: 12%"' in html, "the fill is a share of everything owed"
    check("carried over debt gets its own part of the bar, divided off")

    #paying the old debt off takes the divider away again
    client.post(f"/carryover_paid/{bill}")
    html = client.get("/").get_data(as_text=True)
    assert "bill-progress-divider" not in html
    check("clearing the old debt removes the divider")

    #and a weekly bill's bar works the same way
    weekly = add_bill(client, "Petrol", 100, day=1, freq="weekly")
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, weekly)
        p.carried_over_cents = 10000
        bb.db.session.commit()
        month = bb.month_obligation(p)
    client.post(f"/pay/{weekly}")
    html = client.get("/").get_data(as_text=True)
    assert html.count("bill-progress-divider") == 1
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, weekly)
        assert bb.paid_towards_this_month(p) == 10000, "one week of the month"
        assert bb.paid_towards_this_month(p) <= month, "never more than the month owes"
    check("a weekly bill shows the same divider for its older weeks")


def test_the_reminder_messages_say_the_right_thing():
    """ #31 - the exact wording of every reminder, and the email it goes out in """
    client = fresh()
    with bb.app.app_context():
        user = bb.User.query.first()
        user.email_reminders = True
        bb.db.session.commit()

    #a bill that is already overdue, with a description on it
    client.post("/add", data={"name": "Rent", "description": "the flat",
                              "amount": "5500", "due_day": "1",
                              "bill_type": "fixed", "frequency": "monthly"},
                follow_redirects=True)
    with bb.app.app_context():
        user = bb.User.query.first()
        p = bb.Payment.query.filter_by(name="Rent").first()
        overdue = bb.overdue_message(p, user)
        soon = bb.due_soon_message(p, user)
        monthly = bb.monthly_message(p, user)

        assert overdue.startswith("'Rent' (R5500.00) is overdue!"), overdue
        assert "the flat" in overdue, "the bill's description must reach the message"
        assert "1st" in overdue, "the due day is written 1st, not 1"
        assert "R550000" not in overdue, "cents must never be printed raw"
        assert "'Rent' (R5500.00)" in soon and "the flat" in soon
        assert monthly.startswith("Monthly reminder: 'Rent' (R5500.00)")
    check("every message names the bill, its real amount and its description")

    #the summary line used to print cents as rands - R550000.00 for one bill
    with bb.app.app_context():
        user = bb.User.query.first()
        payments = bb.Payment.query.all()
        summary = bb.monthly_summary_message(payments, user)
        assert "R5500.00" in summary, summary
        assert "R550000" not in summary, "the month total was printed in cents"
    check("the monthly total reads R5500.00, not R550000.00")

    #a variable bill asks to be confirmed, in the user's own currency
    with bb.app.app_context():
        user = bb.User.query.first()
        user.currency = "$"
        bb.db.session.commit()
    client.post("/add", data={"name": "Electricity", "description": "",
                              "amount": "700", "due_day": "20",
                              "bill_type": "variable", "frequency": "monthly"},
                follow_redirects=True)
    with bb.app.app_context():
        user = bb.User.query.first()
        p = bb.Payment.query.filter_by(name="Electricity").first()
        assert bb.confirm_amount_message(p, user).startswith(
            "Has the amount for 'Electricity' been updated this month? It's currently $700.00")
    check("the currency setting is used, not a hard coded R")

    #the real job writes those same words, and emails them
    before = len(bb.sent_emails)
    bb.create_monthly_reminders()
    with bb.app.app_context():
        messages = [r.message for r in bb.Reminder.query.all()]
    assert any(m.startswith("Monthly reminder: 'Rent'") for m in messages)
    assert len(bb.sent_emails) > before, "the reminders should be emailed too"
    body = bb.sent_emails[-1]["body"]
    assert all(m in body for m in messages if "Rent" in m), \
        "every reminder must appear in the email, word for word"
    assert "the flat" in body, "the description has to survive into the email"
    check("the scheduled job writes and emails exactly those messages")

    #the test email button: one real send, showing every wording
    before = len(bb.sent_emails)
    client.post("/settings/test-email", follow_redirects=True)
    assert len(bb.sent_emails) == before + 1, "the button must send one email"
    test_mail = bb.sent_emails[-1]
    assert test_mail["to"] == EMAIL
    assert test_mail["subject"] == "Budget Buddy test reminder"
    for phrase in ("is overdue!", "is due", "Monthly reminder:",
                   "Has the amount for", "Weekly Check in!", "New month!"):
        assert phrase in test_mail["body"], f"the test email is missing {phrase!r}"
    with bb.app.app_context():
        count = bb.Reminder.query.count()
    client.post("/settings/test-email", follow_redirects=True)
    with bb.app.app_context():
        assert bb.Reminder.query.count() == count, \
            "a test email must not add real reminders"
    check("the settings button sends one real email with every wording in it")

    #no bills, no confusing empty email
    empty = fresh()
    empty.post("/settings/test-email", follow_redirects=True)
    assert "no bills yet" in bb.sent_emails[-1]["body"]
    check("with no bills the test email says so instead of arriving blank")


def test_once_off_bills_persist_then_archive():
    client = fresh()
    bill = add_bill(client, "New Fridge", 8000, day=20, kind="once_off")

    bb.create_monthly_reminders()
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, bill)
        assert not p.is_paid and not p.carried_over_cents, \
            "an unpaid once-off must be left completely alone"
    check("the monthly reset skips once-off bills")

    html = client.post(f"/archive/{bill}", follow_redirects=True).get_data(as_text=True)
    assert "Only paid once-off" in html
    check("an unpaid once-off cannot be archived")

    client.post(f"/pay/{bill}")
    bb.create_monthly_reminders()
    with bb.app.app_context():
        bb.db.session.expire_all()
        assert bb.db.session.get(bb.Payment, bill).is_paid, \
            "a paid once-off stays paid into the new month"
    client.post(f"/archive/{bill}", follow_redirects=True)
    assert f'data-id="{bill}"' not in client.get("/").get_data(as_text=True)
    check("a paid once-off archives away off the dashboard")


def test_your_own_reminders():
    """ #59 - your own reminders, on the dashboard and emailed on the day """
    client = fresh()
    today = bb.local_today()
    tomorrow = today + datetime.timedelta(days=1)
    client.post("/reminders/mine", data={"text": "Pick up prescription",
                                         "due_date": tomorrow.isoformat(), "repeat": "none"})
    client.post("/reminders/mine", data={"text": "Water the plants",
                                         "due_date": today.isoformat(), "repeat": "weekly"})
    html = client.get("/").get_data(as_text=True)
    assert "Water the plants" in html and "Pick up prescription" not in html
    check("a reminder shows on the dashboard from its date, not before")
    page = client.get("/reminders").get_data(as_text=True)
    assert "Water the plants" in page and "Pick up prescription" in page and "Every week" in page
    check("the reminders page lists them all, with their repeat")

    before = len(bb.sent_emails)
    bb.email_personal_reminders()
    assert len(bb.sent_emails) == before + 1
    body = bb.sent_emails[-1]["body"]
    assert "Water the plants" in body and "prescription" not in body
    bb.email_personal_reminders()
    assert len(bb.sent_emails) == before + 1, "a re-run must not email it again"
    check("emailed once on the day, never twice")

    with bb.app.app_context():
        plants = bb.PersonalReminder.query.filter_by(text="Water the plants").first().id
        pills = bb.PersonalReminder.query.filter_by(text="Pick up prescription").first()
        pills.due_date = today
        pills = pills.id
        bb.db.session.commit()
    client.post(f"/reminders/mine/done/{plants}")
    client.post(f"/reminders/mine/done/{pills}")
    with bb.app.app_context():
        assert bb.db.session.get(bb.PersonalReminder, plants).due_date == \
            today + datetime.timedelta(days=7)
        assert bb.db.session.get(bb.PersonalReminder, pills) is None
    html = client.get("/").get_data(as_text=True)
    assert "Water the plants" not in html and "Pick up prescription" not in html
    check("Got it moves a weekly one on a week, and finishes a one-off")

    r = bb.PersonalReminder(text="Rent", repeat="monthly", day=31,
                            due_date=datetime.date(2026, 1, 31))
    r.due_date = bb.next_due(r, datetime.date(2026, 1, 31))
    assert r.due_date == datetime.date(2026, 2, 28)
    assert bb.next_due(r, datetime.date(2026, 2, 28)) == datetime.date(2026, 3, 31)
    check("monthly keeps its day: the 31st is the 28th in Feb, back to the 31st after")

    with bb.app.app_context():
        count = bb.PersonalReminder.query.count()
    client.post("/reminders/mine", data={"text": "", "due_date": "nope", "repeat": "none"})
    client.post("/reminders/mine", data={"text": "x", "due_date": today.isoformat(),
                                         "repeat": "hourly"})
    with bb.app.app_context():
        assert bb.PersonalReminder.query.count() == count
    other = bb.app.test_client()
    other.post("/register", data={"username": "nosy", "email": "nosy@t.local",
                                  "password": "pw123456", "confirm": "pw123456"})
    assert other.post(f"/reminders/mine/delete/{plants}").status_code == 404
    check("bad input is refused, and nobody can touch someone else's")


def test_a_batch_of_emails_shares_one_login():
    """ #12 - one mail server login per job, not one per email.
    #18 - a failed email is logged, not printed.
    A fake server stands in, and SMTP_HOST points nowhere real, so nothing
    can reach an actual mail server even if the fake were missed """
    import logging
    client = fresh()
    add_bill(client, "Rent", 500, day=5)
    for name in ("amy", "ben"):
        c = bb.app.test_client()
        c.post("/register", data={"username": name, "email": f"{name}@t.local",
                                  "password": "pw123456", "confirm": "pw123456"})
        add_bill(c, f"{name} rent", 500, day=5)

    logins, sent, logged = [], [], []

    class FakeServer:
        def send_message(self, msg):
            sent.append(msg["To"])

        def quit(self):
            pass

    class Catch(logging.Handler):
        def emit(self, record):
            logged.append(record.getMessage())

    keys = ("EMAIL_ADDRESS", "EMAIL_APP_PASSWORD", "SMTP_HOST")
    saved_env = {k: os.environ.get(k) for k in keys}
    real_open, handler = bb.open_smtp, Catch()
    os.environ.update(EMAIL_ADDRESS="bb@t.local", EMAIL_APP_PASSWORD="x",
                      SMTP_HOST="invalid.invalid")
    bb.app.logger.addHandler(handler)
    bb.app.logger.propagate = False
    bb.app.config["TESTING"] = False
    try:
        bb.open_smtp = lambda: logins.append(1) or FakeServer()
        bb.create_monthly_reminders()
        assert sorted(sent) == ["amy@t.local", "ben@t.local", EMAIL], sent
        assert len(logins) == 1, f"{len(logins)} logins for one batch"

        class Flaky(FakeServer):
            calls = 0

            def send_message(self, msg):
                Flaky.calls += 1
                if Flaky.calls == 1:
                    raise bb.smtplib.SMTPServerDisconnected("dropped")
                if msg["To"].startswith("ben"):
                    raise bb.smtplib.SMTPRecipientsRefused({})
                sent.append(msg["To"])

        sent.clear(); logins.clear()
        bb.open_smtp = lambda: logins.append(1) or Flaky()
        bb.create_weekly_reminder()
    finally:
        bb.app.config["TESTING"] = True
        bb.open_smtp = real_open
        bb.app.logger.removeHandler(handler)
        bb.app.logger.propagate = True
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("a whole job's emails share one mail server login")
    assert sorted(sent) == ["amy@t.local", EMAIL], sent
    assert len(logins) == 2, "a dropped connection logs in again, once"
    check("a dropped connection reconnects and the email still goes")
    assert logged == ["Email failed for ben@t.local"], logged
    check("a failed email is logged, and everyone else still gets theirs")


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
    client.post(f"/pay/{bill}")
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

    assert "main.dataset.swapped" in html, \
        "swapped content must be flagged so entry animations don't replay"
    #the flag has to be on the incoming page too, or a block that gets
    #replaced outright arrives without it and animates in
    assert "newMain.dataset.swapped" in html
    check("a swapped page does not replay its entry animations")

    #only the parts that changed are touched, so the page doesn't blink
    assert "function morph" in html and "isEqualNode" in html, \
        "an unchanged element must be left alone, not replaced"
    assert "main.innerHTML = newMain.innerHTML" not in html, \
        "replacing all of main is what made a click feel like a page reload"
    check("only the changed parts of the page are swapped")

    #the POST must not quietly load the page it redirects to
    assert 'redirect: "manual"' in html, \
        "following the redirect renders the page twice and eats the flash"
    check("one round trip per click, not two")

    #the flash message survives to the page the user actually sees
    bill = add_bill(client, "Rent", 500, day=5)
    client.post(f"/pay/{bill}")                 # redirect NOT followed
    assert "is paid" in client.get("/").get_data(as_text=True), \
        "the confirmation was being swallowed by the hidden redirect"
    check("the confirmation message reaches the user")

    #the flash space is always in the page, so nothing shifts when one appears
    assert "flash-area" in client.get("/").get_data(as_text=True)
    check("a flash appearing doesn't shift the whole page down")

    #dragging is delegated, so a bill swapped in by a refresh still drags
    dragging = client.get("/?sort=custom").get_data(as_text=True)
    assert "billDragBound" in dragging and "closest('.bill')" in dragging, \
        "per-bill listeners are lost when a bill is swapped in"
    check("drag and drop survives a soft refresh")


def test_a_weekly_bill_never_keeps_a_day_of_the_month():
    """ A weekly loan carrying "the 15th" used to raise IndexError deep in
    due_phrase, which killed the reminder jobs for EVERY user """
    client = fresh()

    #the form can't save a weekday of 15 any more
    bill = add_bill(client, "Car loan", 250, day=15, kind="loan", freq="weekly",
                    total_value=50000, current_balance=30000)
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        assert p.due_day == 7, f"a weekly day must be 1-7, got {p.due_day}"
    #and switching a monthly bill over re-reads the day the same way
    monthly = add_bill(client, "Gym", 300, day=28)
    client.post(f"/edit/{monthly}", data={
        "name": "Gym", "description": "", "amount": "300", "due_day": "28",
        "bill_type": "fixed", "frequency": "weekly"}, follow_redirects=True)
    with bb.app.app_context():
        assert bb.db.session.get(bb.Payment, monthly).due_day == 7
    check("a weekly bill is saved with a weekday, never a day of the month")

    #the money fields must really save. writing the old rands names
    #(payment.amount ...) is thrown away silently, page still says "updated"
    loan = add_bill(client, "Car", 250, day=10, kind="loan",
                    total_value=50000, current_balance=30000)
    client.post(f"/edit/{loan}", data={
        "name": "Car", "description": "", "amount": "349.50", "due_day": "10",
        "bill_type": "loan", "frequency": "monthly",
        "total_value": "60000", "current_balance": "27500",
        "service_fee": "69", "loan_insurance": "45.25",
        "initiation_fee": "1207.50"}, follow_redirects=True)
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, loan)
        assert p.amount_cents == 34950, f"amount not saved: {p.amount_cents}"
        assert p.total_value_cents == 6000000, p.total_value_cents
        assert p.current_balance_cents == 2750000, p.current_balance_cents
        assert p.service_fee_cents == 6900, p.service_fee_cents
        assert p.loan_insurance_cents == 4525, p.loan_insurance_cents
        assert p.initiation_fee_cents == 120750, p.initiation_fee_cents
    check("editing a bill saves the amount, balances and fees")

    #a rate is a plain percentage - money() would divide it by 100
    assert bb.rate_text(11.5) == "11.5" and bb.rate_text(11.0) == "11"
    assert bb.rate_text(22.25) == "22.25" and bb.rate_text(None) == ""
    rated = add_bill(client, "Bond", 4500, day=1, kind="loan",
                     total_value=900000, current_balance=750000,
                     interest_rate=11.5)
    html = client.get("/").get_data(as_text=True)
    assert "11.5%" in html, "an 11.5% rate must read 11.5%"
    assert "0.12%" not in html, "money() would have shown 0.12%"
    check("interest rates show as a percentage, not divided by 100")

    #bills saved before that clamp existed still must not crash anything
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        p.due_day = 15                     # straight into the database
        bb.db.session.commit()
        assert bb.due_phrase(p) in list(__import__("calendar").day_name)
        assert bb.get_status(p) in ("paid", "partial", "overdue", "soon", "upcoming")
    assert client.get("/").status_code == 200
    bb.create_weekly_reminder()
    bb.create_monthly_reminders()
    with bb.app.app_context():
        assert bb.Reminder.query.count() > 0
    check("an old bill with a bad day still reads, and the jobs still run")


def test_one_bad_account_cannot_stop_everyone_elses_reminders():
    client = fresh()                        # a normal user
    add_bill(client, "Rent", 500, day=5)
    other = bb.app.test_client()
    other.post("/register", data={"username": "bob", "email": "bob@t.local",
                                  "password": "pw123456", "confirm": "pw123456"},
               follow_redirects=True)
    other.post("/add", data={"name": "Boom", "description": "", "amount": "100",
                             "due_day": "1", "bill_type": "fixed",
                             "frequency": "monthly"}, follow_redirects=True)

    #make one account's reminders blow up, the way bad data used to
    original = bb.monthly_message

    def explode(payment, user):
        if payment.name == "Boom":
            raise ValueError("bad data on this account")
        return original(payment, user)

    bb.monthly_message = explode
    try:
        bb.create_monthly_reminders()       # must not raise
    finally:
        bb.monthly_message = original

    with bb.app.app_context():
        messages = [r.message for r in bb.Reminder.query.all()]
    assert any("Rent" in m for m in messages), \
        "the healthy account must still get its reminders"
    assert not any("Boom" in m for m in messages), \
        "the broken account is rolled back, not half written"
    check("a broken account is skipped, everyone else still gets reminded")


def test_a_page_costs_the_same_however_many_bills():
    """ The buddy sits on every page. It used to run a SUM per weekly loan,
    so even Settings got slower with every loan added """
    def queries_for(url, loans):
        client = fresh()
        for i in range(loans):
            add_bill(client, f"Loan {i}", 250, day=(i % 7) + 1, kind="loan",
                     freq="weekly", total_value=50000, current_balance=30000)
        client.get(url)                      # warm up
        with counting_queries() as counter:
            client.get(url)
        return counter.n

    for url in ("/", "/settings", "/reminders"):
        few, many = queries_for(url, 2), queries_for(url, 20)
        assert many <= few, f"{url}: {few} queries with 2 loans, {many} with 20"
        assert many < 12, f"{url} runs {many} queries"
        check(f"{url:<11} {many} queries with 20 loans, same as with 2")


def test_pages_without_bills_reuse_the_buddys_mood():
    """ #10 - Settings, Reminders and History used to load every bill just to
    pick the buddy's speech bubble. The mood is remembered for the day now,
    and forgotten the moment anything changes """
    from sqlalchemy import event, engine
    client = fresh()
    bill = add_bill(client, "Rent", 500, day=5)
    client.get("/")
    seen = []

    def tick(conn, cursor, statement, *rest):
        seen.append(statement)

    event.listen(engine.Engine, "after_cursor_execute", tick)
    try:
        for page in ("/settings", "/reminders"):
            client.get(page)
    finally:
        event.remove(engine.Engine, "after_cursor_execute", tick)
    assert not any(re.search(r"FROM payment\b", s) for s in seen), \
        "the bills were loaded again for the buddy"
    check("Settings and Reminders don't load the bills for the buddy")

    client.post(f"/pay/{bill}")
    assert "buddy-mood-happy" in client.get("/settings").get_data(as_text=True), \
        "paying must change the mood straight away, not tomorrow"
    check("paying a bill changes the mood on the very next page")


def test_the_month_end_costs_the_same_however_many_bills():
    """ #72 - closing off the month totalled last month one bill at a time """
    from sqlalchemy import event, engine

    def selects(bills):
        client = fresh()
        for i in range(bills):
            add_bill(client, f"W{i}", 100, day=(i % 7) + 1, freq="weekly")
        seen = []

        def tick(conn, cursor, statement, *rest):
            if statement.lstrip().upper().startswith("SELECT"):
                seen.append(statement)

        event.listen(engine.Engine, "after_cursor_execute", tick)
        try:
            bb.create_monthly_reminders()
        finally:
            event.remove(engine.Engine, "after_cursor_execute", tick)
        return len(seen)

    few, many = selects(2), selects(20)
    assert many == few, f"{few} selects with 2 weekly bills, {many} with 20"
    check(f"the month end is {many} selects with 20 weekly bills, same as with 2")


def test_history_is_paged_and_cheap():
    client = fresh()
    bill = add_bill(client, "Rent", 500, day=5)
    with bb.app.app_context():
        uid = bb.User.query.first().id
        for i in range(250):
            bb.db.session.add(bb.PaymentLog(
                bill_name="Rent", amount_paid_cents=1000, payment_id=bill,
                user_id=uid, paid_at=bb.local_now() - datetime.timedelta(hours=i * 5)))
        bb.db.session.commit()

    client.get("/history")
    with counting_queries() as counter:
        html = client.get("/history").get_data(as_text=True)
    assert counter.n < 8, f"/history runs {counter.n} queries"
    check(f"/history is {counter.n} queries, chart included, not one per month")

    assert html.count("<tr>") <= bb.HISTORY_PER_PAGE + 6, "the page must be capped"
    assert "Older" in html, "there is more history, so there must be a way to it"
    page2 = client.get("/history?page=2").get_data(as_text=True)
    assert "Newer" in page2 and "<tr>" in page2
    assert client.get("/history?page=99").status_code == 200, "past the end is not an error"
    check(f"{bb.HISTORY_PER_PAGE} rows a page, with older and newer links")

    #the chart still adds up, even though it no longer loads every row
    with bb.app.app_context():
        this_month = bb.paid_in_month(bb.db.session.get(bb.Payment, bill))
    assert f'{this_month // 100}' in html or this_month == 0
    check("the six month chart still totals from one grouped query")


def test_history_shows_each_bills_month_at_a_glance():
    """ #63 - red / amber / green per bill, per month, on the history page """
    client = fresh()
    rent = add_bill(client, "Rent", 500, day=1)
    water = add_bill(client, "Water", 400, day=1, kind="variable")
    add_bill(client, "Gym", 300, day=28)
    fridge = add_bill(client, "Fridge", 800, day=5, kind="once_off")
    with bb.app.app_context():
        year, month = bb.previous_month()
        uid = bb.User.query.first().id
        for p in bb.Payment.query.all():
            p.date_added = datetime.datetime(year, month, 1)
        for pid, name, cents in ((rent, "Rent", 50000), (water, "Water", 10000),
                                 (fridge, "Fridge", 80000)):
            bb.db.session.add(bb.PaymentLog(bill_name=name, amount_paid_cents=cents,
                                            payment_id=pid, user_id=uid,
                                            paid_at=datetime.datetime(year, month, 10)))
        f = bb.db.session.get(bb.Payment, fridge)
        f.is_paid, f.is_archived = True, True
        bb.db.session.commit()
    add_bill(client, "New sub", 50, day=3)
    client.post(f"/pay/{rent}")

    html = client.get("/history").get_data(as_text=True)
    this_month, last_month = html.split('class="history-month"')[1:3]

    def chips(section):
        return {name.strip(): status for status, name in re.findall(
            r'badge-(\w+)"[^>]*>\s*(?:✓ |✕ )?([^<]+?)\s*</span>', section)}

    last = chips(last_month)
    assert last == {"Rent": "paid", "Water": "partial", "Gym": "overdue",
                    "Fridge": "paid"}, last
    check("last month: paid green, part paid amber, nothing paid red")
    assert "New sub" not in last, "a bill added later isn't in older months"
    check("bills only show from the month they were added")

    now = chips(this_month)
    assert now["Rent"] == "paid" and "Fridge" not in now, now
    assert now["Water"] in ("overdue", "upcoming") and now["New sub"] in ("overdue", "upcoming")
    check("this month: live status, archived once-offs gone")


def test_reordering_is_one_query_not_thirty():
    client = fresh()
    ids = [add_bill(client, f"B{i}", 100, day=(i % 27) + 1) for i in range(30)]
    client.get("/?sort=custom")
    with counting_queries() as counter:
        client.post("/reorder", json=list(reversed(ids)))
    assert counter.n < 6, f"a drag cost {counter.n} queries for 30 bills"
    with bb.app.app_context():
        order = {p.id: p.sort_order for p in bb.Payment.query.all()}
    assert order[ids[-1]] == 0 and order[ids[0]] == 29, "the new order must stick"
    check(f"a drag with 30 bills is {counter.n} queries, and still saves the order")


def test_the_buddy_holds_its_tongue():
    """ The speech bubble re-rolled its message on every single page load,
    so it changed and re-popped on every click """
    client = fresh()
    bill = add_bill(client, "Rent", 500, day=5)

    def bubble():
        html = client.get("/").get_data(as_text=True)
        return re.search(r'class="buddy-bubble">(.*?)</div>', html, re.S).group(1).strip()

    said = {bubble() for _ in range(10)}
    assert len(said) == 1, f"the buddy changed its line {len(said)} times doing nothing"
    check("the buddy says the same thing until something changes")

    before = bubble()
    client.post(f"/pay/{bill}")
    assert bubble() != before, "paying the last bill should cheer it up"
    check("it does speak up when the bills actually change")


def test_the_page_paints_without_waiting():
    import re
    client = fresh()
    html = client.get("/").get_data(as_text=True)
    #google fonts used to block the first paint entirely
    assert 'media="print"' in html, "the webfont must not block the first paint"
    assert "<noscript>" in html, "and must still load without javascript"
    check("webfonts load without blocking the first paint")

    #a year-long cache is only safe if the url changes when the file does
    url = re.search(r'href="(/static/style\.css[^"]*)"', html).group(1)
    assert re.search(r"\?v=\d+", url), "the stylesheet url must carry a version"
    assert "max-age=31536000" in client.get(url).headers.get("Cache-Control", "")
    check("stylesheet cached for a year, busted by a version stamp")


def test_a_bad_weekly_day_does_not_break_the_page():
    #a bill switched from monthly to weekly can still hold a day of the month
    client = fresh()
    with bb.app.app_context():
        uid = bb.User.query.first().id
        bb.db.session.add(bb.Payment(name="Switched", amount_cents=5000,
                                     due_day=28, frequency="weekly", user_id=uid))
        bb.db.session.commit()
    assert client.get("/").status_code == 200, "day 28 on a weekly bill must not 500"
    check("an out-of-range weekly day is clamped, not crashed on")


# ----------- audit fixes
def test_bad_numbers_are_turned_away():
    client = fresh()
    for raw in ("abc", "12.5.6", "R199", "nan", "inf", ""):
        r = client.post("/add", data={"name": f"Bad {raw}", "description": "",
                                      "amount": raw, "due_day": "5",
                                      "bill_type": "fixed", "frequency": "monthly"})
        assert r.status_code == 302, f"{raw!r} gave {r.status_code}"
    with bb.app.app_context():
        assert bb.Payment.query.count() == 0, "a bad amount must not save a bill"
    assert "look like a number" in client.get("/").get_data(as_text=True)
    check("junk in a money box is a message, not a 500")

    bill = add_bill(client, "Rent", 500, day=5)
    client.post(f"/edit/{bill}", data={
        "name": "Renamed", "description": "", "amount": "R199", "due_day": "5",
        "bill_type": "fixed", "frequency": "monthly"})
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, bill)
        assert p.amount_cents == 50000 and p.name == "Rent", "a failed edit changes nothing"
    check("a failed edit is rolled back whole")

    for url, data in ((f"/partial_pay/{bill}", {"paid_amount": "12.5.6"}),
                      (f"/bill/confirm/{bill}", {"new_amount": "abc"}),
                      (f"/update_balance/{bill}", {"new_balance": "abc"}),
                      ("/settings", {"currency": "R", "theme": "pastel",
                                     "budget_limit": "lots"}),
                      ("/add", {"name": "Car", "description": "", "amount": "300",
                                "due_day": "5", "bill_type": "loan",
                                "frequency": "monthly", "months_remaining": "2.5"})):
        assert client.post(url, data=data).status_code == 302, url
    with bb.app.app_context():
        assert bb.Payment.query.filter_by(name="Car").first() is None
    assert bb.parse_whole("12") == 12 and bb.parse_whole("") is None
    assert bb.parse_cents("199,99") == 19999, "comma decimals still work"
    check("every money, rate and months box refuses junk the same way")


def test_passwords_need_eight_characters():
    with bb.app.app_context():
        bb.db.drop_all()
        bb.db.create_all()
    c = bb.app.test_client()
    html = c.post("/register", data={"username": "shorty", "email": "s@t.local",
                                     "password": "short", "confirm": "short"},
                  follow_redirects=True).get_data(as_text=True)
    assert "at least 8 characters" in html
    with bb.app.app_context():
        assert bb.User.query.count() == 0
    check("a 5 character password can't make an account")

    client = fresh()
    client.get("/logout")
    with bb.app.app_context():
        uid = bb.User.query.first().id
    link = f"/reset/{bb.get_reset_serializer().dumps(uid)}"
    html = client.post(link, data={"password": "tiny", "confirm": "tiny"},
                       follow_redirects=True).get_data(as_text=True)
    assert "at least 8 characters" in html
    with bb.app.app_context():
        assert bb.User.query.first().check_password("pw123456"), "unchanged"
    check("nor can a password reset set one")


def test_two_people_can_share_a_name():
    """ #49 - David#0001 and David#0002, told apart by a tag """
    with bb.app.app_context():
        bb.db.drop_all()
        bb.db.create_all()
    for email in ("a@t.local", "b@t.local"):
        html = bb.app.test_client().post("/register", data={
            "username": "David", "email": email, "password": "pw123456",
            "confirm": "pw123456"}, follow_redirects=True).get_data(as_text=True)
    assert "David#0002" in html, "the welcome should say which David you are"
    with bb.app.app_context():
        handles = sorted((u.email, u.handle) for u in bb.User.query.all())
    assert handles == [("a@t.local", "David#0001"), ("b@t.local", "David#0002")], handles
    check("a second David is David#0002, not turned away")

    def log_in(name):
        c = bb.app.test_client()
        html = c.post("/login", data={"username": name, "password": "pw123456"},
                      follow_redirects=True).get_data(as_text=True)
        return c, html

    c, html = log_in("David#0002")
    assert "Welcome back" in html and "David#0002" in c.get("/settings").get_data(as_text=True)
    assert "Welcome back" in log_in("B@T.local")[1], "email login ignores case"
    assert "More than one account is called David" in log_in("David")[1]
    assert "Wrong username or password" in log_in("David#0009")[1]
    check("log in with the email or David#0002, a shared plain name is explained")

    html = bb.app.test_client().post("/register", data={
        "username": "Da#vid", "email": "c@t.local", "password": "pw123456",
        "confirm": "pw123456"}, follow_redirects=True).get_data(as_text=True)
    assert "contain #" in html
    with bb.app.app_context():
        bb.db.session.add(bb.User(username="David", tag=1, email="d@t.local",
                                  password_hash="x"))
        try:
            bb.db.session.commit()
            raise AssertionError("a duplicate handle was saved")
        except bb.IntegrityError:
            bb.db.session.rollback()
    check("the database itself refuses a duplicate handle")


def test_a_missing_archive_flag_still_shows_the_bill():
    client = fresh()
    bill = add_bill(client, "Wifi", 500, day=5)
    with bb.app.app_context():
        bb.db.session.get(bb.Payment, bill).is_archived = None
        bb.db.session.commit()
    assert f'data-id="{bill}"' in client.get("/").get_data(as_text=True), \
        "NULL != 1 is NULL in SQL, which hid the bill"
    bb.create_monthly_reminders()
    with bb.app.app_context():
        assert any("Wifi" in r.message for r in bb.Reminder.query.all())
    check("a NULL is_archived bill stays on the dashboard and in the jobs")


def test_the_daily_task_never_runs_twice():
    client = fresh()
    loan = add_bill(client, "Car", 2000, day=5, kind="loan", total_value=100000,
                    current_balance=50000, interest_rate=12, service_fee=69)
    os.environ["TASK_TOKEN"] = "t0ken"
    real_now = bb.local_now
    #the 1st, a Thursday
    bb.local_now = lambda: datetime.datetime(2026, 10, 1, 9, 0)
    try:
        runner = bb.app.test_client()
        first = runner.get("/tasks/run-daily?token=t0ken").get_data(as_text=True)
        with bb.app.app_context():
            balance = bb.db.session.get(bb.Payment, loan).current_balance_cents
            count = bb.Reminder.query.count()
        #R50 000 + 1% interest + R69 fee
        assert balance == 5000000 + 50000 + 6900, balance
        assert "monthly" in first

        late = bb.app.test_client()
        late.post("/register", data={"username": "late", "email": "late@t.local",
                                     "password": "pw123456", "confirm": "pw123456"})
        late.post("/add", data={"name": "Late bill", "description": "", "amount": "10",
                                "due_day": "9", "bill_type": "fixed",
                                "frequency": "monthly"})
        runner.get("/tasks/run-daily?token=t0ken")
        with bb.app.app_context():
            assert bb.db.session.get(bb.Payment, loan).current_balance_cents == balance, \
                "a second run added the interest again"
            messages = [r.message for r in bb.Reminder.query.all()]
            assert sum("'Car'" in m for m in messages) == \
                sum("'Car'" in m for m in messages[:count]), "reminders duplicated"
            assert any("Late bill" in m for m in messages), "the new user was skipped"
    finally:
        bb.local_now = real_now
        os.environ.pop("TASK_TOKEN")
    check("a second run the same month adds nothing twice")
    check("but still picks up anyone the first run missed")


def test_the_clock_is_south_african():
    utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    gap = bb.local_now() - utc
    assert abs(gap - datetime.timedelta(hours=2)) < datetime.timedelta(seconds=5), gap
    client = fresh()
    bill = add_bill(client, "Rent", 500, day=5)
    client.post(f"/pay/{bill}")
    with bb.app.app_context():
        paid_at = bb.PaymentLog.query.first().paid_at
    assert abs(paid_at - bb.local_now()) < datetime.timedelta(seconds=5)
    check("dates and stored times are SAST, whatever the server's clock says")


def test_the_history_chart_reads_in_rands():
    client = fresh()
    bill = add_bill(client, "Rent", 416.70, day=5)
    client.post(f"/pay/{bill}")
    html = client.get("/history").get_data(as_text=True)
    assert 'class="chart-value">R417<' in html, "R416.70 rounds to R417 on the bar"
    assert "R41670" not in html, "cents were printed as rands"
    check("the chart label is R417, not R41670")


def test_a_fully_paid_weekly_loan_keeps_the_buddy_happy():
    client = fresh()
    loan = add_bill(client, "Weekly loan", 250, day=1, kind="loan", freq="weekly",
                    total_value=60000, current_balance=42000)
    with bb.app.app_context():
        weeks = bb.weeks_in_month(bb.db.session.get(bb.Payment, loan))
    client.post(f"/week/{loan}/{weeks}")
    bb.create_weekly_reminder()
    with bb.app.app_context():
        p = bb.db.session.get(bb.Payment, loan)
        assert p.is_paid and bb.get_status(p) == "paid", "Monday no longer resets it"
        #old flag
        p.is_paid = False
        bb.db.session.commit()
    assert "buddy-mood-happy" in client.get("/").get_data(as_text=True), \
        "the buddy must follow the status, not is_paid"
    check("a loan paid for the month keeps the buddy happy after Monday")


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
