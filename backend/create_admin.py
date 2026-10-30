"""Create the first SurvCam administrator interactively."""
import getpass
from database import init_db, get_user, add_user
from auth import hash_password

username = input("Administrator username: ").strip()
if not username:
    raise SystemExit("Username cannot be empty")
password = getpass.getpass("Administrator password: ")
if len(password) < 12:
    raise SystemExit("Use a password with at least 12 characters")

init_db()
if get_user(username):
    raise SystemExit("That username already exists")
add_user(username, hash_password(password), "admin")
print(f"Administrator {username!r} created")
