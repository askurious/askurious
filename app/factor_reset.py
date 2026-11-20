"""Reset the second factor of an account on the server, for a site admin who lost the phone and the backup codes
(app/twofactor.py): since site admins give the code after an ORCID sign-in too, the ORCID sign-in alone no longer opens
the administration to set the factor up anew.

    python -m app.factor_reset <ORCID iD>          (in the researchers' container)

The stored secret, the last step and the backup codes go; the account's next visit of the administration leads to the
setup. Written to the security log ("second_factor_reset", by the operator); nothing else of the account changes.
Only whoever has the server can run this, as with every other key of the installation."""
import sys


def reset(orcid):
    """True if the account with this ORCID iD had a factor that is gone now; False if it had none; None if there is
    no such account. The caller commits."""
    from app import passwords, security_log
    from app.extensions import db
    from app.models import User

    user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
    if user is None:
        return None
    row = passwords.login_row(user)
    if row is None or not row.totp_box:
        return False
    row.totp_box, row.totp_step, row.totp_backup = None, None, None
    security_log.event("second_factor_reset", account=user.id, via="operator")
    return True


def main(argv=None):
    from app import create_app
    from app.extensions import db

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m app.factor_reset <ORCID iD>")
        return 2
    app = create_app()
    with app.app_context():
        done = reset(args[0].strip())
        db.session.commit()
    if done is None:
        print("no account with this ORCID iD")
        return 1
    print("second factor reset: the account sets up a new one at its next visit of the administration" if done
          else "the account has no second factor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
