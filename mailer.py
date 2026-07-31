import os
from pathlib import Path

import resend
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

resend.api_key = os.environ.get("RESEND_API_KEY", "")
SENDER = os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")

_TITLES = {
    "verify": "Confirmă-ți contul Persona",
    "reset": "Resetare parolă Persona",
}
_INTROS = {
    "verify": "Bine ai venit! Folosește codul de mai jos ca să-ți confirmi contul:",
    "reset": "Ai cerut resetarea parolei. Folosește codul de mai jos:",
}


def enabled():
    return bool(resend.api_key)


def _html(code, purpose):
    intro = _INTROS.get(purpose, "Codul tău:")
    return f"""
    <div style="font-family:Arial,sans-serif;background:#0e0e11;padding:32px;border-radius:16px;color:#ececec">
      <div style="font-size:22px;font-weight:700;margin-bottom:8px">🎭 Persona</div>
      <p style="color:#b8b8c0;font-size:15px">{intro}</p>
      <div style="font-size:34px;font-weight:800;letter-spacing:10px;color:#FF7A59;
                  background:#17171c;border:1px solid #2a2a33;border-radius:12px;
                  padding:18px;text-align:center;margin:18px 0">{code}</div>
      <p style="color:#7b7b86;font-size:13px">Codul expiră în 15 minute. Dacă nu ai cerut tu acest email, ignoră-l.</p>
    </div>
    """


def send_code(to_email, code, purpose):
    if not resend.api_key:
        raise RuntimeError("Serviciul de email nu este configurat (lipsește RESEND_API_KEY).")
    return resend.Emails.send({
        "from": SENDER,
        "to": [to_email],
        "subject": _TITLES.get(purpose, "Cod Persona"),
        "html": _html(code, purpose),
    })
