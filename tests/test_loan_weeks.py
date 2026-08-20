import sys

sys.path.insert(0, r"c:\Users\trist\Desktop\Coding\Budget Buddy")
import app as bb

bb.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
del bb.app.extensions["sqlalchemy"]
bb.db.init_app(bb.app)
with bb.app.app_context():
    bb.db.create_all()

bb.app.config["TESTING"] = True
client = bb.app.test_client()

client.post("/register", data={
    "username": "borrower", "email": "loan@test.local",
    "password": "pw12345", "confirm": "pw12345",
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
assert paid == 0 and left == 250 * weeks and not is_paid
print(f"new weekly loan -> 0 of {weeks} weeks, R{left:.2f} owed this month")

html = client.get("/").get_data(as_text=True)
assert "week-toggles" in html and html.count("week-box") >= weeks, "week boxes missing"
assert f"/pay/{loan_id}" not in html, "a weekly loan should not offer Mark paid"
print(f"dashboard -> {weeks} week boxes, no Mark paid button")

client.post(f"/week/{loan_id}/1")
paid, left, is_paid, xp1, coins1 = state()
assert paid == 1 and left == 250 * (weeks - 1), (paid, left)
assert xp1 == xp0 + 15 and coins1 == coins0 + 15, "ticking a week should earn 15 xp"
assert not is_paid, "one week does not finish the month"
print(f"tick week 1 -> R{left:.2f} left, +15 xp")

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
    assert bb.db.session.get(bb.Payment, loan_id).current_balance == 41000
client.post(f"/update_balance/{loan_id}", data={"new_balance": "40500"})
assert state()[3] == after, "a second update in the same month earns nothing"
with bb.app.app_context():
    assert bb.db.session.get(bb.Payment, loan_id).current_balance == 40500, \
        "the balance must still update even when no xp is given"
print("update balance -> +10 xp once a month, balance always saves")

client.post("/add", data={
    "name": "Phone loan", "description": "", "amount": "80",
    "due_day": "2", "bill_type": "loan", "frequency": "weekly",
    "total_value": "9000", "current_balance": "5000",
}, follow_redirects=True)
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
with bb.app.app_context():
    gid = bb.Payment.query.filter_by(name="Groceries").first().id
html = client.get("/").get_data(as_text=True)
assert f"/pay/{gid}" in html, "ordinary weekly bills keep the Mark paid button"
assert f"/week/{gid}/1" not in html, "week boxes are only for loans"
print("ordinary weekly bill -> unchanged, still uses Mark paid")

print("\nALL LOAN WEEK CHECKS PASSED")


# ---- a weekly loan's "true monthly cost" uses the month, not one week ----
client.post("/add", data={
    "name": "Van loan", "description": "", "amount": "300",
    "due_day": "4", "bill_type": "loan", "frequency": "weekly",
    "total_value": "80000", "current_balance": "50000",
    "service_fee": "60", "loan_insurance": "40",
}, follow_redirects=True)
with bb.app.app_context():
    van = bb.Payment.query.filter_by(name="Van loan").first()
    van_weeks = bb.weeks_in_month(van)
    expected = 300 * van_weeks + 60 + 40
html = client.get("/").get_data(as_text=True)
assert f"True monthly cost: R{expected:.2f}" in html, \
    f"expected R{expected:.2f} (R300 x {van_weeks} weeks + R100 fees)"
assert f"True monthly cost: R{300 + 100:.2f}" not in html, \
    "one week's payment must not be mistaken for the month's"
print(f"weekly loan cost -> R{expected:.2f} = R300 x {van_weeks} weeks + R100 fees")

# ---- a monthly loan is unchanged ----
client.post("/add", data={
    "name": "Bike loan", "description": "", "amount": "900",
    "due_day": "12", "bill_type": "loan", "frequency": "monthly",
    "total_value": "20000", "current_balance": "12000",
    "service_fee": "50", "loan_insurance": "25",
}, follow_redirects=True)
html = client.get("/").get_data(as_text=True)
assert f"True monthly cost: R{900 + 75:.2f}" in html
print("monthly loan cost -> R975.00 = R900 + R75 fees, unchanged")

# ---- both forms offer weekly, and label the amount to match ----
for page in ("/add", f"/edit/{loan_id}"):
    html = client.get(page).get_data(as_text=True)
    assert 'value="weekly"' in html, f"{page} must offer a weekly option"
    assert 'id="amount-label"' in html and "'Weekly' : 'Monthly'" in html, \
        f"{page} must relabel the amount when weekly is chosen"
print("add + edit forms -> weekly option, amount label follows it")

print("\nALL WEEKLY LOAN LABEL CHECKS PASSED")
