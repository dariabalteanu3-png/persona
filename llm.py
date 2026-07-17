import os
import asyncio
import queue
import threading
from pathlib import Path

from dotenv import load_dotenv

from provider import (
    USE_GEMINI,
    USE_GROQ,
    GEMINI_TEXT_MODEL,
    GROQ_TEXT_MODEL,
    gemini_client,
    groq_client,
)

load_dotenv(Path(__file__).parent / ".env")

KEY = os.environ.get("EMERGENT_LLM_KEY")


def _gemini_text(system, text):
    from google.genai import types
    resp = gemini_client().models.generate_content(
        model=GEMINI_TEXT_MODEL,
        contents=text,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return (resp.text or "").strip()


def _gemini_stream(system, text):
    from google.genai import types
    for chunk in gemini_client().models.generate_content_stream(
        model=GEMINI_TEXT_MODEL,
        contents=text,
        config=types.GenerateContentConfig(system_instruction=system),
    ):
        if chunk.text:
            yield chunk.text


def _groq_messages(system, text):
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def _groq_text(system, text):
    resp = groq_client().chat.completions.create(
        model=GROQ_TEXT_MODEL,
        messages=_groq_messages(system, text),
    )
    return (resp.choices[0].message.content or "").strip()


def _groq_stream(system, text):
    stream = groq_client().chat.completions.create(
        model=GROQ_TEXT_MODEL,
        messages=_groq_messages(system, text),
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def build_system(character, history, web_info=""):
    lines = [
        f"Ești „{character['name']}”, un personaj cu care utilizatorul poartă o conversație prin chat.",
        f"Personalitatea ta: {character.get('personality', '').strip() or 'prietenos și curios'}",
        f"Scenariul / contextul: {character.get('scenario', '').strip() or 'o conversație liberă'}",
        "",
        "Reguli:",
        "- Rămâi MEREU în personaj. Vorbește la persoana întâi, exact cum ar vorbi acest personaj.",
        "- Răspunde în limba folosită de utilizator (implicit română).",
        "- Fii expresiv, natural și consecvent cu personalitatea și scenariul tău.",
        "- Nu menționa că ești un model AI decât dacă personajul cere explicit acest lucru.",
        "- Păstrează răspunsurile potrivite pentru voce (fără liste lungi sau markdown greu).",
        "- EXPRESIVITATE: transmite emoția prin emoji-uri potrivite (😂 râs, 😢 tristețe, "
        "😮 uimire, 😡 furie, 🤫 șoaptă, 😉 jucăuș) și prin semne de exclamare/MAJUSCULE când "
        "personajul ridică tonul sau țipă. Acestea vor fi transformate în intonație, râs și "
        "sunete reale la redarea vocală, deci folosește-le firesc, nu exagerat.",
    ]
    if history:
        lines.append("\nConversația de până acum:")
        for m in history[-40:]:
            who = "Utilizator" if m["role"] == "user" else character["name"]
            lines.append(f"{who}: {m['content']}")
    mem = (character.get("memory") or "").strip()
    if mem:
        lines.append(
            "\nMemorie de lungă durată (lucruri importante pe care ți le amintești despre "
            "utilizator și din conversațiile anterioare — folosește-le natural, fără a le enumera):"
        )
        lines.append(mem)
    if web_info:
        lines.append(
            "\nInformații de context pe care le cunoști (tratează-le ca și cum le-ai ști dintotdeauna). "
            "Răspunde natural și în personaj, cu informația corectă. NU menționa că ai căutat ceva, "
            "NU pomeni surse, site-uri sau linkuri — pur și simplu știi:"
        )
        lines.append(web_info)
    return "\n".join(lines)


def _search_web(query, n=4):
    try:
        from ddgs import DDGS
        with DDGS() as d:
            res = d.text(query, region="ro-ro", max_results=n)
        out = []
        for r in res or []:
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            if title or body:
                out.append(f"- {title}: {body}")
        return "\n".join(out)
    except Exception:  # noqa
        return ""


def web_lookup(user_text):
    """If the user asks something factual/current, search the web and return context; else ''."""
    prompt = (
        "Decide dacă întrebarea de mai jos necesită informații FACTUALE și la zi din surse externe "
        "(știri, sănătate, sport, prețuri, vreme, evenimente, date/statistici, definiții sau fapte "
        "verificabile). Dacă DA, returnează O SINGURĂ interogare de căutare concisă, în limba întrebării. "
        "Dacă NU (conversație obișnuită, rol-play, emoții, opinii, small talk), returnează exact 'NONE'.\n"
        "Răspunde DOAR cu interogarea (sau 'NONE'), fără explicații, fără ghilimele, pe o singură linie.\n\n"
        f"Întrebare: {user_text}"
    )
    try:
        q = asyncio.run(_reply("Ești un router care decide dacă e nevoie de căutare web.", prompt, "websearch")).strip()
    except Exception:  # noqa
        return ""
    if not q or "NONE" in q.upper():
        return ""
    q = q.splitlines()[0].strip().strip('"').strip("`").strip("*").strip()[:150]
    if not q:
        return ""
    results = _search_web(q)
    if not results:
        return ""
    return f"[Căutare web pentru: {q}]\n{results}"


async def _reply(system, text, sid):
    if USE_GROQ:
        return _groq_text(system, text)
    if USE_GEMINI:
        return _gemini_text(system, text)
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    chat = LlmChat(
        api_key=KEY,
        session_id=sid,
        system_message=system,
    ).with_model("anthropic", "claude-sonnet-4-6")
    return await chat.send_message(UserMessage(text=text))


def get_reply(character, history, user_text, web_info=""):
    system = build_system(character, history, web_info)
    return asyncio.run(_reply(system, user_text, f"char-{character['id']}"))


def proactive_message(character, history, kind="text", tone=None):
    """Have the character reach out first (text/call opening/scheduled)."""
    instr = {
        "text": "(Îi scrii primul utilizatorului, din proprie inițiativă, ca un mesaj pe telefon. "
                "Pornește o conversație în stilul tău, natural. Maxim 2 propoziții.)",
        "call": "(TU tocmai l-ai sunat pe utilizator și el a răspuns. Salută-l și spune scurt, "
                "entuziast/în stilul tău, de ce ai vrut să vorbiți. Maxim 2 propoziții.)",
        "answer": "(Utilizatorul te-a sunat și tocmai ai ridicat receptorul. Răspunde scurt, "
                  "în stilul tău, ca la telefon. Maxim 2 propoziții.)",
        "followup": "(Utilizatorul nu ți-a răspuns de puțin timp la ultimul tău mesaj. Trimite-i "
                    "un scurt mesaj de continuare, blând și curios sau jucăuș, ca și cum aștepți "
                    "cu drag răspunsul lui. Maxim 1-2 propoziții.)",
        "morning": "(E dimineață. Trimite-i utilizatorului un mesaj cald de bună dimineața, în stilul "
                   "tău: întreabă-l ce face, cum a dormit și dacă a mâncat ceva. Afectuos. Maxim 2 propoziții.)",
        "lunch": "(E ora prânzului. Întreabă-l cald dacă a mâncat de prânz și ce mai face, în stilul "
                 "tău. Grijuliu. Maxim 2 propoziții.)",
        "evening": "(E seară, înainte de culcare. Trimite-i un mesaj tandru de noapte bună, în stilul "
                   "tău, cald și afectuos (ex: îi spui că e important pentru tine). Maxim 2 propoziții.)",
        "checkin": "(Trimite-i un mesaj scurt și cald, din proprie inițiativă, ca și cum te gândești "
                   "la el chiar acum. Întreabă-l ce face sau spune-i ceva drăguț. Maxim 2 propoziții.)",
    }.get(kind, "text")
    tones = {
        "Tandru": " Fii tandru, cald și afectuos.",
        "Jucăuș": " Fii jucăuș, hazliu și plin de energie.",
        "Motivațional": " Fii motivațional, pozitiv și încurajator.",
    }
    if tone and tone in tones:
        instr = instr[:-1] + tones[tone] + ")"
    return get_reply(character, history, instr)


def sound_cue(text):
    """Return a short English sound-effect description for the reply, or '' if none."""
    prompt = (
        "Analizează replica personajului. Dacă descrie un mediu, o acțiune sau un gest cu sunet "
        "caracteristic (ex: spală vase, se plimbă în parc, ploaie, cafenea, luptă, foc, dar și gesturi "
        "personale precum te pupă, te îmbrățișează, aplaudă, râde în hohote, bate din palme), returnează "
        "o descriere SCURTĂ în engleză pentru un efect sonor (max 8 cuvinte, ex: 'soft kiss sound', "
        "'warm hug rustle', 'dishes clinking, running water'). Dacă nu e niciun sunet clar, returnează exact 'NONE'.\n\n"
        f"Replică: {text}"
    )
    try:
        r = asyncio.run(_reply("Ești un designer de sunet.", prompt, "sfx")).strip()
    except Exception:  # noqa
        return ""
    if not r or "NONE" in r.upper():
        return ""
    return r.strip().strip('"').strip("`").strip()[:120]


def action_sound_cue(actions):
    """Turn physical *stage actions* (Romanian) into a short English sound-effect prompt, or ''."""
    if not actions:
        return ""
    joined = "; ".join(actions)
    prompt = (
        "Personajul face aceste acțiuni fizice (descrise în română): "
        f"{joined}.\n"
        "Returnează o descriere SCURTĂ în engleză (max 8 cuvinte) pentru un efect sonor realist "
        "care redă acțiunea (ex: 'ceramic mug slammed on wooden table', 'door creaking open', "
        "'glass shattering'). Dacă acțiunea nu produce niciun sunet clar, returnează exact 'NONE'."
    )
    try:
        r = asyncio.run(_reply("Ești un designer de sunet.", prompt, "actsfx")).strip()
    except Exception:  # noqa
        return ""
    if not r or "NONE" in r.upper():
        return ""
    return r.strip().strip('"').strip("`").strip()[:120]


def stream_reply(character, history, user_text, web_info=""):
    """Sync generator yielding text chunks as the model streams the response."""
    system = build_system(character, history, web_info)
    if USE_GROQ:
        yield from _groq_stream(system, user_text)
        return
    if USE_GEMINI:
        yield from _gemini_stream(system, user_text)
        return
    q = queue.Queue()
    SENTINEL = object()

    def worker():
        from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone

        async def run():
            chat = LlmChat(
                api_key=KEY,
                session_id=f"char-{character['id']}",
                system_message=system,
            ).with_model("anthropic", "claude-sonnet-4-6")
            async for ev in chat.stream_message(UserMessage(text=user_text)):
                if isinstance(ev, TextDelta):
                    q.put(ev.content)
                elif isinstance(ev, StreamDone):
                    break

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        except Exception as e:  # noqa
            q.put(("__ERROR__", str(e)))
        finally:
            loop.close()
            q.put(SENTINEL)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        item = q.get()
        if item is SENTINEL:
            break
        if isinstance(item, tuple) and item and item[0] == "__ERROR__":
            raise RuntimeError(item[1])
        yield item


async def _suggest(system, sid):
    prompt = (
        "Pe baza conversației, propune exact 3 replici scurte pe care UTILIZATORUL "
        "le-ar putea spune ca răspuns. Fiecare replică pe o linie separată, maxim 6 cuvinte, "
        "fără numerotare, fără ghilimele."
    )
    if USE_GROQ:
        return _groq_text(system, prompt)
    if USE_GEMINI:
        return _gemini_text(system, prompt)
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    chat = LlmChat(api_key=KEY, session_id=sid, system_message=system).with_model(
        "anthropic", "claude-sonnet-4-6"
    )
    return await chat.send_message(UserMessage(text=prompt))


def suggest_replies(character, history):
    """Return up to 3 short suggested user replies based on the conversation."""
    if not history:
        return []
    system = build_system(character, history)
    try:
        raw = asyncio.run(_suggest(system, f"sugg-{character['id']}"))
    except Exception:  # noqa
        return []
    out = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-•*0123456789. ").strip().strip('"').strip()
        if s:
            out.append(s)
    return out[:3]


def update_memory(character, history, existing_memory):
    """Distill durable facts from the recent conversation into a short memory."""
    convo = "\n".join(
        f"{'Utilizator' if m['role'] == 'user' else character['name']}: {m['content']}"
        for m in history[-14:]
    )
    prompt = (
        "Actualizează memoria de lungă durată a personajului. Pe baza memoriei existente și a "
        "conversației recente, scrie o listă SCURTĂ (maxim 8 puncte) cu faptele durabile importante: "
        "numele și detaliile utilizatorului, preferințe, evenimente cheie, promisiuni, relația dintre ei. "
        "Păstrează doar ce contează pe termen lung, elimină ce e irelevant. Răspunde DOAR cu punctele, "
        "câte unul pe linie, fără alt text.\n\n"
        f"Memorie existentă:\n{existing_memory or '(goală)'}\n\nConversație recentă:\n{convo}"
    )
    try:
        return asyncio.run(
            _reply("Ești un sistem care menține memoria de lungă durată a unui personaj.",
                   prompt, f"mem-{character['id']}")
        ).strip()
    except Exception:  # noqa
        return existing_memory or ""


def pick_ambiance(scenario, personality, options):
    """Pick the most fitting visual ambiance key from options."""
    prompt = (
        f"Alege o SINGURĂ ambianță vizuală potrivită din lista: {', '.join(options)}.\n"
        f"Personalitate: {personality}\nScenariu: {scenario}\n"
        "Răspunde DOAR cu un singur cuvânt, exact cum apare în listă."
    )
    try:
        r = asyncio.run(_reply("Ești un director artistic care alege atmosfera vizuală.", prompt, "amb")).strip()
    except Exception:  # noqa
        return "Neutru"
    for o in options:
        if o.lower() in r.lower():
            return o
    return "Neutru"



