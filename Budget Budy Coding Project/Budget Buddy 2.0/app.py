
#For your final project (and your final project only!) 
# it is reasonable to use AI-based software other than CS50’s own (e.g., ChatGPT, GitHub Copilot, Bing Chat, et al.), 
# but the essence of the work must still be your own. 
# You’ve learned enough to use such tools as helpers. 
# Treat such tools as amplifying, not supplanting, your productivity. 
#But you still must cite any use of such tools in the comments of your code.

#claude code was used to help determine what the skeleton of the app would be.
#determining what is neccesary

"""
#------------------------------------------------------------------------------#
#-------Budget Buddy - Budgeting + Reminder App Built with Flask & Python------#
#------------------------------------------------------------------------------#

What this app does
    - add monthly bills / subscriptions
    - once a week the app sends a gentle reminder asking if the user has any new bills or subscriptions to add
    - once a week sends a reminder about upcoming payments and overdue bills
    - once a month send a reminder of all the bills and reset the previous months budget sheet
    - which means that if a bill was marked as paid in june the new page saying july will mark everything as not paid
"""
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
import datetime
import calendar

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-fallback")

db = SQLAlchemy(app)

"""
TODO: Currencies?
double check what happens with months that have 30 days vs 31 vs 28 or 29
split datetime between date and time as seperate columns?
"""

 
#------------------------------------------------------------------------------#
#--------------------Setting up Python Classes / Database models---------------#
#------------------------------------------------------------------------------#

class Payment(db.Model):
#unique id number, primary_key=True as means database is filled with unique identifiers automatically
    id = db.Column(db.Integer, primary_key=True)

#description of bill or subscription
    description = db.Column(db.String(100), nullable=False)

#cost of subscription in float to allow for decimals
    amount = db.Column(db.Float, nullable=False)

#day of the month which the next payment is due
    due_day = db.Column(db.Integer, nullable=False)

#true or fals of whether payment as been made or not
    is_paid = db.Column(db.Boolean, default=False)

#captures exactly when a new bill or subscription was added
    date_added = db.Column(db.DateTime, default=datetime.datetime.now)

class Reminder(db.Model):
    #send a message to user

    id = db.Column(db.Integer, primary_key=True)

#reminder text
    message = db.Column(db.String(255), nullable=False)

#weekly or monthly reminder
    catagory = db.Column(db.String(20), nullable=False)

#ticked off yes or no
    is_read = db.Column(db.Boolean, default=False)

#snap of when reminder was created
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)



#----------------------------------------------------------------------#
#---------------------------Helper Functions---------------------------#
#----------------------------------------------------------------------#

def days_until_due(due_day, is_paid=False):
    """     Works out when next bill is due    """

    today = datetime.datetime.now()

    #make sure due day in not more than days in month
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
    "paid"      -> already paid this month                  (green)
    "overdue"   -> due day has passed and bill not paid     (soft red)
    "soon"      -> due within the next 5 days, not yet paid (amber)
    "upcoming"  -> not paid, but not due for a while        (neutral)

    """
    if payment.is_paid:
        return "paid"
    
    today  = datetime.datetime.now()
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
#-----------------ROUTES - each function references a web page----------------#
#-----------------------------------------------------------------------------#


@app.route("/")
def dashboard():
    """ Home Page: Show totals,upcoming reminders and overdues, list of bills"""

    #get every payment from the database, sorted by due day first
    payments = Payment.query.order_by(Payment.due_day).all()

    #build a list pairing each poament with it's status + days left
    payments_with_status = []
    for p in payments:
        payments_with_status.append({
            "payment": p,
            "status": get_status(p),
            "days_left": days_until_due(p.due_day, p.is_paid),
        })

    # usefull monthly totals
    total_monthly = sum(p.amount for p in payments)
    unpaid = [p for p in payments if not p.is_paid]
    total_unpaid = sum(p.amount for p in unpaid)

    unread_reminders = (
        Reminder.query.filter_by(is_read=False)
        .order_by(Reminder.created_at.desc())
        .all()
    )

    # reder template loads dashboard.html and loads the values passed top it
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

    """   Show add bill or subscription form and save when sumbitted   """

#create the form the user will use
    if request.method == "POST":
#add name = request.form[name] which will be the name of the form, 
# wheras description will be, more detailed, 
# e.g. name = Spotify, description = spotify premium patinum duo, paid via vodacom airtime deduction
        name = request.form["name"]
        description = request.form["description"]
        amount = float(request.form["amount"])
        due_day = int(request.form["due_day"])

#create a new payment, one row of information
        new_payment = Payment(
            name=name,
            description=description,
            amount=amount,
            due_day=due_day,
        )

#add to database
        db.session.add(new_payment)
        db.session.commit()

#show a message after commit to show it was added succesfuly
        flash(f"Added '{name}' successfully to your bills.", "success")
        return redirect(url_for("dashboard"))

    #when request is just GET then show empty form
    return render_template("add_payment.html")

@app.route("/pay/<int:payment_id>")
def mark_paid(payment_id):
    """ Mark a Bill as PAID for this month. payment_id is the bill's id """
    #find the bill by its id, or show a 404 if it is not found
    payment = Payment.query.get_or_404(payment_id)
    payment.is_paid = True      #turns status to paid
    db.session.commit()         #saves the status change
    flash(f"'{payment.name}' is paid.", "success") # green
    return redirect(url_for("dashboard"))

@app.route("/unpaid/<int:payment_id>")
def mark_unpaid(payment_id):
    """ mark a bill as unpaid """
    payment = Payment.query.get_or_404(payment_id)
    payment.is_paid = False
    db.session.commit()
    flash(f"'{payment.name}' has not been paid yet.", "warning") # yellow