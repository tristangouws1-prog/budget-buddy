This is my submission for CS50's final project.


1. My project is called Budget Buddy. It is a budgeting app that sends the user reminders to add new bills, 
or sources of income, as well as to pay them when they are due and send reminders when they are overdue.
The app is meant to be useful for people with adhd who have a high tendency of forgetting things like bills because of 
object permanence. 
The app is called Budget Buddy because I plan on adding a small pet-like character to the left part of the screen that helps the user.
The buddy would get points whenever you successfully pay bills etc. 
The idea is that the buddy is the one reminding you to add or pay bills
acting as a body doubling friend. I have not added this function yet because I do not know how yet. 
I plan on learning much more and
continuing to grow this project.


2. app.py is the main file. It is a Flask app that claude code helped with whenever I hit a brick wall. I asked claude to give me ideas, 
help me understand concepts, look for bug fixes as well as teach me how to commit and push code to github. 
All the code in the project was manually typed by me except for bug fixes and typos that claude helped with.
app.py contains the database models(User, Payment, Income, Reminder), 
all the routes(dashboard, add/edit/delete, login/register, settings), 
helper functions(get_status() and ordinal_day()) 
and the APScheduler that sends weekly and monthly reminders.


3. style.css is the file that determines the look, colour scheme, spacing etc. of all the .html files
This page determines the colours like red for overdue and green for paid.
Consistent use of rounded edges on the card layouts to ensure a calm layout.
I chose a calm colour scheme because the app is supposed to be easy to use and ease stress. 
The soft colour palettes are meant to be peaceful.
The Stylesheet also includes a mobile friendly version.
I used CSS variables to store the colour scheme in one place and then reuse those values
accross the rest of the stylesheet to keep a consistant colour palette.

4. dashboard.html is the main page that is seen when a user logs in. 
This page shows them their income, expenses, how much is left in their budget and all the reminders that are sent are shown here in different colours, red, amber and green.
The dashboard page shows all the important information in one place. 
The bills have a per-bill progress bar showing how much of the bill has been paid if only a partial payment was made.
Loans and credit cards also have progress bars as well as the budget-limit warning.
The page allows the user to filter/sort by overdue.


5. add_payment.html and edit_payment.html are where payments are added and after 
they are added edit_payment.html helps to fix any errors such as typos or an incorrect amount or date.
add_payment allows for multiple bill types like once-off bills, subscriptions, loans and credit.
Depending on the bill type different fields are shown or hidden using Javascript.



6. add_income.html/edit_income.html are pages where sources of income are added and edited if any mistakes were made or updates
happened. Income can either be a "fixed" value each month such as a salary or it can be a "variable"
income that changes from month to month, such as income received from freelance work.
Variable income defaults to unconfirmed each month, and asks the user via a reminder if they have received
an income yet.


7. settings.html is a page where the user can change their currency preference, this is useful because the currency symbol
is used across the entire app and each user has the option to customise their preferred currency. To further upgrade this
I am considering either adding more currency options, or adding a field where a custom currency can be typed in.
The settings page also allows the user to set and update a custom budget limit.
This is useful as the budget limit might be different from total income.


8. reminders.html is a page that shows the full history of reminders that has been sent to the user. 
Reminders that have been dismissed are dimmed and they are sorted newest first. 
Using python's "strftime" also converts the dates to an easily readable format.


9. login.html / register.html are the pages that handle account creation and subsequently login authentication.
When a user registers, their password is never stored, it is hashed using Werkzeug's security functions.
Login re-hashes the password and compares it to the stored hash.
Flask-login keeps track of who is logged in and the "@login_required" line protects
each page so that a user has to be logged in to the correct account to view the relevant page, otherwise they are 
redirected to the login page.
Database queries are filtered using the user's id, which means each user can only see their own account details.


10.Design Choices:

SQLite was chosen as the database because Budget Buddy runs locally and only for one user at a time. A larger database would have added setup complexity
and that is unnecessary for the scope of this project. The trade-off is that Budget Buddy is not currently suited to handle thousands of users, but that is not the goal here.

For the automated reminders I used APScheduler with cron triggers rather than a system-level cron job. I did this because my research led me to believe a system cron runs
at the operating-system level and would need to be set up differently on every device, including that it doesn't work the same way on Windows as on Mac.
APScheduler runs inside the Flask process which means that it starts when the app starts. The trade off is that it only works while the app is open, which is fine for how the app works now.
I still used cron triggers to express schedules naturally, such as 9am on a Monday, or the 1st of each month, instead of manually calculating time intervals.

For partial payments I decided to use amount_paid instead of creating a separate payments table that would log each payment, 
because amount_paid stores a value directly onto each bill. I considered using a separate payments table since it would keep a full history of every payment made and could
possibly help with features like a history chart, but currently Budget Buddy resets every month. So adding a log of past payments would add a lot of complexity
and not functionally change the core of the app at the moment. When I expand the app later adding a dedicated payments table that helps me display spending history is on my list.

I chose to compute each bill's status instead of storing it, because a stored value can quickly become innacurate. Using the get_status() function 
works out whether a bill is paid, overdue, due soon or still upcoming each time that the dashboard is loaded, so all the information that is displayed on the dashboard is
live and accurate data.


get_owned_income_or_404 / get_owned_payment_or_404 check ownership, this means that instead of looking for a bill
or income by id alone, i wrote helper functions that only return a record if it belongs to a logged in user, otherwise
it returns 404. This stops one user from viewing or editing another user's data.


I used calendar.monthrange and min(due_day, days_in_month) in days_until_due() to make sure that the due days_in_month is the last valid day in the month.
This ensures that a bill that is due on the 31st still works in February.


I decided to show reminders in the app, rather than send emails. This keeps the app self contained. 
The trade-off is that notifications only show when you open the app, but that is fine for now,
as the idea is to open budget buddy each morning when you arrive at work.


use_reloader=False is Flask's development server which usually runs a second reloader process, which would start
the APScheduler twice and send every reminder twice. This is turned off so the scheduler only runs once and each
remminder is sent only once.

