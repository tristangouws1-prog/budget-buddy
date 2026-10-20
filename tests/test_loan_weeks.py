import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as bb

bb.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
del bb.app.extensions["sqlalchemy"]
bb.db.init_app(bb.app)
#TESTING keeps send_email off the network entirely
with bb.app.app_context():
    bb.db.create_all()

bb.app.config["TESTING"] = True
#the test client has no browser to carry a csrf token, and the login
#limiter would trip on repeated test logins
bb.app.config["WTF_CSRF_ENABLED"] = False
bb.app.config["RATELIMIT_ENABLED"] = False
bb.limiter.enabled = False
client = bb.app.test_client()


def from_the_1st():
    """ Bills count as added on the 1st, so the whole month is owed """
    with bb.app.app_context():
        today = bb.local_today()
        for p in bb.Payment.query.all():
            p.date_added = datetime.datetime(today.year, today.month, 1)
        bb.db.session.commit()


client.post("/register", data={
    "username": "borrower", "email": "loan@test.local",
    "password": "pw123456", "confirm": "pw123456",
}, follow_redirects=True)
with bb.app.app_context():
    b = bb.Buddy.query.first()
    b.stage = "hatched"
    bb.db.session.commit()

client.post("/add", data={
    "name": "Car loan", "description": "", "amount": "250",
    "due_day": "1", "bill_type": "loan", "frequency": "weekly",
    "total_value": "60000", "current_balance": "42000",
}, follow_redirects=True)
from_the_1st()
with bb.app.app_context():
    loan = bb.Payment.query.first()
    loan_id = loan.id
    weeks = bb.weeks_in_month(loan)


def state():
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.db.session.get(bb.Payment, loan_id)
        buddy = bb.Buddy.query.filter_by(is_active=True).first()
        return (bb.weeks_paid_this_month(p), bb.remaining_this_month(p),
                p.is_paid, buddy.xp, buddy.coins)


paid, left, is_paid, xp0, coins0 = state()
assert paid == 0 and left == 25000 * weeks and not is_paid
print(f"new weekly loan -> 0 of {weeks} weeks, R{left/100:.2f} owed this month")

html = client.get("/").get_data(as_text=True)
assert "week-toggles" in html and html.count("week-box") >= weeks, "week boxes missing"
assert f"/pay/{loan_id}" not in html, "a weekly loan should not offer Mark paid"
print(f"dashboard -> {weeks} week boxes, no Mark paid button")

client.post(f"/week/{loan_id}/1")
paid, left, is_paid, xp1, coins1 = state()
assert paid == 1 and left == 25000 * (weeks - 1), (paid, left)
assert xp1 == xp0 + 15 and coins1 == coins0 + 15, "ticking a week should earn 15 xp"
assert not is_paid, "one week does not finish the month"
print(f"tick week 1 -> R{left/100:.2f} left, +15 xp")

client.post(f"/week/{loan_id}/3")
paid, left, is_paid, xp3, coins3 = state()
assert paid == 3, paid
assert xp3 == xp1 + 30, "weeks 2 and 3 should each earn 15"
print(f"tick week 3 -> {paid} of {weeks} weeks done, +30 xp for the two weeks")

client.post(f"/week/{loan_id}/3")
paid, left, is_paid, xp_back, coins_back = state()
assert paid == 2, f"undo should leave 2 weeks, got {paid}"
assert xp_back == xp3 - 15 and coins_back == coins3 - 15, "undo returns the xp"
print("untick week 3 -> back to 2 weeks, xp taken back")

client.post(f"/week/{loan_id}/3")
paid, left, is_paid, xp_again, _ = state()
assert paid == 3 and xp_again == xp3, "re-ticking should return to the same xp"
for _ in range(4):
    client.post(f"/week/{loan_id}/3")
    client.post(f"/week/{loan_id}/3")
paid, left, is_paid, xp_farm, _ = state()
assert paid == 3 and xp_farm == xp3, f"tick/untick cycles must not farm xp ({xp_farm})"
print("tick/untick repeatedly -> no xp farmed, weeks stay correct")

client.post(f"/week/{loan_id}/{weeks}")
paid, left, is_paid, xp_full, _ = state()
assert paid == weeks and left == 0 and is_paid, (paid, left, is_paid)
print(f"tick all {weeks} weeks -> nothing owed, loan reads as paid")

before = state()[3]
client.post(f"/update_balance/{loan_id}", data={"new_balance": "41000"})
after = state()[3]
assert after == before + 10, f"updating the balance should earn 10 xp, got {after - before}"
with bb.app.app_context():
    assert bb.db.session.get(bb.Payment, loan_id).current_balance_cents == 4100000
client.post(f"/update_balance/{loan_id}", data={"new_balance": "40500"})
assert state()[3] == after, "a second update in the same month earns nothing"
with bb.app.app_context():
    assert bb.db.session.get(bb.Payment, loan_id).current_balance_cents == 4050000, \
        "the balance must still update even when no xp is given"
print("update balance -> +10 xp once a month, balance always saves")

client.post("/add", data={
    "name": "Phone loan", "description": "", "amount": "80",
    "due_day": "2", "bill_type": "loan", "frequency": "weekly",
    "total_value": "9000", "current_balance": "5000",
}, follow_redirects=True)
from_the_1st()
with bb.app.app_context():
    second_id = bb.Payment.query.filter_by(name="Phone loan").first().id
    egg = bb.Buddy.query.filter_by(is_active=True).first()
    egg.stage = "egg"
    egg.xp = 95
    bb.db.session.commit()
client.post(f"/week/{second_id}/1")
with bb.app.app_context():
    egg = bb.Buddy.query.filter_by(is_active=True).first()
    assert egg.xp >= 100 and egg.stage == "hatched", (egg.xp, egg.stage)
print(f"an egg -> receives the xp ({egg.xp}) and hatches from it")

client.post("/add", data={
    "name": "Groceries", "description": "", "amount": "100",
    "due_day": "1", "bill_type": "fixed", "frequency": "weekly",
}, follow_redirects=True)
from_the_1st()
with bb.app.app_context():
    gid = bb.Payment.query.filter_by(name="Groceries").first().id
html = client.get("/").get_data(as_text=True)
assert f"/week/{gid}/1" in html, "ordinary weekly bills get week boxes too (#61)"
assert f"/pay/{gid}" not in html, "and no Mark paid button"
print("ordinary weekly bill -> week boxes too, no Mark paid")

print("\nALL LOAN WEEK CHECKS PASSED")


# ---- a weekly loan's monthly cost uses the month, not one week ----
client.post("/add", data={
    "name": "Van loan", "description": "", "amount": "300",
    "due_day": "4", "bill_type": "loan", "frequency": "weekly",
    "total_value": "80000", "current_balance": "50000",
    "service_fee": "60", "loan_insurance": "40",
}, follow_redirects=True)
from_the_1st()
with bb.app.app_context():
    van = bb.Payment.query.filter_by(name="Van loan").first()
    van_weeks = bb.weeks_in_month(van)
    expected = 300 * van_weeks          # rands, the fees are inside it
html = client.get("/").get_data(as_text=True)
assert f"Monthly cost: R{bb.money(expected * 100)}" in html, \
    f"expected R{expected:.2f} (R300 x {van_weeks} weeks)"
assert f"Monthly cost: R{300:.2f}" not in html, \
    "one week's payment must not be mistaken for the month's"
print(f"weekly loan cost -> R{expected:.2f} = R300 x {van_weeks} weeks, fees included")

# ---- a monthly loan is unchanged ----
client.post("/add", data={
    "name": "Bike loan", "description": "", "amount": "900",
    "due_day": "12", "bill_type": "loan", "frequency": "monthly",
    "total_value": "20000", "current_balance": "12000",
    "service_fee": "50", "loan_insurance": "25",
}, follow_redirects=True)
from_the_1st()
html = client.get("/").get_data(as_text=True)
assert f"Monthly cost: R{bb.money(90000)}" in html
assert f"R{900 + 75:.2f}" not in html, "the fees are already in the instalment"
print("monthly loan cost -> R900.00, the R75 fees not added twice")

# ---- both forms offer weekly, and label the amount to match ----
for page in ("/add", f"/edit/{loan_id}"):
    html = client.get(page).get_data(as_text=True)
    assert 'value="weekly"' in html, f"{page} must offer a weekly option"
    assert 'id="amount-label"' in html and "'Weekly' : 'Monthly'" in html, \
        f"{page} must relabel the amount when weekly is chosen"
print("add + edit forms -> weekly option, amount label follows it")

print("\nALL WEEKLY LOAN LABEL CHECKS PASSED")


# ---- every week ticks visibly, whatever the amount ----
# 208.35 // 41.67 is 4 in floating point, so week 5 used to stay unticked
for weekly_amount in ("41.67", "333.33", "1666.67", "99.99", "7.77"):
    with bb.app.app_context():
        bb.db.drop_all()
        bb.db.create_all()
    client = bb.app.test_client()
    client.post("/register", data={"username": "t", "email": "t@t.local",
                                   "password": "pw123456", "confirm": "pw123456"},
                follow_redirects=True)
    client.post("/add", data={"name": "Loan", "description": "", "amount": weekly_amount,
                              "due_day": "1", "bill_type": "loan", "frequency": "weekly",
                              "total_value": "60000", "current_balance": "42000"},
                follow_redirects=True)
    from_the_1st()
    with bb.app.app_context():
        p = bb.Payment.query.first()
        pid, n_weeks = p.id, bb.weeks_in_month(p)
    for w in range(1, n_weeks + 1):
        client.post(f"/week/{pid}/{w}")
        ticked = client.get("/").get_data(as_text=True).count("week-box-done")
        assert ticked == w, f"R{weekly_amount}: clicked week {w} but {ticked} show ticked"
    with bb.app.app_context():
        bb.db.session.expire_all()
        assert bb.remaining_this_month(bb.db.session.get(bb.Payment, pid)) == 0
print("every week ticks visibly for awkward amounts, and clears the month")

print("\nALL WEEK COUNTING CHECKS PASSED")


# ---- paying a loan brings its balance down, undo puts it back ----
with bb.app.app_context():
    bb.db.drop_all()
    bb.db.create_all()
client = bb.app.test_client()
client.post("/register", data={"username": "payer", "email": "payer@test.local",
                               "password": "pw123456", "confirm": "pw123456"},
            follow_redirects=True)
client.post("/add", data={
    "name": "Car", "amount": "1000", "due_day": "5",
    "bill_type": "loan", "frequency": "monthly",
    "total_value": "20000", "current_balance": "10000", "interest_rate": "12",
    "service_fee": "50", "loan_insurance": "50",
}, follow_redirects=True)
client.post("/add", data={
    "name": "Bike", "amount": "100", "due_day": "1",
    "bill_type": "loan", "frequency": "weekly",
    "total_value": "5000", "current_balance": "3000",
}, follow_redirects=True)
from_the_1st()


def loan(name):
    with bb.app.app_context():
        bb.db.session.expire_all()
        p = bb.Payment.query.filter_by(name=name).first()
        bb.db.session.expunge(p)
        return p


car_id, bike_id = loan("Car").id, loan("Bike").id
client.post(f"/pay/{car_id}")
assert loan("Car").current_balance_cents == 900000, loan("Car").current_balance_cents
client.post(f"/unpaid/{car_id}")
assert loan("Car").current_balance_cents == 1000000, "undo must put the balance back"
print("pay a loan -> R10000.00 down to R9000.00, undo -> back to R10000.00")

client.post(f"/week/{bike_id}/1")
assert loan("Bike").current_balance_cents == 290000, loan("Bike").current_balance_cents
client.post(f"/week/{bike_id}/1")
assert loan("Bike").current_balance_cents == 300000, "unticking a week puts it back"
print("tick a week -> R3000.00 down to R2900.00, untick -> back to R3000.00")

# ---- months to pay off are worked out, not typed ----
car = loan("Car")
# R900 after fees
assert bb.months_to_pay_off(car) == 12, bb.months_to_pay_off(car)
balance, months = car.current_balance_cents, 0
while balance > 0:
    balance += bb.percent_of(balance, 1) + 10000 - 100000
    months += 1
assert months == 12, "the formula must agree with the month-end job, month by month"
assert "About 12 months left to pay off" in client.get("/").get_data(as_text=True)
print("R10000.00 at 12%, R900.00 a month after fees -> 12 months, same as month by month")

# R433.33 a month
assert bb.months_to_pay_off(loan("Bike")) == 7, bb.months_to_pay_off(loan("Bike"))
print("R3000.00 at 0%, R100.00 a week -> 7 months")

with bb.app.app_context():
    p = bb.db.session.get(bb.Payment, car_id)
    p.amount_cents = 15000          # below interest
    bb.db.session.commit()
assert bb.months_to_pay_off(loan("Car")) is None
assert "won't go down" in client.get("/").get_data(as_text=True)
print("instalment below the interest and fees -> warned it never pays off")

with bb.app.app_context():
    bb.db.session.get(bb.Payment, car_id).current_balance_cents = 0
    bb.db.session.commit()
assert bb.months_to_pay_off(loan("Car")) == 0
print("paid off -> 0 months")

print("\nALL LOAN BALANCE CHECKS PASSED")
