# Budget Buddy

A calm, ADHD-friendly budgeting app built with Flask and Python.

It lets you:
- Add your regular monthly bills and subscriptions (name, amount, due day).
- See at a glance what's **paid**, **coming up**, and **overdue** (colour-coded).
- Mark bills as paid (or undo it).
- Get **weekly** reminders asking if you've added any new bills/subscriptions.
- Get **monthly** reminders of all your payments (and the app resets paid status for the new month).

---

## How to run it (step by step)

You'll need **Python 3** installed. Then open a terminal **in this folder** and run the commands below.

### 1. (Recommended) Create a virtual environment
A "virtual environment" is a private space for this project's packages, so they don't clash with anything else on your computer.

**On Mac / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**On Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

### 2. Install the required packages
```bash
pip install -r requirements.txt
```

### 3. Start the app
```bash
python app.py
```

### 4. Open it in your browser
Go to: **http://127.0.0.1:5000**

To stop the app, press **Ctrl + C** in the terminal.

---

## Project structure

```
adhd_budget/
├── app.py               <- the main program (database, pages, reminders)
├── requirements.txt     <- list of packages to install
├── templates/           <- the HTML pages
│   ├── base.html        <- shared layout (header/footer/fonts)
│   ├── dashboard.html   <- the home page
│   ├── add_payment.html <- the "add a bill" form
│   └── reminders.html   <- the reminders list
└── static/
    └── style.css        <- all the styling (colours, layout, fonts)
```

A file called `budget.db` will be created automatically the first time you run the app — that's your database, where bills and reminders are stored.

---

## How the reminders work (and how to test them quickly)

Reminders are created automatically by a background scheduler:
- **Weekly:** every Monday at 9:00 AM.
- **Monthly:** on the 1st of each month at 9:00 AM.

Because those are real schedules, you might wait days to see one. To test instantly, open `app.py`, find the **"TIP FOR TESTING"** comment in section 7, and temporarily swap a job to run on an interval, e.g. every 15 seconds. Restart the app, wait, then refresh the page — a reminder will appear. Change it back when you're done.

---

## Want to take it further? Ideas to try
- Add categories (e.g. "Essentials" vs "Fun") and show totals per category.
- Add a "paid history" so you can see which months you paid on time.
- Send reminders by email instead of just showing them in the app.
- Add a yearly total so you can see what your subscriptions cost per year.
