"""
============================================================================
 BUDGET BUDDY  -  a simple budgeting + reminder app built with Flask & Python
============================================================================

WHAT THIS APP DOES (in plain English):
  - You add your regular monthly bills / subscriptions (name, amount, due day).
  - The home page shows, at a glance, what's PAID, what's COMING UP, what's OVERDUE.
  - Once a WEEK the app creates a gentle reminder asking if you've added any NEW bills.
  - Once a MONTH the app reminds you about each bill and resets everything for
    the fresh month (so nothing is marked "paid" until you pay it again).

It's designed to be calm and low-effort to use, which helps if you have ADHD.

This file is written to be BEGINNER FRIENDLY, so there are lots of comments
explaining what every part does. Read it top to bottom like a story.
"""

# ---------------------------------------------------------------------------
# 1. IMPORTS  -  we bring in the tools (libraries) we need.
# ---------------------------------------------------------------------------

# Flask is the web framework. We import the specific pieces we use:
#   Flask           -> creates the web application itself
#   render_template -> turns an HTML file (a "template") into a web page
#   request         -> lets us read data the user typed into a form
#   redirect        -> sends the user's browser to a different page
#   url_for         -> builds the URL for one of our pages using its function name
#   flash           -> shows a short, one-time message to the user (e.g. "Saved!")

from flask import Flask, render_template, request, redirect, url_for, flash

# Flask-SQLAlchemy lets us save data in a database using normal Python classes
# instead of writing raw database (SQL) code. A "database" here is just a file
# where our bills are stored, so they're still there after we close the app.
# APScheduler runs functions automatically on a schedule, in the background.
# We use it to create the weekly and monthly reminders without you doing anything.

from flask_sqlalchemy import SQLAlchemy


from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import calendar  # helps us find how many days are in a given month


# ---------------------------------------------------------------------------
# 2. CREATE AND CONFIGURE THE APP
# ---------------------------------------------------------------------------

# This creates our Flask application. __name__ tells Flask where this file is,
# so it knows where to look for the "templates" and "static" folders.
app = Flask(__name__)

# A "secret key" is required for flash messages to work safely.
# For a real, public app you'd keep this private. For learning, any text is fine.
app.config["SECRET_KEY"] = "change-this-to-something-secret"

# Tell the database where to live. "sqlite:///budget.db" means:
# "use a simple file-based database saved in a file called budget.db".
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///budget.db"

# Connect the database tool to our Flask app.
db = SQLAlchemy(app)


# ---------------------------------------------------------------------------
# 3. DATABASE MODELS  -  Python classes that describe what we store.
# ---------------------------------------------------------------------------
#
# "model" is a class that represents a TABLE in the database.
# Each attribute (db.Column) is a COLUMN in that table.
# Each saved item (e.g. one bill) is a ROW.

class Payment(db.Model):
    """One regular bill or subscription, e.g. 'Netflix, £10.99, due on the 5th'."""

    # Every row needs a unique id number. primary_key=True makes this the main
    # identifier; the database fills it in automatically (1, 2, 3, ...).
    id = db.Column(db.Integer, primary_key=True)

    # The name/description of the bill, e.g. "Rent" or "Spotify".
    # nullable=False means this field is required (can't be empty).
    description = db.Column(db.String(100), nullable=False)

    # How much it costs each time. db.Float stores numbers with decimals (£10.99).
    amount = db.Column(db.Float, nullable=False)

    # The day of the month it's due (1-31). e.g. 5 means "the 5th".
    due_day = db.Column(db.Integer, nullable=False)

    # Have we paid it this month yet? True or False. Starts as False (not paid).
    is_paid = db.Column(db.Boolean, default=False)

    # When this bill was first added. Filled in automatically with the current
    # date/time. Note: we pass `datetime.now` WITHOUT brackets so the database
    # calls it at the moment a row is created.
    date_added = db.Column(db.DateTime, default=datetime.now)


class Reminder(db.Model):
    """A single reminder message that the app has generated for you to see."""

    id = db.Column(db.Integer, primary_key=True)

    # The reminder text, e.g. "Don't forget: Rent is due soon!".
    message = db.Column(db.String(255), nullable=False)

    # What kind of reminder it is: "weekly" or "monthly". We use this to colour it.
    category = db.Column(db.String(20), nullable=False)

    # Has the user ticked it off? Starts as False (unread / still showing).
    is_read = db.Column(db.Boolean, default=False)

    # When the reminder was created.
    created_at = db.Column(db.DateTime, default=datetime.now)


# ---------------------------------------------------------------------------
# 4. HELPER FUNCTIONS  -  small, reusable bits of logic.
# ---------------------------------------------------------------------------

def days_until_due(due_day):
    """
    Work out how many days from TODAY until a bill's due day.

    Example: today is the 3rd, the bill is due on the 5th -> returns 2.
    If the due day has already passed this month, we count to next month's date.
    """
    today = datetime.now()

    # Some months are shorter (February!), so make sure the due day isn't bigger
    # than the number of days this month has. calendar.monthrange(...)[1] gives
    # the number of days in the given month.
    days_in_this_month = calendar.monthrange(today.year, today.month)[1]
    safe_due_day = min(due_day, days_in_this_month)

    if today.day <= safe_due_day:
        # The due day is still coming up later this month.
        return safe_due_day - today.day
    else:
        # The due day has passed, so the next one is next month. Count the days
        # left in this month, then add the due day in the next month.
        days_left_this_month = days_in_this_month - today.day
        return days_left_this_month + due_day


def get_status(payment):
    """
    Return a simple status WORD for a bill so the page can colour-code it:
      "paid"     -> already paid this month            (green)
      "overdue"  -> due day has passed and not paid     (soft red)
      "soon"     -> due within the next 5 days, not paid (amber)
      "upcoming" -> not paid, but not due for a while    (neutral)
    """
    # If it's already paid, we're done.
    if payment.is_paid:
        return "paid"

    today = datetime.now()
    days_in_this_month = calendar.monthrange(today.year, today.month)[1]
    safe_due_day = min(payment.due_day, days_in_this_month)

    # If today is past the due day and it's still not paid -> overdue.
    if today.day > safe_due_day:
        return "overdue"

    # If it's due within 5 days -> "soon".
    if days_until_due(payment.due_day) <= 5:
        return "soon"

    # Otherwise it's just upcoming.
    return "upcoming"


# ---------------------------------------------------------------------------
# 5. ROUTES  -  each function below handles ONE web page (a URL).
# ---------------------------------------------------------------------------
# The @app.route("...") line above a function says "when someone visits this
# URL, run this function". Whatever the function returns becomes the web page.

@app.route("/")
def dashboard():
    """The HOME page: shows totals, reminders, and the list of bills."""

    # Get every payment from the database, sorted by due day (earliest first).
    payments = Payment.query.order_by(Payment.due_day).all()

    # Build a list pairing each payment with its status + days left, so the
    # template can easily display the right colour and text.
    payments_with_status = []
    for p in payments:
        payments_with_status.append({
            "payment": p,
            "status": get_status(p),
            "days_left": days_until_due(p.due_day),
        })

    # Work out some helpful totals.
    total_monthly = sum(p.amount for p in payments)      # cost of ALL bills
    unpaid = [p for p in payments if not p.is_paid]       # bills not yet paid
    total_unpaid = sum(p.amount for p in unpaid)          # money still to pay

    # Get the reminders the user hasn't ticked off yet (newest first).
    unread_reminders = (
        Reminder.query.filter_by(is_read=False)
        .order_by(Reminder.created_at.desc())
        .all()
    )

    # render_template loads dashboard.html and fills in the values we hand it.
    return render_template(
        "dashboard.html",
        payments_with_status=payments_with_status,
        total_monthly=total_monthly,
        total_unpaid=total_unpaid,
        unpaid_count=len(unpaid),
        reminders=unread_reminders,
    )


@app.route("/add", methods=["GET", "POST"])
def add_payment():
    """
    Show the 'add a bill' form (GET) and save it when submitted (POST).

    methods=["GET", "POST"] means this page handles both situations:
      - GET  = the user is just LOOKING at the form
      - POST = the user filled it in and clicked Save
    """
    if request.method == "POST":
        # request.form holds what the user typed. The keys ("description" etc.)
        # match the name="..." attributes on the <input> fields in the HTML.
        description = request.form["description"]
        amount = float(request.form["amount"])   # text -> number with decimals
        due_day = int(request.form["due_day"])    # text -> whole number

        # Create a new Payment (one row) using the form data.
        new_payment = Payment(
            description=description,
            amount=amount,
            due_day=due_day,
        )

        # Add it to the database, then commit (save) the change permanently.
        db.session.add(new_payment)
        db.session.commit()

        # Show a friendly success message, then send the user back home.
        flash(f"Added '{description}' to your bills. Nice one!", "success")
        return redirect(url_for("dashboard"))

    # If it's a GET request, just show the empty form.
    return render_template("add_payment.html")


@app.route("/pay/<int:payment_id>")
def mark_paid(payment_id):
    """Mark one bill as PAID for this month. <int:payment_id> is the bill's id."""
    # Find the bill by its id, or automatically show a 404 page if it's missing.
    payment = Payment.query.get_or_404(payment_id)
    payment.is_paid = True        # flip the switch to "paid"
    db.session.commit()           # save the change
    flash(f"Marked '{payment.description}' as paid. Well done!", "success")
    return redirect(url_for("dashboard"))


@app.route("/unpay/<int:payment_id>")
def mark_unpaid(payment_id):
    """Undo: mark a bill as NOT paid again (in case you tapped it by mistake)."""
    payment = Payment.query.get_or_404(payment_id)
    payment.is_paid = False
    db.session.commit()
    flash(f"Marked '{payment.description}' as not paid yet.", "success")
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:payment_id>")
def delete_payment(payment_id):
    """Remove a bill completely (e.g. you cancelled a subscription)."""
    payment = Payment.query.get_or_404(payment_id)
    db.session.delete(payment)
    db.session.commit()
    flash(f"Deleted '{payment.description}'.", "success")
    return redirect(url_for("dashboard"))


@app.route("/reminders")
def reminders():
    """A page listing ALL reminders (read and unread), newest first."""
    all_reminders = Reminder.query.order_by(Reminder.created_at.desc()).all()
    return render_template("reminders.html", reminders=all_reminders)


@app.route("/reminders/read/<int:reminder_id>")
def mark_reminder_read(reminder_id):
    """Tick off a reminder so it stops showing on the home page."""
    reminder = Reminder.query.get_or_404(reminder_id)
    reminder.is_read = True
    db.session.commit()
    # request.referrer is "the page you came from", so we return there.
    return redirect(request.referrer or url_for("dashboard"))


# ---------------------------------------------------------------------------
# 6. SCHEDULED REMINDER JOBS  -  functions that run automatically.
# ---------------------------------------------------------------------------

def create_weekly_reminder():
    """
    Runs once a WEEK. Creates a gentle nudge asking whether you've signed up
    for any new bills or subscriptions recently.
    """
    # Background jobs run OUTSIDE a normal web request. To use the database from
    # here we must temporarily "enter" the app context, like this:
    with app.app_context():
        reminder = Reminder(
            message=("Weekly check-in: have you added any new bills or "
                     "subscriptions this week? Tap '+ Add a bill' if so."),
            category="weekly",
        )
        db.session.add(reminder)
        db.session.commit()


def create_monthly_reminders():
    """
    Runs once a MONTH (on the 1st). It:
      1. Resets every bill back to 'not paid' for the new month.
      2. Creates a reminder for each bill so you know what's coming.
    """
    with app.app_context():
        payments = Payment.query.all()

        # Step 1: it's a new month, so nothing is paid yet -> reset them all.
        for p in payments:
            p.is_paid = False

        # Step 2: make a reminder for each bill.
        for p in payments:
            db.session.add(Reminder(
                message=(f"Monthly reminder: '{p.description}' (£{p.amount:.2f}) "
                         f"is due on day {p.due_day} this month."),
                category="monthly",
            ))

        # Add one friendly summary reminder too (only if there are bills).
        if payments:
            total = sum(p.amount for p in payments)
            db.session.add(Reminder(
                message=(f"New month! You have {len(payments)} bills totalling "
                         f"£{total:.2f} to stay on top of. You've got this."),
                category="monthly",
            ))

        db.session.commit()


# ---------------------------------------------------------------------------
# 7. SET UP THE SCHEDULER
# ---------------------------------------------------------------------------

# Create the background scheduler that will run our reminder functions.
scheduler = BackgroundScheduler()

# WEEKLY job: every Monday at 9:00 AM, ask about new bills.
#   trigger="cron" lets us schedule by calendar (like a recurring alarm).
#   day_of_week="mon" -> Mondays.   hour=9, minute=0 -> 09:00.
scheduler.add_job(
    func=create_weekly_reminder,
    trigger="cron",
    day_of_week="mon",
    hour=9,
    minute=0,
    id="weekly_reminder",
)

# MONTHLY job: on the 1st of every month at 9:00 AM, reset paid status + remind.
#   day=1 -> the 1st of the month.
scheduler.add_job(
    func=create_monthly_reminders,
    trigger="cron",
    day=1,
    hour=9,
    minute=0,
    id="monthly_reminder",
)

# ---- TIP FOR TESTING -------------------------------------------------------
# The reminders above only fire on their REAL schedule, so you might wait days
# to see one! To test quickly, temporarily replace a job's trigger with an
# interval one, for example:
#
#     scheduler.add_job(func=create_weekly_reminder,
#                       trigger="interval", seconds=15, id="weekly_reminder")
#
# That makes it run every 15 seconds. Change it back when you're done testing.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 8. START EVERYTHING
# ---------------------------------------------------------------------------

# This block only runs when you start THIS file directly (python app.py).
if __name__ == "__main__":
    # Create the database tables the first time we run (if they don't exist yet).
    # We need the app context to talk to the database.
    with app.app_context():
        db.create_all()

    # Start the background scheduler so reminders can fire while the app runs.
    scheduler.start()

    # Start the web server.
    #   debug=True         -> shows helpful error pages while you're building.
    #   use_reloader=False -> stops Flask from restarting itself twice, which
    #                         would otherwise start the scheduler twice and make
    #                         duplicate reminders.
    #
    # Once it's running, open this address in your web browser:
    #   http://127.0.0.1:5000
    app.run(debug=True, use_reloader=False)
