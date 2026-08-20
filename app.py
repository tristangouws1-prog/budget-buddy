
"""
#------------------------------------------------------------------------------#
#-------Budget Buddy = Budgeting + Reminder App Built with Flask & Python------#
#------------------------------------------------------------------------------#

What this app does
    - each person makes their own account (login + password) so their bills are private
    - add monthly bills / subscriptions
    - once a week the app sends a gentle reminder asking if the user has any new bills or subscriptions to add
    - once a week sends a reminder about upcoming payments and overdue bills
    - once a month send a reminder of all the bills and reset the previous months budget sheet
    - which means that if a bill was marked as paid in june the new page saying july will mark everything as not paid
    - keeps a permanent history of every payment, shown as a table and a six month spending chart
    - lets each user pick a colour theme, including a dark mode
    - sends the weekly and monthly reminders by email as well as showing them in the app
    """

#imports
import os
import secrets

import smtplib
from email.message import EmailMessage

from pathlib import Path
from dotenv import load_dotenv

#load .env from THIS file's folder, not the folder the app was started from
#(a web server can start it anywhere, and then .env is silently never found)
load_dotenv(Path(__file__).with_name(".env"))

#signed expiring tokens for the password reset links, comes with Flask
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import datetime
import calendar
import random

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-fallback")

#locally the database sits in the instance folder. on a host set DATABASE_URL
#to an absolute path (4 slashes = absolute, 3 = relative)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///budget.db")

db = SQLAlchemy(app)

#Flask-Login keeps track of who is logged in 
login_manager = LoginManager(app)
#if a not-logged-in person visits a protected page, send them to the login page
login_manager.login_view = "login"
login_manager.login_message = "Please log in to see your bills."

login_manager.login_message_category = "warning"



#------------------------------------------------------------------------------#
#--------------------Setting up Python Classes / Database models---------------#
#------------------------------------------------------------------------------#

class User(db.Model, UserMixin):
    """One person's account."""

    id = db.Column(db.Integer, primary_key=True)

#the username
    username = db.Column(db.String(80), unique=False, nullable=False)

#NEVER stores the real password
    password_hash = db.Column(db.String(255), nullable=False)

    #symbol shown before every money amount (e.g. R, $, €, £)
    currency = db.Column(db.String(5), nullable=False, default="R")

    #current theme
    theme = db.Column(db.String(20), nullable=False, default="pastel")

    #email reminder address (required - every account must have one)
    email = db.Column(db.String(120), unique=True, nullable=False)

    #reminders are emailed by default; users can opt out in settings
    email_reminders = db.Column(db.Boolean, default=True)

    budget_limit = db.Column(db.Float, nullable=True)

#a user's bills and reminders.
    payments = db.relationship("Payment", backref="user", lazy=True)
    reminders = db.relationship("Reminder", backref="user", lazy=True)
    incomes = db.relationship("Income", backref="user", lazy=True)
    payment_logs = db.relationship("PaymentLog", backref="user", lazy=True)

    def set_password(self, password):
        #scramble the password
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        #returns True if the typed password matches the saved hash
        return check_password_hash(self.password_hash, password)


class Payment(db.Model):
    """One regular bill or subscription belonging to a user."""
#unique id number
    id = db.Column(db.Integer, primary_key=True)

#short name of bill e.g. "Spotify"
    name = db.Column(db.String(100), nullable=False)

#longer description e.g. "Spotify Premium Platinum Duo via Vodacom"
    description = db.Column(db.String(100), nullable=True)

#how the bill is paid(airtime vs debit card etc)
    payment_method = db.Column(db.String(50), nullable = True)

#cost of subscription or bill in float to allow for decimals
    amount = db.Column(db.Float, nullable=False)

#day of the month which the next payment is due
    due_day = db.Column(db.Integer, nullable=False)

#true or false of whether payment has been made or not
    is_paid = db.Column(db.Boolean, default=False)

#amount that has been paid
    amount_paid = db.Column(db.Float, nullable=True, default=0)

#"fixed" for normal bills, "loan" for debts being paid down, "credit" for store/credit accounts
    bill_type = db.Column(db.String(20), nullable=False, default="fixed")

#for loans: original loan amount; for credit accounts: the credit limit
    total_value = db.Column(db.Float, nullable=True)

#for loans: remaining balance owed; for credit accounts: current balance used
    current_balance = db.Column(db.Float, nullable=True)

#for loans: interest rate percentage
    interest_rate = db.Column(db.Float, nullable=True)

#for loans: months remaining on the loan
    months_remaining = db.Column(db.Integer, nullable=True)

#for loans: monthly loan insuraance/protection premium
    loan_insurance = db.Column(db.Float, nullable=True)

#for loan: once-off initiation fee
    initiation_fee = db.Column(db.Float, nullable=True)

#for loans: monthly service/admin fee
    service_fee = db.Column(db.Float, nullable=True)

#for credit accounts: minimum % of balance due each month
    minimum_payment_percent = db.Column(db.Float, nullable=True)

#money still owed from earlier months/weeks - unpaid bills roll over into here
    carried_over = db.Column(db.Float, nullable=True, default=0)

#for variable bills (water, electricity): has this month's amount been checked yet?
    is_confirmed = db.Column(db.Boolean, default=True)

#"monthly" bills use a day of the month; "weekly" ones use a day of the week (1=Monday .. 7=Sunday)
    frequency = db.Column(db.String(10), nullable=False, default="monthly")

#custom drag-and-drop position on the dashboard
    sort_order = db.Column(db.Integer, nullable=True)

#for once-off bills: paid and tucked away, off the dashboard for good (#50)
    is_archived = db.Column(db.Boolean, default=False)

#captures exactly when a new bill or subscription was added
    date_added = db.Column(db.DateTime, default=datetime.datetime.now)

#which user owns this bill
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

class Reminder(db.Model):
    #send a message to user

    id = db.Column(db.Integer, primary_key=True)

#reminder text
    message = db.Column(db.String(255), nullable=False)

#weekly or monthly reminder
    category = db.Column(db.String(20), nullable=False)

#ticked off yes or no
    is_read = db.Column(db.Boolean, default=False)

#snap of when reminder was created
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

#which bill this reminder is about, when it is about one (#53) -
#lets paying a bill automatically tick off its reminders
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), nullable=True)

#which user this reminder is for
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

class Income(db.Model):
    """Source of income """

    id = db.Column(db.Integer, primary_key=True)

    #name of source of income
    name = db.Column(db.String(100), nullable=False)

    #last known amount
    amount = db.Column(db.Float, nullable=False)

    # fixed amount for a stable income
    income_type = db.Column(db.String(20), nullable=False, default="fixed")

    #"monthly" (salary) or "weekly" (weekly wages)
    frequency = db.Column(db.String(10), nullable=False, default="monthly")

    #variable income, confirm if that months' amount has been confirmed.
    #reset to False each month automatically
    is_confirmed = db.Column(db.Boolean, default=True)

    #which user the income belongs to
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class PaymentLog(db.Model):
    """ One payment made towards a bill.
    Never reset by the monthly rollover, so it builds a permanent history """

    id = db.Column(db.Integer, primary_key=True)

#name of the bill at the time it was paid
    bill_name = db.Column(db.String(100), nullable=False)

#how much money was paid in this specific payment (not the running total)
    amount_paid = db.Column(db.Float, nullable=False)

#exactly when the payment was recorded
    paid_at = db.Column(db.DateTime, default=datetime.datetime.now)

#which bill this payment was for
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), nullable=True)

#which user made the payment
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Buddy(db.Model):
    """ A pixel companion. A user can own several, one shown at a time """

    id = db.Column(db.Integer, primary_key=True)

#what the user calls their buddy
    name = db.Column(db.String(50), nullable=False, default="Buddy")

#which sprite it uses, one of BUDDY_SPECIES
    species = db.Column(db.String(30), nullable=False, default="blobcat")

#"egg" until it has soaked up HATCH_XP, then "hatched"
    stage = db.Column(db.String(10), nullable=False, default="hatched")

#lifetime experience, never spent - the level is worked out from it
    xp = db.Column(db.Integer, nullable=False, default=0)

#spendable coins, earned alongside xp and spent in the shop
    coins = db.Column(db.Integer, nullable=False, default=0)

#when the buddy was created
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

#is this the one shown on screen? only one per user
    is_active = db.Column(db.Boolean, default=False)

#who the buddy belongs to
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class XpEvent(db.Model):
    """ One xp award. The unique rule stops the same action paying twice,
    e.g. un-paying and re-paying a bill over and over """

    id = db.Column(db.Integer, primary_key=True)

#what earned it: "register_bill", "pay_bill", "check_in", "clear_carryover"
    kind = db.Column(db.String(30), nullable=False)

#which exact action, e.g. "pay:14:2026-08" - one award per key, ever
    ref_key = db.Column(db.String(60), nullable=False)

#how much xp it earned
    amount = db.Column(db.Integer, nullable=False)

#when it was earned
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)

#who earned it
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    __table_args__ = (db.UniqueConstraint("user_id", "kind", "ref_key"),)


class OwnedCosmetic(db.Model):
    """ A shop item a user has bought. The catalogue itself is BUDDY_SHOP """

    id = db.Column(db.Integer, primary_key=True)

#which BUDDY_SHOP item, e.g. "party_hat"
    item_key = db.Column(db.String(30), nullable=False)

#worn/placed right now? one per slot
    equipped = db.Column(db.Boolean, default=False)

#who bought it
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    __table_args__ = (db.UniqueConstraint("user_id", "item_key"),)


@login_manager.user_loader
def load_user(user_id):
    #Flask-Login
    return db.session.get(User, int(user_id))


#----------------------------------------------------------------------#
#---------------------------Helper Functions---------------------------#
#----------------------------------------------------------------------#

@app.template_filter("ordinal")
def ordinal_day(day):
    """Turn a day into its text version 1 becomes 1st, 22 becomes 22nd, 
    15 becomes 15th and 3 becomes 3rd etc
    [11th, 12, 13th are special edge cases]"""
    if day in (11, 12, 13):
        suffix = "th"
    else:
        
        #last digit decides suffix: 1 -> st, 2 -> nd, 3 -> rd, rest are th
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def days_until_due(due_day, is_paid=False):
    """     Works out when next bill is due    """

    today = datetime.datetime.now()

    #make sure due day is not more than days in month
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    due_day = min(due_day, days_in_month)

    #due day still coming later in month
    if today.day <= due_day:
        return due_day - today.day
    elif not is_paid:
        #due date has passed and bill not paid
        #negative number = days overdue
        return -(today.day - due_day)
    else:
        #paid already, so next payment is next month
        days_left_this_month = days_in_month - today.day
        return days_left_this_month + due_day


def days_until_due_weekly(due_weekday, is_paid=False):
    """ Same as days_until_due but inside a week (1=Monday .. 7=Sunday).
    negative = the day already passed this week and the bill isn't paid """
    today = datetime.datetime.now()
    todays_weekday = today.weekday() + 1   #weekday() is 0-6, we use 1-7
    diff = due_weekday - todays_weekday
    if diff >= 0:
        return diff
    elif not is_paid:
        return diff
    else:
        #already paid, so next one is the same day next week
        return diff + 7


@app.template_filter("weekday_name")
def weekday_name(day):
    """Turn 1..7 into Monday..Sunday for weekly bills."""
    return calendar.day_name[day - 1]


def days_left_for(payment):
    """ Days until a bill is due, whatever its frequency """
    if payment.frequency == "weekly":
        return days_until_due_weekly(payment.due_day, payment.is_paid)
    return days_until_due(payment.due_day, payment.is_paid)


def due_date_for(payment):
    """ The date a bill is next due.
    Uses the days-left helpers so month lengths are only handled in one place """
    today = datetime.datetime.now()
    return today + datetime.timedelta(days=days_left_for(payment))


def due_date_text(payment):
    """ The due date in full, e.g. "Friday 25 July 2026".
    Built piece by piece so the day isn't zero padded ("5 July", not "05 July") """
    due = due_date_for(payment)
    return f"{due.strftime('%A')} {due.day} {due.strftime('%B %Y')}"


def description_note(payment):
    """ The optional description, tacked onto reminder messages and emails """
    return f" — {payment.description}" if payment.description else ""


def due_phrase(payment):
    """ How reminders say when a bill is due:
    monthly -> "the 5th, Sunday 5 July 2026", weekly -> "Friday" """
    if payment.frequency == "weekly":
        return calendar.day_name[payment.due_day - 1]
    return f"the {ordinal_day(payment.due_day)}, {due_date_text(payment)}"


def monthly_equivalent(amount, frequency):
    """ A weekly amount as a monthly one (52 weeks / 12 months).
    For INCOME only - bills use month_obligation, which counts real weeks """
    if frequency == "weekly":
        return round(amount * 52 / 12, 2)
    return amount


def previous_month(today=None):
    """ The (year, month) before today, for closing off the month just ended """
    today = today or datetime.date.today()
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def weeks_in_month(payment, year=None, month=None):
    """ How many times a weekly bill falls due in a month, really 4 or 5 """
    today = datetime.date.today()
    year = year or today.year
    month = month or today.month
    days = calendar.monthrange(year, month)[1]
    #due_day is 1=Monday..7=Sunday, weekday() is 0=Monday..6=Sunday
    wanted = payment.due_day - 1
    return sum(1 for d in range(1, days + 1)
               if datetime.date(year, month, d).weekday() == wanted)


def month_obligation(payment, year=None, month=None):
    """ What a bill costs over one whole month.
    A weekly bill costs its amount once per due-weekday, so ticking off one
    week clears exactly one week of the monthly total """
    if payment.frequency == "weekly":
        return round(payment.amount * weeks_in_month(payment, year, month), 2)
    return payment.amount


def paid_in_month(payment, year=None, month=None):
    """ How much was really paid towards a bill in a month.
    Read from the payment history so every week counts, not just the last one """
    today = datetime.date.today()
    year = year or today.year
    month = month or today.month
    start = datetime.datetime(year, month, 1)
    if month == 12:
        end = datetime.datetime(year + 1, 1, 1)
    else:
        end = datetime.datetime(year, month + 1, 1)
    total = db.session.query(db.func.sum(PaymentLog.amount_paid)).filter(
        PaymentLog.payment_id == payment.id,
        PaymentLog.paid_at >= start,
        PaymentLog.paid_at < end,
    ).scalar()
    return round(total or 0, 2)


def remaining_this_month(payment):
    """ Still owed on a bill for THIS month.
    Carried over debt is counted separately, not here """
    if payment.frequency == "weekly":
        return max(0.0, round(month_obligation(payment) - paid_in_month(payment), 2))
    if payment.is_paid:
        return 0.0
    return round(payment.amount - (payment.amount_paid or 0), 2)


def weeks_paid_this_month(payment):
    """ How many of this month's weeks are paid off so far """
    if payment.frequency != "weekly" or not payment.amount:
        return 0
    return int(paid_in_month(payment) // payment.amount)


def week_xp_key(payment, week, month=None):
    """ The one-award-per-week key for ticking a weekly loan off """
    month = month or datetime.date.today().strftime("%Y-%m")
    return f"loanweek:{payment.id}:{month}:{week}"


def get_status(payment):
    """
    Return a status WORD for a bill
    Colour coded
    "paid"     -> (green)
    "overdue"  -> (soft red)
    "soon"     -> (amber)
    "upcoming" -> (neutral)

    """

    if payment.is_paid:
        return "paid"

    #weekly bills 
    if payment.frequency == "weekly":
        days = days_until_due_weekly(payment.due_day, payment.is_paid)
        if days < 0:
            return "overdue"
        if days <= 2:
            return "soon"
        return "upcoming"

    today = datetime.datetime.now()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    due_day = min(payment.due_day, days_in_month)

    #if today is past due day and bill is not paid = overdue
    if today.day > due_day:
        return "overdue"

    #if due within 5 days = "soon"
    if days_until_due(payment.due_day, payment.is_paid) <= 5:
        return "soon"

    return "upcoming"

#what the buddy says, per mood
BUDDY_MESSAGES = {
    "happy": [
        "Everything's paid - you're amazing!",
        "All clear! Treat yourself (a little).",
        "Look at that dashboard. Spotless!",
    ],
    "neutral": [
        "We've got this. One bill at a time.",
        "Hellooooo!",
        "I'm keeping an eye on things with you.",
    ],
    "worried": [
        "Eek - '{bill}' is overdue!",
        "Um... '{bill}' needs some attention.",
        "Don't forget '{bill}'! I believe in you!",
    ],
}

#an egg hatches once it has soaked up this much xp
HATCH_XP = 100

#what an egg can hatch into. cats are recolours of one drawing, frogs of another
BUDDY_SPECIES = ["blobcat", "mintcat", "peachcat", "blackcat", "frog", "purplefrog"]

#which species use the frog drawing
FROG_SPECIES = ("frog", "purplefrog")

#what the egg says at each % of hatching progress
EGG_MESSAGES = [
    (0, "An egg! Keeping your budget tidy might warm it up..."),
    (25, "The egg twitched! Paying bills seems to help."),
    (50, "A crack! Whatever's inside likes good budgeting."),
    (75, "It's nearly hatching! Just a little more XP!"),
]

#hitting these levels earns a new egg, up to MAX_BUDDIES.
#the cap is one starting egg plus one per milestone, so none are wasted
EGG_LEVELS = (3, 5, 7, 10)
MAX_BUDDIES = 5

#everything the shop sells, one item per slot at a time.
#the drawings live in templates/_buddy_sprite.html and _buddy_room.html
BUDDY_SHOP = {
    "party_hat":  {"name": "Party hat",  "icon": "🎉", "price": 60,  "slot": "hat",       "min_level": 1},
    "flower":     {"name": "Flower",     "icon": "🌸", "price": 80,  "slot": "hat",       "min_level": 1},
    "top_hat":    {"name": "Top hat",    "icon": "🎩", "price": 150, "slot": "hat",       "min_level": 3},
    "bow_tie":    {"name": "Bow tie",    "icon": "🎀", "price": 50,  "slot": "accessory", "min_level": 1},
    "witch_hat":  {"name": "Witch hat",  "icon": "🧙", "price": 130, "slot": "hat",       "min_level": 2},
    "sunglasses": {"name": "Sunglasses", "icon": "🕶️", "price": 100, "slot": "accessory", "min_level": 2},
    "scarf":      {"name": "Scarf",      "icon": "🧣", "price": 120, "slot": "accessory", "min_level": 2},
    #room decor. each corner is its own slot, so all four can be filled at once
    "window":     {"name": "Window",     "icon": "🪟", "price": 140, "slot": "wall_left",   "min_level": 2},
    "poster":     {"name": "Poster",     "icon": "🖼️", "price": 90,  "slot": "wall_right",  "min_level": 1},
    "plant":      {"name": "Pot plant",  "icon": "🪴", "price": 80,  "slot": "floor_left",  "min_level": 1},
    "lamp":       {"name": "Lamp",       "icon": "💡", "price": 120, "slot": "floor_right", "min_level": 2},
    "rug":        {"name": "Cosy rug",   "icon": "🧶", "price": 100, "slot": "floor",       "min_level": 1},
}

#slot names for the shop - "wall_left" reads badly on screen
SLOT_LABELS = {
    "hat": "hat",
    "accessory": "accessory",
    "wall_left": "top left",
    "wall_right": "top right",
    "floor_left": "bottom left",
    "floor_right": "bottom right",
    "floor": "floor",
}

#slots worn ON the buddy, everything else furnishes the room
WEARABLE_SLOTS = ("hat", "accessory")


def buddy_mood(user):
    """ How the buddy feels about the bills, returns (mood, message) """
    payments = Payment.query.filter_by(user_id=user.id).all()
    overdue = [p for p in payments if get_status(p) == "overdue"]
    if overdue:
        mood = "worried"
        message = random.choice(BUDDY_MESSAGES["worried"]).format(bill=overdue[0].name)
    elif payments and all(p.is_paid for p in payments):
        mood = "happy"
        message = random.choice(BUDDY_MESSAGES["happy"])
    else:
        mood = "neutral"
        message = random.choice(BUDDY_MESSAGES["neutral"])
    return mood, message


def buddy_level(xp):
    """ Level from lifetime xp: 2 at 50, 3 at 200, 4 at 450 etc """
    return int((xp / 50) ** 0.5) + 1


def xp_for_level(level):
    """ Xp needed to reach a level, the reverse of buddy_level """
    return 50 * (level - 1) ** 2


def pay_period_key(payment):
    """ The one-award-per-period key: per month, or per week for weekly bills """
    today = datetime.date.today()
    if payment.frequency == "weekly":
        year, week, _ = today.isocalendar()
        return f"pay:{payment.id}:{year}-W{week:02d}"
    return f"pay:{payment.id}:{today.strftime('%Y-%m')}"


def get_active_buddy(user):
    """ The buddy shown on screen.
    Older accounts get their first buddy promoted, and an account with none
    at all gets a ready-hatched one made on the spot """
    buddy = Buddy.query.filter_by(user_id=user.id, is_active=True).first()
    if buddy is None:
        buddy = Buddy.query.filter_by(user_id=user.id).first()
        if buddy is None:
            buddy = Buddy(user_id=user.id, is_active=True)
        else:
            buddy.is_active = True
        db.session.add(buddy)
        db.session.commit()
    return buddy


def award_xp(user, kind, ref_key, amount):
    """ Give the active buddy xp, but only once per (kind, ref_key).
    Returns True if it was actually awarded """
    if XpEvent.query.filter_by(user_id=user.id, kind=kind, ref_key=ref_key).first():
        return False
    buddy = get_active_buddy(user)
    level_before = buddy_level(buddy.xp)
    db.session.add(XpEvent(user_id=user.id, kind=kind, ref_key=ref_key, amount=amount))
    buddy.xp += amount
    buddy.coins += amount   #coins come alongside xp, and get spent in the shop

    #enough xp hatches an egg, the species is a surprise until now
    just_hatched = False
    if buddy.stage == "egg" and buddy.xp >= HATCH_XP:
        buddy.stage = "hatched"
        buddy.species = random.choice(BUDDY_SPECIES)
        just_hatched = True
    db.session.commit()

    if just_hatched:
        #makes the next page play the hatch animation, once
        session["buddy_hatched"] = True
        flash(f"The egg hatched! Say hello to {buddy.name}!", "success")
    elif buddy.stage == "hatched" and buddy_level(buddy.xp) > level_before:
        new_level = buddy_level(buddy.xp)
        flash(f"{buddy.name} reached level {new_level}!", "success")
        #milestone levels earn a new egg for the house
        if (new_level in EGG_LEVELS
                and Buddy.query.filter_by(user_id=user.id).count() < MAX_BUDDIES):
            db.session.add(Buddy(user_id=user.id, stage="egg", is_active=False))
            db.session.commit()
            flash("You found a new egg! It's waiting in the house.", "success")
    return True


def mark_bill_reminders_read(payment):
    """ Tick off a bill's unread reminders when it gets paid.
    New reminders carry the bill id, older ones match on the quoted name """
    unread = Reminder.query.filter_by(user_id=payment.user_id, is_read=False).all()
    for r in unread:
        if r.payment_id == payment.id or (
                r.payment_id is None and f"'{payment.name}'" in r.message):
            r.is_read = True


def parse_money(raw):
    """ Turn a form value into a number, 2 decimals, None if empty.
    Fixes numbers saving wrongly: float maths gives 149.99999999999997, and
    some phone keyboards type a comma decimal ("199,99") """
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(" ", "").replace(",", ".")
    return round(float(cleaned), 2)


def log_payment(payment, amount):
    """ Save one row of payment history.
    A negative amount is a correction, e.g. fixing a typo """
    if amount == 0:
        return
    db.session.add(PaymentLog(
        bill_name=payment.name,
        amount_paid=amount,
        payment_id=payment.id,
        user_id=payment.user_id,
    ))


def send_email(to_address, subject, body):
    """ Send one plain text email, Gmail by default.
    The login comes from .env so no real password is ever written in the code """

    sender = os.environ.get("EMAIL_ADDRESS")
    password = os.environ.get("EMAIL_APP_PASSWORD")

    #the mail server, can be pointed at another provider from .env
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", 465))

    #the name people see in their inbox instead of the raw address
    from_name = os.environ.get("EMAIL_FROM_NAME", "Budget Buddy")

    #email not set up, or no address to send to = quietly do nothing
    if not sender or not password or not to_address:
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    #"Display Name <address>" is how a friendly sender is written
    msg["From"] = f"{from_name} <{sender}>"
    msg["To"] = to_address
    msg.set_content(body)

    try:
        #465 is encrypted from the start, 587 starts plain and upgrades
        if port == 587:
            with smtplib.SMTP(host, port) as server:
                server.starttls()
                server.login(sender, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port) as server:
                server.login(sender, password)
                server.send_message(msg)
    except Exception as e:
        #print instead of raising, so one bad email doesn't stop the others
        print(f"Email failed for {to_address}: {e}")


def email_unread_reminders(user, subject):
    """ Send a user's unread reminders as one summary email.
    Called AFTER they are committed, so they can be read back """
    if not user.email_reminders or not user.email:
        return

    lines = [
        r.message for r in Reminder.query.filter_by(
            user_id=user.id, is_read=False
        ).order_by(Reminder.created_at.desc()).all()
    ]
    if not lines:
        return

    body = "Here are your Budget Buddy reminders:\n\n"
    body += "\n".join(f"- {line}" for line in lines)
    body += "\n\nOpen Budget Buddy to tick these off."
    send_email(user.email, subject, body)


#-----------------------------------------------------------------------------#
#-----------------AUTH ROUTES - register, login, logout-----------------------#
#-----------------------------------------------------------------------------#

@app.route("/register", methods=["GET", "POST"])
def register():
    """ Make a new account """
    #if already logged in, no need to register
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        confirm = request.form["confirm"]

        #simple checks before we create the account
        if not username or not password or not email:
            flash("Please fill in a username, an email address and a password.", "warning")
            return redirect(url_for("register"))

        if password != confirm:
            flash("Those passwords don't match. Try again.", "warning")
            return redirect(url_for("register"))

        #is the username already taken
        if User.query.filter_by(username=username).first():
            flash("That username is already taken. Pick another.", "warning")
            return redirect(url_for("register"))

        #is the email already used by another account
        if User.query.filter_by(email=email).first():
            flash("That email is already registered. Try logging in.", "warning")
            return redirect(url_for("register"))

        #make the user, scramble the password, save user
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        #new accounts start with a mystery egg, older ones get a
        #ready-hatched buddy from get_active_buddy instead
        db.session.add(Buddy(user_id=user.id, stage="egg", is_active=True))
        db.session.commit()

        #log them straight in after registering
        login_user(user)
        flash(f"Welcome to Budget Buddy, {username}!", "welcome")
        return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """ Log into an existing account """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        user = User.query.filter_by(username=username).first()

        #check the user exists AND the password is correct
        if user is None or not user.check_password(password):
            flash("Wrong username or password.", "warning")
            return redirect(url_for("login"))

        login_user(user)
        flash(f"Welcome back, {username}!", "welcome")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    """ Log out of the current account """
    logout_user()
    flash("You've been logged out.", "success")
    return redirect(url_for("login"))


def get_reset_serializer():
    """ Makes the signed, expiring tokens used in password reset links.
    Signed with SECRET_KEY so it can't be faked, and nothing is stored """
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="password-reset")


@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    """ Ask for an email address and send a password reset link to it """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        user = User.query.filter_by(email=email).first() if email else None
        if user:
            token = get_reset_serializer().dumps(user.id)
            link = url_for("reset_password", token=token, _external=True)
            send_email(
                user.email,
                "Reset your Budget Buddy password",
                (f"Hi {user.username},\n\n"
                 f"Click this link to choose a new password:\n{link}\n\n"
                 "The link works for 1 hour. If you didn't ask for this, you can ignore it."),
            )

        flash("If that email has an account, a reset link has been sent to it.", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    """ Choose a new password using an emailed reset link """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    try:
        #max_age is in seconds, so the link stops working after an hour
        user_id = get_reset_serializer().loads(token, max_age=3600)
    except SignatureExpired:
        flash("That reset link has expired. Ask for a new one.", "warning")
        return redirect(url_for("forgot_password"))
    except BadSignature:
        flash("That reset link is not valid.", "warning")
        return redirect(url_for("forgot_password"))

    user = db.session.get(User, user_id)
    if user is None:
        flash("That account no longer exists.", "warning")
        return redirect(url_for("register"))

    if request.method == "POST":
        password = request.form["password"]
        confirm = request.form["confirm"]
        if not password:
            flash("Please type a new password.", "warning")
            return redirect(url_for("reset_password", token=token))
        if password != confirm:
            flash("Those passwords don't match. Try again.", "warning")
            return redirect(url_for("reset_password", token=token))
        user.set_password(password)
        db.session.commit()
        flash("Password changed. You can log in now.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.context_processor
def inject_buddy():
    """ Give every template the active buddy, base.html shows it on all pages """
    if not current_user.is_authenticated:
        return {}
    buddy = get_active_buddy(current_user)

    #showing up counts, the daily check in
    award_xp(current_user, "check_in", f"day:{datetime.date.today().isoformat()}", 5)

    #did that xp just hatch the egg? set by award_xp, played once
    just_hatched = session.pop("buddy_hatched", False)

    #what the buddy is wearing, drawn as extra layers on the sprite
    equipped = [c.item_key for c in OwnedCosmetic.query.filter_by(
        user_id=current_user.id, equipped=True).all()]

    ctx = {"buddy": buddy, "buddy_just_hatched": just_hatched,
           "buddy_equipped": equipped}

    if buddy.stage == "egg":
        #eggs have no mood, their message tracks hatching progress
        pct = min(100, round(buddy.xp / HATCH_XP * 100))
        message = EGG_MESSAGES[0][1]
        for threshold, text in EGG_MESSAGES:
            if pct >= threshold:
                message = text
        ctx.update({"buddy_mood": "neutral", "buddy_message": message,
                    "buddy_xp_pct": pct})
    else:
        mood, message = buddy_mood(current_user)
        level = buddy_level(buddy.xp)
        base = xp_for_level(level)
        nxt = xp_for_level(level + 1)
        ctx.update({"buddy_mood": mood, "buddy_message": message,
                    "buddy_level": level,
                    "buddy_xp_pct": round((buddy.xp - base) / (nxt - base) * 100)})
    return ctx


#-----------------------------------------------------------------------------#
#-----------------ROUTES - each function references a web page----------------#
#-----------------------------------------------------------------------------#

@app.route("/")
@login_required
def dashboard():
    """ Home Page: Show totals,upcoming reminders and overdues, list of bills"""

    filter_status = request.args.get("filter", "all")
    sort_by = request.args.get("sort", "due_day")

    #only this user's payments, sorted by due day. archived once-off
    #bills are finished with, so they stay out of sight
    payments = (
        Payment.query.filter_by(user_id=current_user.id)
        .filter(Payment.is_archived != True)
        .order_by(Payment.due_day)
        .all()
    )

    #build a list pairing each payment with its status + days left
    payments_with_status = []
    for p in payments:
        payments_with_status.append({
            "payment": p,
            "status": get_status(p),
            "days_left": days_left_for(p),
            #what it costs for the whole month and how far through it we are,
            #weekly bills get ticked off a week at a time
            "month_cost": month_obligation(p),
            "month_paid": paid_in_month(p) if p.frequency == "weekly" else None,
            "weeks_total": weeks_in_month(p) if p.frequency == "weekly" else None,
            "weeks_paid": weeks_paid_this_month(p),
        })

    #filter
    if filter_status != "all":
        payments_with_status = [p for p in payments_with_status if p["status"] == filter_status]

    #sort
    if sort_by == "amount":
        payments_with_status.sort(key=lambda p: p["payment"].amount, reverse=True)
    elif sort_by == "name":
        payments_with_status.sort(key=lambda p: p["payment"].name.lower())
    elif sort_by == "custom":
        #the user's own drag-and-drop order; bills never dragged go last
        payments_with_status.sort(
            key=lambda p: (p["payment"].sort_order is None, p["payment"].sort_order or 0)
        )

    #monthly totals. a weekly bill counts every week of the month, and each
    #week marked paid comes off what's left
    total_monthly = sum(month_obligation(p) for p in payments)
    #left = this month's outstanding PLUS anything carried over from before
    still_owed = {p.id: remaining_this_month(p) for p in payments}
    total_unpaid = (
        sum(still_owed.values())
        + sum(p.carried_over or 0 for p in payments)
    )
    #a weekly bill stays "still to pay" until every week of the month is done
    unpaid = [p for p in payments
              if still_owed[p.id] > 0 or (p.carried_over or 0) > 0]

    #only this user's unread reminders
    unread_reminders = (
        Reminder.query.filter_by(user_id=current_user.id, is_read=False)
        .order_by(Reminder.created_at.desc())
        .all()
    )

    over_budget = (current_user.budget_limit is not None and total_monthly > current_user.budget_limit)

    income_sources = Income.query.filter_by(user_id=current_user.id).all()
    #weekly wages count as their monthly equivalent
    total_income = sum(monthly_equivalent(i.amount, i.frequency) for i in income_sources)
    money_remaining = total_income - total_monthly

    # render template loads dashboard.html
    return render_template(
        "dashboard.html",
        payments_with_status=payments_with_status,
        total_monthly=total_monthly,
        total_unpaid=total_unpaid,
        unpaid_count=len(unpaid),
        reminders=unread_reminders,
        over_budget=over_budget,
        budget_limit=current_user.budget_limit,
        income_sources=income_sources,
        total_income=total_income,
        money_remaining=money_remaining,
        filter_status=filter_status,
        sort_by=sort_by
    )

@app.route("/add", methods=["GET", "POST"])
@login_required
def add_payment():
    """Show add bill or subscription form and save when submitted."""

#create the form the user will use
    if request.method == "POST":
#name = short name e.g. "Spotify"
#description = more detailed e.g. "Spotify Premium Platinum Duo via Vodacom airtime deduction"
        name = request.form["name"]
        description = request.form["description"] or None
        amount = parse_money(request.form["amount"])
        due_day = int(request.form["due_day"])
        payment_method = request.form.get("payment_method") or None
        bill_type = request.form.get("bill_type", "fixed")
        frequency = request.form.get("frequency", "monthly")
        total_value = parse_money(request.form.get("total_value"))
        current_balance = parse_money(request.form.get("current_balance"))

        interest_rate = parse_money(request.form.get("interest_rate"))
        raw_months = request.form.get("months_remaining")
        months_remaining = int(raw_months) if raw_months else None
        loan_insurance = parse_money(request.form.get("loan_insurance"))
        service_fee = parse_money(request.form.get("service_fee"))
        initiation_fee = parse_money(request.form.get("initiation_fee"))

        minimum_payment_percent = parse_money(request.form.get("minimum_payment_percent"))


#create a new payment, one row of information, owned by the logged-in user
        new_payment = Payment(
            name=name,
            description=description,
            amount=amount,
            due_day=due_day,
            payment_method=payment_method,
            bill_type=bill_type,
            frequency=frequency,
            total_value=total_value,
            current_balance=current_balance,
            user_id=current_user.id,
            
            interest_rate=interest_rate,
            months_remaining=months_remaining,
            loan_insurance=loan_insurance,
            initiation_fee=initiation_fee,
            service_fee=service_fee,

            minimum_payment_percent=minimum_payment_percent
        )

#add to database
        db.session.add(new_payment)
        db.session.commit()

        #registering a bill earns the buddy XP (once per bill, ever)
        award_xp(current_user, "register_bill", f"bill:{new_payment.id}", 10)

#show a message after commit to show it was added successfully
        flash(f"Added '{name}' successfully to your bills.", "success")
        return redirect(url_for("dashboard"))

    #when request is just GET then show empty form
    return render_template("add_payment.html")


@app.route("/income/add", methods=["GET", "POST"])
@login_required
def add_income():
    """show income form(fixed or varied) and save when submitted """

    if request.method == "POST":

        name=request.form["name"]
        amount=parse_money(request.form["amount"])
        income_type = request.form.get("income_type", "fixed")
        frequency = request.form.get("frequency", "monthly")

        new_income = Income(
            name=name,
            amount=amount,
            income_type=income_type,
            frequency=frequency,
            user_id=current_user.id,
        )


        db.session.add(new_income)
        db.session.commit()
        flash(f"Added '{name}' as an income source.", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_income.html")

@app.route("/edit/<int:payment_id>", methods=["GET", "POST"])
@login_required
def edit_payment(payment_id):
    """ Edit a bill """
    payment = get_owned_payment_or_404(payment_id)
    if request.method == "POST":
        #overwrite the bill's fields with new values
        payment.name = request.form["name"]
        payment.description = request.form["description"] or None
        payment.amount = parse_money(request.form["amount"])
        payment.due_day = int(request.form["due_day"])
        payment.payment_method = request.form.get("payment_method") or None
        payment.bill_type = request.form.get("bill_type", "fixed")
        payment.frequency = request.form.get("frequency", "monthly")

        payment.total_value = parse_money(request.form.get("total_value"))
        payment.current_balance = parse_money(request.form.get("current_balance"))
        payment.service_fee = parse_money(request.form.get("service_fee"))

        payment.interest_rate = parse_money(request.form.get("interest_rate"))
        raw_months = request.form.get("months_remaining")
        payment.months_remaining = int(raw_months) if raw_months else None
        payment.loan_insurance = parse_money(request.form.get("loan_insurance"))
        payment.initiation_fee = parse_money(request.form.get("initiation_fee"))
        payment.minimum_payment_percent = parse_money(request.form.get("minimum_payment_percent"))

        db.session.commit()
        flash(f"Updated '{payment.name}' successfully", "success")
        return redirect(url_for("dashboard"))
    #show new form with updated values
    return render_template("edit_payment.html", payment=payment)


@app.route("/income/edit/<int:income_id>", methods=["GET", "POST"])
@login_required
def edit_income(income_id):
    """Edit income"""
    income = get_owned_income_or_404(income_id)
    if request.method == "POST":
        income.name = request.form["name"]
        income.amount = parse_money(request.form["amount"])
        income.income_type = request.form.get("income_type", "fixed")
        income.frequency = request.form.get("frequency", "monthly")
        db.session.commit()
        flash(f"Updated '{income.name}' successfully", "success")
        return redirect(url_for("dashboard"))
    
    return render_template("edit_income.html", income=income)


@app.route("/partial_pay/<int:payment_id>", methods=["POST"])
@login_required
def partial_pay(payment_id):
    """record a partial payment """
    payment = get_owned_payment_or_404(payment_id)
    raw = request.form.get("paid_amount")
    if raw:
        #remember what was already paid so only the NEW part is logged
        old_paid = payment.amount_paid or 0
        payment.amount_paid = parse_money(raw)
        if payment.amount_paid >= payment.amount:
            payment.is_paid = True
            payment.amount_paid = payment.amount
        else:
            payment.is_paid = False
        log_payment(payment, payment.amount_paid - old_paid)
        #finishing the bill also ticks off its reminders (#53)
        if payment.is_paid:
            mark_bill_reminders_read(payment)
    db.session.commit()
    #finishing off the whole bill earns the same XP as "Mark paid"
    if payment.is_paid:
        award_xp(current_user, "pay_bill", pay_period_key(payment), 15)
    flash(f"Payment recorded for '{payment.name}'.","success")
    return redirect(url_for("dashboard"))


@app.route("/carryover_paid/<int:payment_id>", methods=["POST"])
@login_required
def clear_carryover(payment_id):
    """ Mark the rolled-over debt on a bill as paid off """
    payment = get_owned_payment_or_404(payment_id)
    cleared_something = False
    if payment.carried_over:
        #it was real money paid, so it belongs in the payment history
        log_payment(payment, payment.carried_over)
        payment.carried_over = 0
        cleared_something = True
    db.session.commit()
    if cleared_something:
        #catching up on old debt deserves extra XP - once per bill per month
        month = datetime.date.today().strftime("%Y-%m")
        award_xp(current_user, "clear_carryover", f"carry:{payment.id}:{month}", 20)
    flash(f"Cleared the carried-over amount for '{payment.name}'.", "success")
    return redirect(url_for("dashboard"))


@app.route("/bill/confirm/<int:payment_id>", methods=["POST"])
@login_required
def confirm_bill(payment_id):
    """ Confirm (and maybe update) this month's amount for a variable bill.
    Leaving the amount box empty means "same as last month"."""
    payment = get_owned_payment_or_404(payment_id)
    new_amount = parse_money(request.form.get("new_amount"))
    if new_amount is not None:
        payment.amount = new_amount
    payment.is_confirmed = True
    db.session.commit()
    flash(f"Amount confirmed for '{payment.name}'.", "success")
    return redirect(url_for("dashboard"))


@app.route("/reorder", methods=["POST"])
@login_required
def reorder_bills():
    """ Save the custom drag-and-drop order of the bills.
    The page sends a JSON list of bill ids in their new order. """
    ids = request.get_json(silent=True)
    if not isinstance(ids, list):
        return {"ok": False}, 400
    for position, payment_id in enumerate(ids):
        payment = Payment.query.filter_by(id=payment_id, user_id=current_user.id).first()
        if payment:
            payment.sort_order = position
    db.session.commit()
    return {"ok": True}



@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        current_user.currency = request.form.get("currency", "R")
        current_user.theme = request.form.get("theme", "pastel")

        #email is required, so only change it if something was actually typed
        new_email = (request.form.get("email") or "").strip()
        if new_email and new_email != current_user.email:
            #don't let one account take an email another account already uses
            if User.query.filter_by(email=new_email).first():
                flash("That email is already used by another account.", "warning")
                return redirect(url_for("settings"))
            current_user.email = new_email

        #an unticked checkbox sends nothing at all, which reads as False
        current_user.email_reminders = bool(request.form.get("email_reminders"))

        current_user.budget_limit = parse_money(request.form.get("budget_limit"))
        db.session.commit()
        flash("Settings saved", "success")
        return redirect(url_for("settings"))
    return render_template("settings.html")

def get_owned_payment_or_404(payment_id):
    """ Fetch a bill by id but ONLY if it belongs to the logged-in user.
        Stops someone editing another person's bills by guessing ids. """
    return Payment.query.filter_by(
        id=payment_id, user_id=current_user.id
    ).first_or_404()

def get_owned_income_or_404(income_id):
    """ Fetch an income by id but only if it belongs to the logged-in user."""
    return Income.query.filter_by(
        id=income_id, user_id=current_user.id
    ).first_or_404()


@app.route("/pay/<int:payment_id>")
@login_required
def mark_paid(payment_id):
    """ Mark a Bill as PAID for this month. payment_id is the bill's id """
    #find the bill by its id (and make sure the user owns it), or show a 404
    payment = get_owned_payment_or_404(payment_id)
    #log whatever part of the bill was still unpaid to the payment history
    log_payment(payment, payment.amount - (payment.amount_paid or 0))
    payment.is_paid = True      #turns status to paid
    payment.amount_paid = payment.amount
    #paying also ticks off this bill's reminders
    mark_bill_reminders_read(payment)
    db.session.commit()         #saves the status change
    #paying earns xp, once per bill per month or week
    award_xp(current_user, "pay_bill", pay_period_key(payment), 15)
    flash(f"'{payment.name}' is paid.", "success") # green
    return redirect(url_for("dashboard"))

@app.route("/week/<int:payment_id>/<int:week>", methods=["POST"])
@login_required
def toggle_week(payment_id, week):
    """ Tick one week of a weekly loan off, or untick it again.
    Clicking an empty box pays every week up to it, clicking the last
    full box undoes just that week """
    payment = get_owned_payment_or_404(payment_id)
    if payment.frequency != "weekly" or not payment.amount:
        flash("Only weekly bills are paid off a week at a time.", "warning")
        return redirect(url_for("dashboard"))

    total_weeks = weeks_in_month(payment)
    week = max(1, min(week, total_weeks))
    done = weeks_paid_this_month(payment)
    #ticking a full box undoes it, ticking an empty one fills up to it
    target = week - 1 if week <= done else week

    if target > done:
        #one history row per week, so the months add up properly
        log_payment(payment, round(payment.amount * (target - done), 2))
        for w in range(done + 1, target + 1):
            award_xp(current_user, "pay_bill", week_xp_key(payment, w), 15)
    elif target < done:
        #a negative row is a correction, same as anywhere else
        log_payment(payment, round(payment.amount * (target - done), 2))
        #and the xp for those weeks goes back too
        buddy = get_active_buddy(current_user)
        for w in range(target + 1, done + 1):
            event = XpEvent.query.filter_by(
                user_id=current_user.id, kind="pay_bill",
                ref_key=week_xp_key(payment, w)).first()
            if event:
                buddy.xp = max(0, buddy.xp - event.amount)
                buddy.coins = max(0, buddy.coins - event.amount)
                db.session.delete(event)

    #the bill only counts as "paid" once every week of the month is done
    payment.is_paid = target >= total_weeks
    payment.amount_paid = payment.amount if payment.is_paid else 0
    if payment.is_paid:
        mark_bill_reminders_read(payment)
    db.session.commit()
    return redirect(url_for("dashboard"))


@app.route("/archive/<int:payment_id>", methods=["POST"])
@login_required
def archive_payment(payment_id):
    """ Tuck a paid once-off bill away for good.
    It stays in the payment history but leaves the dashboard """
    payment = get_owned_payment_or_404(payment_id)
    if payment.bill_type == "once_off" and payment.is_paid:
        payment.is_archived = True
        db.session.commit()
        flash(f"'{payment.name}' is done and archived.", "success")
    else:
        flash("Only paid once-off bills can be archived.", "warning")
    return redirect(url_for("dashboard"))


@app.route("/unpaid/<int:payment_id>")
@login_required
def mark_unpaid(payment_id):
    """ Mark a bill as unpaid, the Undo button.
    Also deletes the history rows for the period being undone, so a
    mistaken 'Mark paid' doesn't stay in the payment history """
    payment = get_owned_payment_or_404(payment_id)
    payment.is_paid = False
    payment.amount_paid = 0
    today = datetime.date.today()
    if payment.frequency == "weekly":
        #only undo THIS week, earlier weeks of the month were really paid
        period_start = datetime.datetime.combine(
            today - datetime.timedelta(days=today.weekday()), datetime.time.min)
    else:
        period_start = datetime.datetime(today.year, today.month, 1)
    PaymentLog.query.filter(
        PaymentLog.payment_id == payment.id,
        PaymentLog.user_id == current_user.id,
        PaymentLog.paid_at >= period_start,
    ).delete()
    #undoing takes back the xp and coins it earned, and frees the key
    #so paying it again properly can earn them back
    event = XpEvent.query.filter_by(
        user_id=current_user.id, kind="pay_bill",
        ref_key=pay_period_key(payment)).first()
    if event:
        buddy = get_active_buddy(current_user)
        buddy.xp = max(0, buddy.xp - event.amount)
        buddy.coins = max(0, buddy.coins - event.amount)
        db.session.delete(event)
    db.session.commit()
    flash(f"'{payment.name}' has not been paid yet.", "warning") # yellow
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:payment_id>")
@login_required
def delete_payment(payment_id):
    """ Remove a bill completely (e.g. you cancelled a subscription) """
    payment = get_owned_payment_or_404(payment_id)
    db.session.delete(payment)
    db.session.commit()
    flash(f"Deleted '{payment.name}'.", "success")
    return redirect(url_for("dashboard"))


@app.route("/income/delete/<int:income_id>")
@login_required
def delete_income(income_id):
    """ Remove an income source completely """
    income = get_owned_income_or_404(income_id)
    db.session.delete(income)
    db.session.commit()
    flash(f"Deleted '{income.name}'.", "success")
    return redirect(url_for("dashboard"))

@app.route("/income/confirm/<int:income_id>", methods=["POST"])
@login_required
def confirm_income(income_id):
    """ Confirm an income source """
    income = get_owned_income_or_404(income_id)
    raw = request.form.get("new_amount")
    if raw:
        income.amount = parse_money(raw)
    income.is_confirmed = True
    db.session.commit()
    flash(f"Income '{income.name}' confirmed.", "success")
    return redirect(url_for("dashboard"))

@app.route("/update_balance/<int:payment_id>", methods=["POST"])
@login_required
def update_balance(payment_id):
    """ Update the current balance for a loan or credit account bill """
    payment = get_owned_payment_or_404(payment_id)
    payment.current_balance = parse_money(request.form["new_balance"])
    db.session.commit()
    #keeping the balance honest earns xp, once per bill per month
    month = datetime.date.today().strftime("%Y-%m")
    award_xp(current_user, "update_balance", f"balance:{payment.id}:{month}", 10)
    flash(f"Balance updated for '{payment.name}'.", "success")
    return redirect(url_for("dashboard"))


@app.route("/history")
@login_required
def history():
    """ Page showing every payment ever recorded, grouped by month, newest first """
    logs = (
        PaymentLog.query.filter_by(user_id=current_user.id)
        .order_by(PaymentLog.paid_at.desc())
        .all()
    )

    #group the logs into months, newest month first
    #each month is a dict: its name e.g. "July 2026", its rows, and its total
    months = []
    for log in logs:
        label = log.paid_at.strftime("%B %Y")
        #logs are already sorted, so a new label means a new month has started
        if not months or months[-1]["label"] != label:
            months.append({"label": label, "logs": [], "total": 0})
        months[-1]["logs"].append(log)
        months[-1]["total"] += log.amount_paid
    today = datetime.datetime.now()
    year = today.year
    month = today.month

    chart_months = []
    for _ in range(6):
        start = datetime.datetime(year, month, 1)
        if month == 12:
            next_start = datetime.datetime(year + 1, 1, 1)
        else:
            next_start = datetime.datetime(year, month + 1, 1)

        month_logs = PaymentLog.query.filter(
            PaymentLog.user_id == current_user.id,
            PaymentLog.paid_at >= start,
            PaymentLog.paid_at < next_start
        ).all()
        total = sum(log.amount_paid for log in month_logs)

        #short month name e.g. "Jul" so the label fits under a narrow chart bar
        chart_months.append({"label": start.strftime("%b"), "total": total})

        if month == 1:
            month = 12
            year -= 1
        else:
            month -= 1

    chart_months.reverse()  # so the oldest month is first  

    chart_max = max(m["total"] for m in chart_months) or 1
    for m in chart_months:
        m["height"] = max(round(m["total"] / chart_max * 100), 0)

    return render_template("history.html", months=months, chart_months=chart_months)


@app.route("/reminders")
@login_required
def reminders():
    """ Page showing all reminders (read and unread) sorted by newest first """
    all_reminders = (
        Reminder.query.filter_by(user_id=current_user.id)
        .order_by(Reminder.created_at.desc())
        .all()
    )
    return render_template("reminders.html", reminders=all_reminders)


@app.route("/reminders/read/<int:reminder_id>")
@login_required
def mark_reminder_read(reminder_id):
    """ Tick off a reminder so it stops showing on the home page """

    reminder = Reminder.query.filter_by(
        id=reminder_id, user_id=current_user.id
    ).first_or_404()
    reminder.is_read = True
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/buddy")
@login_required
def buddy_page():
    """ The buddy's house: rename, shop, dress up, pick who's out front """
    owned = {c.item_key: c for c in
             OwnedCosmetic.query.filter_by(user_id=current_user.id).all()}
    buddies = (Buddy.query.filter_by(user_id=current_user.id)
               .order_by(Buddy.created_at).all())
    levels = {b.id: buddy_level(b.xp) for b in buddies}
    return render_template("buddy.html", shop=BUDDY_SHOP, owned=owned,
                           buddies=buddies, levels=levels,
                           slot_labels=SLOT_LABELS, wear_slots=WEARABLE_SLOTS)


@app.route("/buddy/activate/<int:buddy_id>", methods=["POST"])
@login_required
def activate_buddy(buddy_id):
    """ Choose which buddy or egg is shown on every page """
    target = Buddy.query.filter_by(id=buddy_id, user_id=current_user.id).first_or_404()
    for b in Buddy.query.filter_by(user_id=current_user.id).all():
        b.is_active = (b.id == target.id)
    db.session.commit()
    who = "The egg" if target.stage == "egg" else target.name
    flash(f"{who} is now out front!", "success")
    return redirect(url_for("buddy_page"))


@app.route("/buddy/name", methods=["POST"])
@login_required
def rename_buddy():
    """ Give the active buddy a new name """
    buddy = get_active_buddy(current_user)
    new_name = request.form.get("name", "").strip()
    if buddy and new_name:
        buddy.name = new_name[:50]
        db.session.commit()
        flash(f"Your buddy is now called {buddy.name}!", "success")
    return redirect(url_for("buddy_page"))


@app.route("/buddy/buy/<item_key>", methods=["POST"])
@login_required
def buy_cosmetic(item_key):
    """ Buy a shop item with coins, worn or placed straight away """
    item = BUDDY_SHOP.get(item_key)
    buddy = get_active_buddy(current_user)
    if item is None:
        flash("That item doesn't exist.", "warning")
        return redirect(url_for("buddy_page"))
    if buddy.stage == "egg":
        flash("Hatch your egg first!", "warning")
        return redirect(url_for("buddy_page"))
    if OwnedCosmetic.query.filter_by(user_id=current_user.id, item_key=item_key).first():
        flash("You already own that.", "warning")
        return redirect(url_for("buddy_page"))
    if buddy_level(buddy.xp) < item["min_level"]:
        flash(f"{item['name']} unlocks at level {item['min_level']}.", "warning")
        return redirect(url_for("buddy_page"))
    if buddy.coins < item["price"]:
        flash(f"Not enough coins - {item['name']} costs {item['price']}.", "warning")
        return redirect(url_for("buddy_page"))

    buddy.coins -= item["price"]
    #wearing the new item bumps whatever was in the same slot
    for other in OwnedCosmetic.query.filter_by(user_id=current_user.id, equipped=True).all():
        if BUDDY_SHOP.get(other.item_key, {}).get("slot") == item["slot"]:
            other.equipped = False
    db.session.add(OwnedCosmetic(user_id=current_user.id, item_key=item_key, equipped=True))
    db.session.commit()
    flash(f"Bought the {item['name']}! {buddy.name} is wearing it.", "success")
    return redirect(url_for("buddy_page"))


@app.route("/buddy/equip/<item_key>", methods=["POST"])
@login_required
def equip_cosmetic(item_key):
    """ Put an owned item on, bumping the same slot, or take it off """
    item = BUDDY_SHOP.get(item_key)
    owned = OwnedCosmetic.query.filter_by(user_id=current_user.id, item_key=item_key).first()
    if item is None or owned is None:
        flash("You don't own that item.", "warning")
        return redirect(url_for("buddy_page"))
    if owned.equipped:
        owned.equipped = False
    else:
        #only one item per slot may be worn at a time
        for other in OwnedCosmetic.query.filter_by(user_id=current_user.id, equipped=True).all():
            if BUDDY_SHOP.get(other.item_key, {}).get("slot") == item["slot"]:
                other.equipped = False
        owned.equipped = True
    db.session.commit()
    return redirect(url_for("buddy_page"))


@app.route("/tasks/run-daily")
def run_daily_tasks():
    """ Run whichever scheduled jobs are due today.
    Called daily by an outside scheduler, because a BackgroundScheduler
    can't survive on a host whose web worker sleeps between visits.
    A secret token stops a stranger triggering it """

    expected = os.environ.get("TASK_TOKEN")

    #no token set = refuse everyone rather than run unprotected.
    #compare_digest always takes the same time, so it can't leak the
    #token one letter at a time like == would
    if not expected or not secrets.compare_digest(request.args.get("token", ""), expected):
        return "Forbidden", 403

    today = datetime.datetime.now()
    ran = []

    #weekday() is 0 for Monday, so the weekly job still only runs weekly
    if today.weekday() == 0:
        create_weekly_reminder()
        ran.append("weekly")

    #and the monthly job only on the 1st
    if today.day == 1:
        create_monthly_reminders()
        ran.append("monthly")

    return f"ran: {', '.join(ran) if ran else 'nothing due today'}", 200

#---------------------------------------------------------#
#------Scheduled reminder jobs(automated reminders)-------#
#---------------------------------------------------------#
"""
automated reminders that run once a week or once a month etc.
they loop over every user so everyone gets their own reminders.
"""

def create_weekly_reminder():
    #runs once a week

    with app.app_context():
        for user in User.query.all():
            db.session.add(Reminder(
                message=("Weekly Check in! Have you added any new bills or subscriptions this week? Click on + to add"),
                category="weekly",
                user_id=user.id,
            ))
            #reminder for variable income(weekly)
            variable_incomes = Income.query.filter_by(
                user_id=user.id, 
                income_type="variable",
                is_confirmed=False
            ).all()
            for income in variable_incomes:
                db.session.add(Reminder(
                    message=(f"Have you received your '{income.name}' income this month?"),
                    category="weekly",
                    user_id=user.id,
                ))
            #archived once-off bills are done, no reminders for them
            user_payments = (Payment.query.filter_by(user_id=user.id)
                             .filter(Payment.is_archived != True).all())

            #weekly bills start a fresh week every Monday so they can be
            #ticked off again. nothing rolls over here, a missed week stays
            #in this month's total until the monthly job closes it off
            for p in user_payments:
                if p.frequency == "weekly":
                    p.is_paid = False
                    p.amount_paid = 0

            #variable bills like water and electricity: nag until this
            #month's amount is confirmed, this goes in the email too
            for p in user_payments:
                if p.bill_type == "variable" and not p.is_confirmed:
                    db.session.add(Reminder(
                        message=(f"Has the amount for '{p.name}' been updated this month? "
                                 f"It's currently {user.currency}{p.amount:.2f} - confirm it on the dashboard."),
                        category="weekly",
                        payment_id=p.id,
                        user_id=user.id,
                    ))

            #remind about overdue and upcoming bills
            for p in user_payments:
                status = get_status(p)
                if status == "overdue":
                    db.session.add(Reminder(
                        message=f"'{p.name}' ({user.currency}{p.amount:.2f}) is overdue! Was due on {due_phrase(p)}{description_note(p)}",
                        category="overdue",
                        payment_id=p.id,
                        user_id=user.id,
                    ))
                elif status == "soon":
                    days = days_until_due(p.due_day, p.is_paid)
                    #"in 0 days" reads badly, so say "today" instead
                    if days == 0:
                        timing = "is due today"
                    else:
                        timing = f"is due in {days} day{'s' if days != 1 else ''}"
                    db.session.add(Reminder(
                        message=f"'{p.name}' ({user.currency}{p.amount:.2f}) {timing}, {due_date_text(p)}{description_note(p)}",
                        category="soon",
                        payment_id=p.id,
                        user_id=user.id,
                    ))

        db.session.commit()

        #email AFTER committing: the reminders above only exist in the database
        #once committed, so reading them back has to happen in a second loop
        for user in User.query.all():
            email_unread_reminders(user, "Your Budget Buddy weekly reminders")


def create_monthly_reminders():
    #runs once a month, resets every bill back to not paid for every user

    with app.app_context():
        for user in User.query.all():
            #archived once-off bills are done, leave them out entirely
            payments = (Payment.query.filter_by(user_id=user.id)
                        .filter(Payment.is_archived != True).all())

            # new month so reset all of this user's payments
            last_year, last_month = previous_month()
            for p in payments:
                #weekly bills: close off the month that just ended. whatever
                #of its weeks went unpaid rolls over now. the week itself
                #resets on Mondays, not here
                if p.frequency == "weekly":
                    shortfall = round(
                        month_obligation(p, last_year, last_month)
                        - paid_in_month(p, last_year, last_month), 2)
                    if shortfall > 0:
                        p.carried_over = round((p.carried_over or 0) + shortfall, 2)
                    continue
                #once-off bills never reset, they stay until paid and archived
                if p.bill_type == "once_off":
                    continue
                #whatever wasn't paid rolls over instead of being forgotten
                shortfall = round(p.amount - (p.amount_paid or 0), 2)
                if not p.is_paid and shortfall > 0:
                    p.carried_over = round((p.carried_over or 0) + shortfall, 2)
                p.is_paid = False
                p.amount_paid = 0
                #variable bills need their new month's amount checked
                if p.bill_type == "variable":
                    p.is_confirmed = False

            #loans and store accounts: a new month means the bank adds its
            #charges, so grow the balance by interest + insurance + service fee,
            #then ask the user to check it against their real statement
            for p in payments:
                if p.bill_type in ("loan", "credit") and p.current_balance:
                    charges = []
                    increase = 0
                    if p.interest_rate:
                        interest = round(p.current_balance * (p.interest_rate / 100 / 12), 2)
                        increase += interest
                        charges.append(f"{user.currency}{interest:.2f} interest")
                    if p.loan_insurance:
                        increase += p.loan_insurance
                        charges.append(f"{user.currency}{p.loan_insurance:.2f} insurance")
                    if p.service_fee:
                        increase += p.service_fee
                        charges.append(f"{user.currency}{p.service_fee:.2f} service fee")
                    if increase:
                        p.current_balance = round(p.current_balance + increase, 2)
                        db.session.add(Reminder(
                            message=(f"'{p.name}': added {', '.join(charges)} to the balance for the new month "
                                     f"(now {user.currency}{p.current_balance:.2f}). Please make sure this matches your statement."),
                            category="monthly",
                            payment_id=p.id,
                            user_id=user.id,
                        ))

            #variable income reminder(monthly)
            variable_incomes = Income.query.filter_by(
                user_id=user.id,
                income_type="variable",
            ).all()
            for income in variable_incomes:
                income.is_confirmed = False

            # create a reminder for each bill
            for p in payments:
                db.session.add(Reminder(
                    message=(f"Monthly reminder: '{p.name}' ({user.currency}{p.amount:.2f}) is due on {due_phrase(p)}{description_note(p)}"),
                    category="monthly",
                    payment_id=p.id,
                    user_id=user.id,
                ))

            #add a reminder that gives a summary of the monthly bills (if there are bills)
            if payments:
                total = sum(month_obligation(p) for p in payments)
                db.session.add(Reminder(
                    message=(f"New month! You have {len(payments)} bills totalling {user.currency}{total:.2f} to stay on top of. You've got this!"),
                    category="monthly",
                    user_id=user.id,
                ))

        db.session.commit()

        #email AFTER committing, for the same reason as the weekly job
        for user in User.query.all():
            email_unread_reminders(user, "Your Budget Buddy monthly reminders")


#-------------------------------------------------------------------#
#-------------------------Scheduler---------------------------------#
#-------------------------------------------------------------------#


#create background scheduler that runs reminder functions
scheduler = BackgroundScheduler()

#WEEKLY: every monday at 09:00 AM, ask about new bills
#trigger = "cron"
scheduler.add_job(
    func=create_weekly_reminder,
    trigger="cron",
    day_of_week="mon",
    hour=9,
    minute=0,
    id="weekly_reminder",
)

#MONTHLY: on the 1st of each month at 9 am, reset paid status
scheduler.add_job(
    func=create_monthly_reminders,
    trigger="cron",
    day=1,
    hour=9,
    minute=0,
    id="monthly_reminder",
)

 
#TESTING
# The reminders  only fire on their REAL schedule
# To test quickly, temporarily replace a job's trigger with an
# interval one, for example:
#
#     scheduler.add_job(func=create_weekly_reminder,
#                       trigger="interval", seconds=15, id="weekly_reminder")
#
# That makes it run every 15 seconds. Change it back when done testing.
# ---------------------------------------------------------------------------



#-------------------------------------------------------------------#
#--------------------------Start Everything-------------------------#
#-------------------------------------------------------------------#

def seed_dev_admin():
    """ Make or refresh the account used for local testing.
    The live database can't be opened from a laptop, so local runs need
    their own login. This resets its password on every local start, so it
    always lets you in.

    Two things keep it off the live site: it only runs under __main__, and
    the host serves through WSGI so it is never reached there; and it does
    nothing unless DEV_ADMIN_PASSWORD is set, which is only in the local .env.
    The login route itself is untouched, there is no "any password" branch """
    password = os.environ.get("DEV_ADMIN_PASSWORD")
    if not password:
        return
    email = os.environ.get("DEV_ADMIN_EMAIL", "budgetbuddysite@gmail.com")
    username = os.environ.get("DEV_ADMIN_USER", "Buddy")

    user = User.query.filter_by(email=email).first()
    action = "password reset for"
    if user is None:
        user = User(username=username, email=email)
        db.session.add(user)
        action = "created"
    user.set_password(password)
    db.session.commit()
    print(f"[dev] local test account {action}: log in as '{user.username}'")


if __name__ == "__main__":
    #create database tables if they dont exist yet
    with app.app_context():
        db.create_all()
        #local testing login, does nothing unless DEV_ADMIN_PASSWORD is set
        seed_dev_admin()

    #start the scheduler
    scheduler.start()

    #start web server
    #once running open http://127.0.0.1:5000
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true", use_reloader=False)
