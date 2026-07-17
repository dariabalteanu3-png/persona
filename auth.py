import random
import re
import secrets
import unicodedata

import bcrypt

import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, hashed):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:  # noqa
        return False


def _public(user):
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name") or user["email"],
        "avatar_image": user.get("avatar_image"),
    }


def public_by_email(email):
    u = db.get_user_by_email((email or "").strip().lower())
    return _public(u) if u else None


def register(username, password, name="", question=None, answer=None):
    username = (username or "").strip().lower()
    if len(username) < 3:
        raise ValueError("Numele de utilizator trebuie să aibă minim 3 caractere.")
    if not password or len(password) < 6:
        raise ValueError("Parola trebuie să aibă minim 6 caractere.")
    if db.get_user_by_email(username):
        raise ValueError("Există deja un cont cu acest nume de utilizator.")
    q = (question or "").strip() or None
    a_hash = hash_password(_norm_answer(answer)) if (q and answer and answer.strip()) else None
    db.create_user(
        username, hash_password(password), (name or "").strip() or username,
        verified=True, security_question=q, security_answer_hash=a_hash,
    )
    return username


def _norm_answer(a):
    a = (a or "").strip().lower()
    a = unicodedata.normalize("NFKD", a)
    return "".join(c for c in a if not unicodedata.combining(c))


def get_security_question(username):
    u = db.get_user_by_email((username or "").strip().lower())
    return u.get("security_question") if u else None


def verify_security_answer(username, answer):
    u = db.get_user_by_email((username or "").strip().lower())
    if not u or not u.get("security_answer_hash"):
        return False
    return verify_password(_norm_answer(answer), u["security_answer_hash"])


def authenticate(email, password):
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user or not verify_password(password or "", user.get("password_hash", "")):
        return None
    return user


def is_verified(email):
    u = db.get_user_by_email((email or "").strip().lower())
    return bool(u and u.get("verified"))


def gen_code(email, purpose):
    email = (email or "").strip().lower()
    code = f"{random.randint(0, 999999):06d}"
    db.create_email_code(email, code, purpose)
    return code


def check_code(email, code, purpose):
    return db.check_email_code((email or "").strip().lower(), code, purpose)


def set_verified(email):
    db.set_user_verified((email or "").strip().lower())


def reset_password(email, new_password):
    email = (email or "").strip().lower()
    if not new_password or len(new_password) < 6:
        raise ValueError("Parola trebuie să aibă minim 6 caractere.")
    db.set_user_password(email, hash_password(new_password))


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    db.create_session(token, user_id, expires_days=30)
    return token


def user_from_token(token):
    if not token:
        return None
    s = db.get_session(token)
    if not s:
        return None
    user = db.get_user_by_id(s["user_id"])
    return _public(user) if user else None


def destroy_session(token):
    if token:
        db.delete_session(token)
