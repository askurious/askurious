"""Copy the app's database into a new, empty one (as Administration > Datenbank does, without a time limit).

    python tools/copy_database.py postgresql://user:password@host:5432/name

Run in the app's folder on the server, with the same .env (it reads the current database from DATABASE_URL,
or the SQLite file in instance/). The new database must be empty; missing tables are created. Everything is
copied in one transaction, so it holds all rows or none. Afterwards set DATABASE_URL in .env to the new
address and restart the app. The old database stays as it was. Details: app/dbmove.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    from app import create_app, dbmove

    app = create_app()
    with app.app_context():
        def report(share, text):
            print(f"\r{share * 100:5.1f} %  {text[:70]:<70}", end="", flush=True)

        try:
            done = dbmove.copy(argv[1], report)
        except dbmove.MoveError as e:
            print(f"\n{e}")
            return 1
    print(f"\nKopiert: {done['rows']} Einträge aus {done['tables']} Tabellen in {done['seconds']} s.")
    print("Jetzt DATABASE_URL in der .env auf die neue Adresse setzen und die App neu starten.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
