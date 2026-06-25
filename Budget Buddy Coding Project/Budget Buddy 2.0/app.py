#For your final project (and your final project only!)
# it is reasonable to use AI-based software other than CS50's own (e.g., ChatGPT, GitHub Copilot, Bing Chat, et al.),
# but the essence of the work must still be your own.
# You've learned enough to use such tools as helpers.
# Treat such tools as amplifying, not supplanting, your productivity.

#But you still must cite any use of such tools in the comments of your code.

#claude code was used to help determine what the skeleton of the app would be.
#determining what is necessary
#claude code was also used to help add user accounts (login/register) to the app.
#claude was used to help me restore much of my lost data when i misunderstood what i was doing and pressed overwrite save
#claude helped determine what to import and also to look for and fix typos and syntax errors

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
"""
#imports
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import datetime
import calendar

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-fallback")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///budget.db"

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

#the username, must be unique
    username = db.Column(db.String(80), unique=True, nullable=False)

#NEVER stores the real password
    password_hash = db.Column(db.String(255), nullable=False)

    #symbol shown before every money amount (e.g. R, $, €, £)
    currency = db.Column(db.String(5), nullable=False, default="R")

    budget_limit = db.Column(db.Float, nullable=True)

#a user's bills and reminders.
    payments = db.relationship("Payment", backref="user", lazy=True)
    reminders = db.relationship("Reminder", backref="user", lazy=True)
    incomes = db.relationship("Income", backref="user", lazy=True)

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

#for credit accounts: minimum % of balance due each month
    minimum_payment_percent = db.Column(db.Float, nullable=True)

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

    #variable income, confirm if that months amount has been confirmed.
    #reset to False each month automatically
    is_confirmed = db.Column(db.Boolean, default=True)

    #which user the income belongs to
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


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

    today = datetime.datetime.now()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    due_day = min(payment.due_day, days_in_month)

    #if today is past due day and bill is not paid -> overdue
    if today.day > due_day:
        return "overdue"

    #if due within 5 days -> "soon"
    if days_until_due(payment.due_day, payment.is_paid) <= 5:
        return "soon"

    return "upcoming"

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
        password = request.form["password"]
        confirm = request.form["confirm"]

        #simple checks before we create the account
        if not username or not password:
            flash("Please fill in both a username and a password.", "warning")
            return redirect(url_for("register"))

        if password != confirm:
            flash("Those passwords don't match. Try again.", "warning")
            return redirect(url_for("register"))

        #is the username already taken
        if User.query.filter_by(username=username).first():
            flash("That username is already taken. Pick another.", "warning")
            return redirect(url_for("register"))

        #make the user, scramble the password, save user
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
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


#-----------------------------------------------------------------------------#
#-----------------ROUTES - each function references a web page----------------#
#-----------------------------------------------------------------------------#

@app.route("/")
@login_required
def dashboard():
    """ Home Page: Show totals,upcoming reminders and overdues, list of bills"""

    filter_status = request.args.get("filter", "all")
    sort_by = request.args.get("sort", "due_day")

    #get only this user's payments, sorted by due day first
    payments = (
        Payment.query.filter_by(user_id=current_user.id)
        .order_by(Payment.due_day)
        .all()
    )

    #build a list pairing each payment with its status + days left
    payments_with_status = []
    for p in payments:
        payments_with_status.append({
            "payment": p,
            "status": get_status(p),
            "days_left": days_until_due(p.due_day, p.is_paid),
        })

    #filter
    if filter_status != "all":
        payments_with_status = [p for p in payments_with_status if p["status"] == filter_status]
   
    #sort
    if sort_by =="amount":
        payments_with_status.sort(key=lambda p: p["payment"].amount, reverse=True)
    elif sort_by == "name":
        payments_with_status.sort(key=lambda p: p["payment"].name.lower())

    # useful monthly totals
    total_monthly = sum(p.amount for p in payments)
    unpaid = [p for p in payments if not p.is_paid]
    total_unpaid = sum(p.amount - (p.amount_paid or 0) for p in unpaid)

    #only this user's unread reminders
    unread_reminders = (
        Reminder.query.filter_by(user_id=current_user.id, is_read=False)
        .order_by(Reminder.created_at.desc())
        .all()
    )

    over_budget = (current_user.budget_limit is not None and total_monthly > current_user.budget_limit)

    income_sources = Income.query.filter_by(user_id=current_user.id).all()
    total_income = sum(i.amount for i in income_sources)
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
        amount = float(request.form["amount"])
        due_day = int(request.form["due_day"])
        payment_method = request.form.get("payment_method") or None
        bill_type = request.form.get("bill_type", "fixed")
        raw_total = request.form.get("total_value")
        raw_balance = request.form.get("current_balance")
        total_value = float(raw_total) if raw_total else None
        current_balance = float(raw_balance) if raw_balance else None

        
        raw_interest = request.form.get("interest_rate")
        raw_months = request.form.get("months_remaining")
        raw_insurance = request.form.get("loan_insurance")
        raw_initiation = request.form.get("initiation_fee")
        interest_rate = float(raw_interest) if raw_interest else None
        months_remaining = int(raw_months) if raw_months else None
        loan_insurance = float(raw_insurance) if raw_insurance else None
        initiation_fee = float(raw_initiation) if raw_initiation else None


        raw_min_pay = request.form.get("minimum_payment_percent")
        minimum_payment_percent = float(raw_min_pay) if raw_min_pay else None


#create a new payment, one row of information, owned by the logged-in user
        new_payment = Payment(
            name=name,
            description=description,
            amount=amount,
            due_day=due_day,
            payment_method=payment_method,
            bill_type=bill_type,
            total_value=total_value,
            current_balance=current_balance,
            user_id=current_user.id,
            
            interest_rate=interest_rate,
            months_remaining=months_remaining,
            loan_insurance=loan_insurance,
            initiation_fee=initiation_fee,

            minimum_payment_percent=minimum_payment_percent
        )

#add to database
        db.session.add(new_payment)
        db.session.commit()

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
        amount=float(request.form["amount"])
        income_type = request.form.get("income_type", "fixed")

        new_income = Income(
            name=name,
            amount=amount,
            income_type=income_type,
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
        payment.amount = float(request.form["amount"])
        payment.due_day = int(request.form["due_day"])
        payment.payment_method = request.form.get("payment_method") or None
        payment.bill_type = request.form.get("bill_type", "fixed")
        
        raw_total = request.form.get("total_value")
        raw_balance = request.form.get("current_balance")
        
        payment.total_value = float(raw_total) if raw_total else None
        payment.current_balance = float(raw_balance) if raw_balance else None
        
        raw_interest = request.form.get("interest_rate")
        raw_months = request.form.get("months_remaining")
        raw_insurance = request.form.get("loan_insurance")
        raw_initiation = request.form.get("initiation_fee")

        payment.interest_rate = float(raw_interest) if raw_interest else None
        payment.months_remaining = int(raw_months) if raw_months else None
        payment.loan_insurance = float(raw_insurance) if raw_insurance else None
        payment.initiation_fee = float(raw_initiation) if raw_initiation else None
        raw_min_pay = request.form.get("minimum_payment_percent")
        payment.minimum_payment_percent = float(raw_min_pay) if raw_min_pay else None

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
        income.amount = float(request.form["amount"])
        income.income_type = request.form.get("income_type", "fixed")
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
        payment.amount_paid = float(raw)
        if payment.amount_paid >= payment.amount:
            payment.is_paid = True
            payment.amount_paid = payment.amount
        else:
            payment.is_paid = False
    db.session.commit()
    flash(f"Payment recorded for '{payment.name}'.","success")
    return redirect(url_for("dashboard"))



@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        current_user.currency = request.form.get("currency", "R")
        raw_limit = request.form.get("budget_limit") 
        current_user.budget_limit = float(raw_limit) if raw_limit else None
        db.session.commit()
        flash("Settings saved", "saved")
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
    payment.is_paid = True      #turns status to paid
    db.session.commit()         #saves the status change
    flash(f"'{payment.name}' is paid.", "success") # green
    return redirect(url_for("dashboard"))

@app.route("/unpaid/<int:payment_id>")
@login_required
def mark_unpaid(payment_id):
    """ mark a bill as unpaid """
    payment = get_owned_payment_or_404(payment_id)
    payment.is_paid = False
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
        income.amount = float(raw)
    income.is_confirmed = True
    db.session.commit()
    flash(f"Income '{income.name}' confirmed.", "success")
    return redirect(url_for("dashboard"))

@app.route("/update_balance/<int:payment_id>", methods=["POST"])
@login_required
def update_balance(payment_id):
    """ Update the current balance for a loan or credit account bill """
    payment = get_owned_payment_or_404(payment_id)
    payment.current_balance = float(request.form["new_balance"])
    db.session.commit()
    flash(f"Balance updated for '{payment.name}'.", "success")
    return redirect(url_for("dashboard"))


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
        #remind about overdue and upcoming bills
            user_payments = Payment.query.filter_by(user_id=user.id).all()
            for p in user_payments:
                status = get_status(p)
                if status == "overdue":
                    db.session.add(Reminder(
                        message=f"'{p.name}' ({user.currency}{p.amount:.2f}) is overdue! Due on the {ordinal_day(p.due_day)}.",
                        category="overdue",
                        user_id=user.id,
                    ))
                elif status == "soon":
                    days = days_until_due(p.due_day, p.is_paid)
                    db.session.add(Reminder(
                        message=f"'{p.name}' ({user.currency}{p.amount:.2f}) is due in {days} day{'s' if days != 1 else ''}.",
                        category="soon",
                        user_id=user.id,
                    ))

        db.session.commit()


def create_monthly_reminders():
    #runs once a month, resets every bill back to not paid for every user

    with app.app_context():
        for user in User.query.all():
            payments = Payment.query.filter_by(user_id=user.id).all()

            # new month so reset all of this user's payments
            for p in payments:
                if p.bill_type != "once_off" or not p.is_paid:
                    p.is_paid = False
                    p.amount_paid = 0

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
                    message=(f"Monthly reminder: '{p.name}' ({user.currency}{p.amount:.2f}) is due on the {ordinal_day(p.due_day)} this month"),
                    category="monthly",
                    user_id=user.id,
                ))

            #add a reminder that gives a summary of the monthly bills (if there are bills)
            if payments:
                total = sum(p.amount for p in payments)
                db.session.add(Reminder(
                    message=(f"New month! You have {len(payments)} bills totalling {user.currency}{total:.2f} to stay on top of. You've got this!"),
                    category="monthly",
                    user_id=user.id,
                ))

        db.session.commit()


#-------------------------------------------------------------------#
#-------------------------Scheduler---------------------------------#
#-------------------------------------------------------------------#


#create background scheduler that runs reminder functions
scheduler = BackgroundScheduler()

#WEEKLY: every monday at 09:00 AM, ask about new bills
#trigger = "cron"
scheduler.add_job(
    func=create_weekly_reminder,
    trigger="interval",
    seconds=15,
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
# The reminders above only fire on their REAL schedule, so you might wait days
# to see one! To test quickly, temporarily replace a job's trigger with an
# interval one, for example:
#
#     scheduler.add_job(func=create_weekly_reminder,
#                       trigger="interval", seconds=15, id="weekly_reminder")
#
# That makes it run every 15 seconds. Change it back when you're done testing.
# ---------------------------------------------------------------------------



#-------------------------------------------------------------------#
#--------------------------Start Everything-------------------------#
#-------------------------------------------------------------------#

if __name__ == "__main__":
    #create database tables if they dont exist yet
    with app.app_context():
        db.create_all()

    #start the scheduler
    scheduler.start()

    #start web server
    #once running open http://127.0.0.1:5000
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true", use_reloader=False)
