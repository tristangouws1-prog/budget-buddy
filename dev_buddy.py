"""Dev helper - look at or tweak the local test account's buddy.

Handy when testing the shop, levels or a particular sprite without having
to pay hundreds of pretend bills first.

    python dev_buddy.py                        # just show the current state
    python dev_buddy.py --coins 5000           # money for the shop
    python dev_buddy.py --level 5              # jump to level 5
    python dev_buddy.py --xp 800               # or set XP exactly
    python dev_buddy.py --species purplefrog   # preview another sprite
    python dev_buddy.py --stage egg            # go back to being an egg
    python dev_buddy.py --unlock-all           # own every cosmetic

Everything can be combined:

    python dev_buddy.py --level 4 --coins 9999 --species blackcat

This edits whichever database DATABASE_URL points at - locally that is
instance/budget.db. It prints the database it is about to touch, so check
that line before running it anywhere unusual.
"""
import argparse
import os

from app import (app, db, User, Buddy, OwnedCosmetic,
                 BUDDY_SPECIES, BUDDY_SHOP, MAX_BUDDIES,
                 buddy_level, xp_for_level)


def describe(label, buddy):
    """One line summary of a buddy's current state."""
    level = buddy_level(buddy.xp)
    nxt = xp_for_level(level + 1)
    print(f"  {label:<7} {buddy.name!r} the {buddy.species} [{buddy.stage}] - "
          f"Lv {level} ({buddy.xp} XP, {nxt - buddy.xp} to Lv {level + 1}), "
          f"{buddy.coins} coins")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect or tweak the local test account's buddy.")
    parser.add_argument("--xp", type=int, help="set lifetime XP exactly")
    parser.add_argument("--level", type=int,
                        help="set XP to the minimum for this level")
    parser.add_argument("--coins", type=int, help="set spendable coins")
    parser.add_argument("--species", choices=BUDDY_SPECIES, help="swap the sprite")
    parser.add_argument("--stage", choices=["egg", "hatched"])
    parser.add_argument("--name", help="rename the buddy")
    parser.add_argument("--unlock-all", action="store_true",
                        help="own (but do not wear) every shop item")
    parser.add_argument("--email",
                        default=os.environ.get("DEV_ADMIN_EMAIL",
                                               "budgetbuddysite@gmail.com"),
                        help="which account to touch")
    parser.add_argument("--levels", action="store_true",
                        help="print the XP needed for each level and exit")
    args = parser.parse_args()

    if args.levels:
        print("XP needed for each level:")
        for lvl in range(1, 11):
            print(f"  Lv {lvl:<3} {xp_for_level(lvl):>5} XP")
        return

    with app.app_context():
        print(f"database: {app.config['SQLALCHEMY_DATABASE_URI']}")

        user = User.query.filter_by(email=args.email).first()
        if user is None:
            print(f"No account found for {args.email}.")
            print("Set DEV_ADMIN_PASSWORD in .env and run 'python app.py' once.")
            return

        #the buddy that is currently out front, or any of them as a fallback
        buddy = (Buddy.query.filter_by(user_id=user.id, is_active=True).first()
                 or Buddy.query.filter_by(user_id=user.id).first())
        if buddy is None:
            print("That account has no buddy yet - open the app once and it "
                  "will be created.")
            return

        others = Buddy.query.filter_by(user_id=user.id).count() - 1
        print(f"account:  {user.username} <{user.email}>"
              + (f"  (+{others} more in the house, max {MAX_BUDDIES})"
                 if others > 0 else ""))
        describe("before", buddy)

        changed = False

        #--level first so an explicit --xp can still override it
        if args.level is not None:
            buddy.xp = xp_for_level(max(1, args.level))
            changed = True
        if args.xp is not None:
            buddy.xp = max(0, args.xp)
            changed = True
        if args.coins is not None:
            buddy.coins = max(0, args.coins)
            changed = True
        if args.species:
            buddy.species = args.species
            changed = True
        if args.stage:
            buddy.stage = args.stage
            changed = True
        if args.name:
            buddy.name = args.name[:50]
            changed = True

        if args.unlock_all:
            owned = {c.item_key for c in
                     OwnedCosmetic.query.filter_by(user_id=user.id).all()}
            added = 0
            for key in BUDDY_SHOP:
                if key not in owned:
                    db.session.add(OwnedCosmetic(user_id=user.id, item_key=key,
                                                 equipped=False))
                    added += 1
            print(f"  unlock  {added} new item(s), {len(BUDDY_SHOP)} owned in total")
            changed = True

        if not changed:
            print("\n(no changes asked for - run with --help to see the options)")
            return

        db.session.commit()
        describe("after", buddy)
        print("\nRefresh the page in your browser to see it.")
        if args.xp is not None or args.level is not None:
            print("Note: setting XP by hand does not hand out the milestone "
                  "eggs that levelling up normally would.")


if __name__ == "__main__":
    main()
