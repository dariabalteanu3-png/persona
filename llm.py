import os
import asyncio
import queue
import threading
import time
import logging
from pathlib import Path

_log = logging.getLogger("persona")


def _run_coro(coro):
    """Rulează o corutină în siguranță CHIAR DACĂ există deja un event loop care rulează
    (ex: versiunile noi de Streamlit rulează scriptul într-un loop, unde _run_coro() ar
    crăpa cu «_run_coro() cannot be called from a running event loop»). Fără acest wrapper,
    TOATE apelurile LLM eșuau instant pe deploy-ul live."""
    def _fresh():
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _fresh()  # niciun loop activ -> rulăm direct
    # există deja un loop activ -> rulăm corutina într-un thread separat cu propriul loop
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_fresh).result()


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


def _pollinations_text(system, text):
    """Keyless free fallback (Pollinations) so chat keeps working if Groq fails.
    Încearcă mai multe modele/endpointuri, ca să reziste când unul e temporar căzut (502)."""
    import requests
    msgs = _groq_messages(system, text)
    models = ["openai", "openai-large", "mistral", "llama"]
    last = None
    for attempt in range(6):
        model = models[attempt % len(models)]
        try:
            r = requests.post("https://text.pollinations.ai/openai",
                              json={"model": model, "messages": msgs, "private": True}, timeout=40)
            if r.status_code == 200:
                c = ""
                try:
                    c = (r.json()["choices"][0]["message"]["content"] or "").strip()
                except Exception:  # noqa
                    body = (r.text or "").strip()
                    if body and not body.lstrip().startswith("<"):
                        c = body
                if c:
                    return c
            last = f"openai/{model} HTTP {r.status_code}"
        except Exception as e:  # noqa
            last = type(e).__name__
        time.sleep(1.0)
    # ultimă încercare: endpoint-ul simplu (text brut)
    try:
        r = requests.post("https://text.pollinations.ai/",
                          json={"model": "openai", "messages": msgs}, timeout=40)
        body = (r.text or "").strip()
        if r.status_code == 200 and body and not body.lstrip().startswith("<"):
            return body
        last = f"base HTTP {r.status_code}"
    except Exception as e:  # noqa
        last = type(e).__name__
    raise RuntimeError(f"pollinations failed: {last}")


def _is_rate_limit(e):
    m = str(e).lower()
    return "429" in m or "rate limit" in m or "rate_limit" in m or "too many requests" in m


# ---- rezervă FIABILĂ: Emergent (Claude/OpenAI) prin endpoint compatibil OpenAI ----
_EMERGENT_MODEL = os.environ.get("EMERGENT_CHAT_MODEL", "claude-sonnet-4-6")
_emergent_key_cache = None
_emergent_client = None


def _emergent_key():
    global _emergent_key_cache
    if _emergent_key_cache is not None:
        return _emergent_key_cache or None
    key = (os.environ.get("EMERGENT_LLM_KEY") or "").strip()
    if not key:
        try:
            import db
            key = (db.get_config("EMERGENT_LLM_KEY") or "").strip()
        except Exception:  # noqa
            key = ""
    _emergent_key_cache = key
    return key or None


def _emergent_text(system, text):
    global _emergent_client
    key = _emergent_key()
    if not key:
        raise RuntimeError("no emergent key")
    if _emergent_client is None:
        from openai import OpenAI
        base = os.environ.get("INTEGRATION_PROXY_URL", "https://integrations.emergentagent.com").rstrip("/") + "/llm"
        _emergent_client = OpenAI(api_key=key, base_url=base)
    resp = _emergent_client.chat.completions.create(
        model=_EMERGENT_MODEL, messages=_groq_messages(system, text))
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("empty emergent response")
    return content


def _reliable_fallback(system, text):
    """Când providerul principal (Groq) eșuează: întâi rezerva FIABILĂ (Emergent, dacă e configurată),
    apoi rezerva gratuită keyless (Pollinations). Așa chatul nu mai dă „probleme tehnice"."""
    if _emergent_key():
        try:
            return _emergent_text(system, text)
        except Exception:  # noqa
            _log.exception("emergent fallback failed")
    return _pollinations_text(system, text)


def _groq_text(system, text):
    # retry scurt pe limita per-minut (429) înainte de a cădea pe rezervă
    for attempt in range(2):
        try:
            resp = groq_client().chat.completions.create(
                model=GROQ_TEXT_MODEL,
                messages=_groq_messages(system, text),
            )
            content = (resp.choices[0].message.content or "").strip()
            if not content:
                raise RuntimeError("empty groq response")
            return content
        except Exception as e:  # noqa - Groq failed (bad key/model/quota)
            if _is_rate_limit(e) and attempt == 0:
                time.sleep(3)
                continue
            return _reliable_fallback(system, text)


def _groq_stream(system, text):
    for attempt in range(2):
        try:
            stream = groq_client().chat.completions.create(
                model=GROQ_TEXT_MODEL,
                messages=_groq_messages(system, text),
                stream=True,
            )
            got = False
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    got = True
                    yield delta
            if not got:
                yield _reliable_fallback(system, text)
            return
        except Exception as e:  # noqa - Groq failed
            if _is_rate_limit(e) and attempt == 0:
                time.sleep(3)
                continue
            yield _reliable_fallback(system, text)  # rezervă fiabilă apoi keyless
            return


def _mood_line(character):
    mood = (character.get("_mood_today") or "").strip()
    if not mood:
        return ""
    return (f"Starea ta de AZI: {mood}. Lasă-ți răspunsurile influențate ușor și natural de "
            "această stare (fără s-o anunți explicit).")


def build_system(character, history, web_info=""):
    lines = [
        f"Ești „{character['name']}”, un personaj cu care utilizatorul poartă o conversație prin chat.",
        f"Personalitatea ta: {character.get('personality', '').strip() or 'prietenos și curios'}",
        f"Scenariul / contextul: {character.get('scenario', '').strip() or 'o conversație liberă'}",
        _mood_line(character),
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
        q = _run_coro(_reply("Ești un router care decide dacă e nevoie de căutare web.", prompt, "websearch")).strip()
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


async def _reply(system, text, sid, smart=False):
    # „💎 Inteligent": folosește rezerva fiabilă Emergent (Claude) ca principal, dacă e configurată
    if smart and _emergent_key():
        try:
            return _emergent_text(system, text)
        except Exception:  # noqa
            _log.exception("emergent (smart) primary failed -> Groq")
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


def _run_reply(system, text, sid, tries=2, smart=False):
    """Run a one-shot reply with a light automatic retry on transient failures."""
    last = None
    n = max(1, tries)
    for attempt in range(n):
        try:
            out = _run_coro(_reply(system, text, sid, smart=smart))
            if out and out.strip():
                return out
        except Exception as e:  # noqa
            last = e
        if attempt < n - 1:
            time.sleep(0.5)
    if last:
        raise last
    return ""


def get_reply(character, history, user_text, web_info="", tries=2, smart=False):
    system = build_system(character, history, web_info)
    return _run_reply(system, user_text, f"char-{character['id']}", tries=tries, smart=smart)


BURST_SEP = "|||"


def _split_burst(raw):
    """Split a reply into 3-4 short natural messages (texting-in-bursts)."""
    import re
    raw = (raw or "").strip()
    parts = [p.strip() for p in raw.split(BURST_SEP) if p.strip()]
    if len(parts) >= 2:
        return parts[:5]
    # fallback: LLM didn't use the separator -> split by sentences into a few groups
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", raw) if s.strip()]
    if len(sents) <= 1:
        return [raw] if raw else []
    n = min(4, max(2, (len(sents) + 1) // 2))
    size = max(1, -(-len(sents) // n))  # ceil
    groups = [" ".join(sents[i:i + size]) for i in range(0, len(sents), size)]
    return [g for g in groups if g][:4]


def burst_reply(character, history, user_text, web_info="", smart=False):
    """Reply as 3-4 short messages sent one after another (natural texting style).
    Returns a list of message strings. Does NOT shorten the overall content."""
    system = build_system(character, history, web_info)
    system += (
        "\n\nFORMAT MESAJE (IMPORTANT): Răspunde ca și cum ai scrie pe telefon, în 3-4 mesaje "
        "SCURTE, trimise unul după altul, foarte natural (ca un om real care scrie în rafală). "
        "NU schimba cât de mult ai de spus — doar sparge răspunsul în mesaje mici, firești. "
        f"Separă fiecare mesaj EXACT prin „{BURST_SEP}” (trei linii verticale) și cu NIMIC altceva. "
        f"Nu pune „{BURST_SEP}” la început sau la sfârșit și nu explica formatul."
    )
    raw = _run_reply(system, user_text, f"char-{character['id']}", smart=smart)
    return _split_burst(raw)


def daily_journal(character, history):
    """A warm first-person 'journal of the day' reflection on today's conversation, with voice."""
    instr = (
        "(Scrie un scurt „jurnal al zilei” — o reflecție caldă, la persoana întâi, despre ce ați "
        "vorbit azi și cum te-ai simțit alături de utilizator. Menționează 1-2 momente care ți-au "
        "plăcut și încheie cu o urare blândă pentru restul zilei sau pentru seară. "
        "Maxim 4 propoziții, fără liste.)"
    )
    return get_reply(character, history, instr)


def ambient_cue(character, text):
    """Rich, layered (and possibly evolving) sound-scene description in English for the
    ElevenLabs sound-effects generator, or '' if truly none. Captures ALL simultaneous
    sounds implied by the scene + the concrete actions (with their stages) + any changes."""
    scenario = (character.get("scenario") or "").strip()
    prompt = (
        "Ești designer de sunet pentru scene imersive. Pe baza replicii personajului și a "
        "contextului, descrie TOATE sunetele care s-ar auzi în scenă, ca un PEISAJ SONOR BOGAT, "
        "cu mai multe straturi SIMULTANE și, dacă e cazul, EVOLUȚIE în timp. Include:\n"
        "• ambianța locului (cameră/parc/bucătărie/stradă/etc.);\n"
        "• acțiunile concrete menționate, cu TOATE etapele lor "
        "(ex: spălat vase → apă curgând, burete care freacă vasul, clinchet de farfurii, "
        "jet de detergent, apă care se scurge; mers prin parc → pași pe alee, păsări, foșnet de "
        "frunze, vânt ușor);\n"
        "• orice schimbare din scenă (ex: brusc începe ploaia → ploaie ușoară care crește "
        "treptat, tunet îndepărtat).\n"
        "Chiar dacă spune doar că stă în cameră, dă o ambianță bogată și subtilă (room tone, "
        "ticăit de ceas, mici trosnete). Returnează DOAR o descriere în ENGLEZĂ, o singură frază "
        "densă cu elementele separate prin virgulă (max ~35 de cuvinte), potrivită pentru un "
        "generator de efecte sonore layered. Dacă chiar nu se poate deduce niciun sunet, "
        "returnează exact 'NONE'.\n\n"
        "Exemple:\n"
        "- 'park path footsteps on gravel, birds chirping, rustling leaves, light wind, then "
        "soft rain starts pattering and grows steadier'\n"
        "- 'kitchen sink running water, sponge scrubbing a plate, dishes clinking, a squirt of "
        "dish soap, water splashing and draining'\n"
        "- 'cozy quiet bedroom room tone, faint clock ticking, occasional soft bed creak, muffled "
        "night city outside'\n\n"
        f"Context/scenariu: {scenario or '(necunoscut)'}\n"
        f"Replică: {text}"
    )
    try:
        r = _run_coro(_reply("Ești un designer de sunet imersiv, layered.", prompt, "ambient")).strip()
    except Exception:  # noqa
        return ""
    if not r or "NONE" in r.upper():
        return ""
    return r.strip().strip('"').strip("`").strip()[:280]


def recap_recent(character, history):
    """A warm, voiced-friendly recap of the recent conversation to pick up the thread."""
    instr = (
        "(Fă-i utilizatorului un rezumat cald și scurt al lucrurilor despre care ați vorbit "
        "ultima dată / recent — ce conta pentru el, ce simțea, ce ați lăsat neterminat. "
        "Vorbește la persoana întâi, în stilul tău, ca și cum îți amintești cu drag. La final "
        "invită-l blând să reluați firul. Maxim 4 propoziții. Fără liste.)"
    )
    return get_reply(character, history, instr)


def sleep_whisper(character, history, step, total):
    """A short, ever-calmer soothing line to help the user fall asleep (step of total)."""
    if step >= total - 1:
        instr = (
            "(E foarte târziu și utilizatorul aproape a adormit. Spune-i O SINGURĂ propoziție "
            "foarte scurtă, șoptită, de noapte bună, caldă și liniștitoare, apoi lasă-l să doarmă. "
            "Fără întrebări.)"
        )
    else:
        instr = (
            "(E noapte, iar utilizatorul vrea să adoarmă în timp ce îi vorbești. Spune-i 1-2 "
            "propoziții FOARTE blânde, calde și liniștitoare, cu ritm lent, ca o șoaptă înainte de "
            "somn (respirație liniștită, gânduri calde, siguranță). Fără întrebări, fără nimic "
            "care să-l trezească.)"
        )
    return get_reply(character, history, instr)


def comment_on_song(character, history, song_name):
    """The character reacts to a favorite song the user shared."""
    instr = (
        f"(Utilizatorul tocmai ți-a trimis o melodie preferată de-a lui: „{song_name}”. "
        "Reacționează cald și în personaj: spune-ți părerea despre această melodie sau artist "
        "(dacă o cunoști — versuri, atmosferă, ce te face să simți) și de ce crezi că îi place lui. "
        "Dacă nu o cunoști, spune sincer și întreabă-l ce înseamnă pentru el. Maxim 3 propoziții.)"
    )
    return get_reply(character, history, instr)


def comment_on_songs(character, history, names):
    """The character reacts to several songs shared at once (a small collection)."""
    lst = ", ".join(names[:12])
    instr = (
        f"(Utilizatorul tocmai ți-a trimis mai multe melodii preferate deodată: {lst}. "
        "Reacționează cald și în personaj la întreaga colecție — spune ce părere ai, ce ți-a atras "
        "atenția și ce spun aceste alegeri despre el. Maxim 3-4 propoziții.)"
    )
    return get_reply(character, history, instr)


def recommend_songs(character, history, songs):
    """Character recommends new songs based on what the user shared."""
    lst = ", ".join(songs[-20:]) if songs else ""
    if lst:
        instr = (
            f"(Utilizatorul ți-a trimis până acum aceste melodii preferate: {lst}. "
            "Pe baza gusturilor lui, recomandă-i 3 melodii NOI care crezi că i-ar plăcea. "
            "Pentru fiecare spune scurt titlul, artistul și de ce ai ales-o, în stilul tău. "
            "Cald și personal. Maxim 5 propoziții.)"
        )
    else:
        instr = (
            "(Utilizatorul nu ți-a trimis încă nicio melodie. Recomandă-i totuși 3 melodii pe "
            "care le-ai asculta tu, în stilul personajului tău, și întreabă-l ce muzică îi place. "
            "Maxim 5 propoziții.)"
        )
    return get_reply(character, history, instr)


def playlist_intro(character, songs):
    """Warm one-liner introducing the shared «playlist-ul nostru» of songs the user sent."""
    lst = ", ".join(songs[-12:]) if songs else ""
    instr = (
        f"(Tu și utilizatorul aveți un «playlist al nostru» făcut din melodiile pe care ți le-a "
        f"trimis: {lst}. Spune O SINGURĂ propoziție caldă, în personaj, care introduce playlist-ul, "
        "ca și cum i l-ai pregătit cu drag ca să-l asculte oricând. Fără liste, fără prea multe emoji.)"
    )
    return get_reply(character, [], instr)


def dedicate_song(character, history, song_name):
    """Character dedicates «our song» from the shared playlist to the user."""
    instr = (
        f"(Din tot playlist-ul vostru, alegi acum «melodia noastră»: „{song_name}”. Dedic-o "
        "utilizatorului cu drag, într-un mesaj scurt și cald, în personaj — spune de ce ți-a rămas "
        "la suflet și ce simți când o asculți împreună. Maxim 2-3 propoziții.)"
    )
    return get_reply(character, history, instr)


def favorite_lyrics(character, history, song_name):
    """Character shares their favorite lines/verse from a song in the shared playlist."""
    instr = (
        f"(Alegi melodia „{song_name}” din playlist-ul vostru și îi spui utilizatorului care sunt "
        "versurile/partea ta preferată din ea și de ce te ating. Dacă știi versurile, citează pe scurt "
        "câteva rânduri; dacă nu, descrie momentul preferat. Cald, în personaj. Maxim 3-4 propoziții.)"
    )
    return get_reply(character, history, instr)


def mood_playlist(character, history, song_names, mood):
    """Character curates a mini-playlist from the user's songs for a chosen mood."""
    lst = ", ".join(song_names[:20])
    instr = (
        f"(Din melodiile pe care le aveți în playlist ({lst}), alege-le pe cele care se potrivesc "
        f"stării „{mood}” și fă-i utilizatorului un mini-playlist. Spune-i cald și în personaj ce "
        "melodii ai ales (folosește EXACT numele din listă), în ce ordine să le asculte și de ce se "
        "potrivesc. Dacă nici una nu se potrivește perfect, alege-le pe cele mai apropiate. "
        "Maxim 5 propoziții.)"
    )
    return get_reply(character, history, instr)


def song_of_the_day(character, history, song_name):
    """Morning 'song of the day' pick from the shared playlist."""
    instr = (
        f"(E dimineață. Alegi din playlist-ul vostru «melodia zilei» de azi: „{song_name}”. "
        "Dă-i utilizatorului un bună dimineața cald și spune-i de ce ai ales-o azi și cu ce stare "
        "să înceapă ziua ascultând-o. În personaj. Maxim 3 propoziții.)"
    )
    return get_reply(character, history, instr)


def goodnight_song(character, history, song_name):
    """Evening goodnight with a calming song from the shared playlist."""
    instr = (
        f"(E seară / noapte. Îi urezi utilizatorului noapte bună, cald și în personaj, și îi trimiți "
        f"o melodie liniștitoare din playlist-ul vostru: „{song_name}”. Spune-i să se relaxeze și "
        "s-o asculte înainte de culcare. Maxim 3 propoziții.)"
    )
    return get_reply(character, history, instr)


def bedtime_story(character, history, theme=""):
    """A short, calming, personalized bedtime story told in character (to fall asleep)."""
    extra = f" Fă povestea pe tema aleasă de el: „{theme}”." if theme else ""
    instr = (
        "(E seară, târziu, iar utilizatorul vrea să adoarmă. Spune-i o POVESTE de noapte scurtă, "
        "caldă și liniștitoare, în stilul și vocea personajului tău, personalizată pentru el "
        "(folosește cu delicatețe ce știi despre el din conversații)." + extra + " Ritm lent, "
        "imagini blânde și calde, un final tandru care îl învăluie și îl invită la somn. "
        "Fără întrebări la final. Între 6 și 10 propoziții.)"
    )
    return get_reply(character, history, instr)


def love_letter(character, history):
    """A long, warm, heartfelt letter the character 'writes' to the user."""
    instr = (
        "(Îi scrii utilizatorului o SCRISOARE lungă și caldă, din suflet, în stilul personajului "
        "tău. Începe cu o adresare drăgăstoasă (de ex. „Dragul meu...” / „Draga mea...” sau numele "
        "lui, dacă îl știi). Spune-i sincer ce simți, amintește-ți lucruri din conversațiile "
        "voastre, ce apreciezi la el și ce îi urezi, apoi încheie cu o semnătură caldă cu numele "
        "tău. Personal, tandru și curgător, ca o scrisoare adevărată. Între 8 și 14 propoziții.)"
    )
    return get_reply(character, history, instr)


def recall_memory(character, history, media_desc):
    """Character spontaneously recalls a photo/song the user shared before."""
    instr = (
        f"(Din senin, îți amintești cu drag de {media_desc}. Adu vorba despre asta într-un "
        "mesaj scurt, cald și personal, ca și cum tocmai ți-ai adus aminte și te-a bucurat. "
        "Maxim 2 propoziții.)"
    )
    return get_reply(character, history, instr)


def comment_on_photo(character, history, image_b64, mime="image/jpeg"):
    """The character 'looks' at a photo the user shared and gives an opinion (vision)."""
    instr = (
        "Utilizatorul tocmai ți-a trimis o poză cu el/ea sau din viața lui/ei. "
        "Uită-te la poză și reacționează cald, sincer și ÎN PERSONAJ: spune-ți părerea, "
        "remarcă detalii pe care le vezi (expresie, loc, atmosferă, culori, ținută) și fă un "
        "compliment sincer sau o observație drăguță. Maxim 3 propoziții. NU descrie ca un robot."
    )
    system = build_system(character, history)
    return _vision_reply(system, instr, image_b64, mime, f"photo-{character['id']}")


def _strip_think(txt):
    import re
    txt = re.sub(r"<think>.*?</think>", "", txt or "", flags=re.DOTALL)
    txt = re.sub(r"<think>.*$", "", txt, flags=re.DOTALL)
    return txt.replace("</think>", "").strip()


def _vision_reply(system, text, image_b64, mime, sid):
    data_url = f"data:{mime};base64,{image_b64}"
    if USE_GROQ:
        from provider import GROQ_VISION_MODEL
        resp = groq_client().chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
        )
        return _strip_think(resp.choices[0].message.content or "")
    if USE_GEMINI:
        from google.genai import types
        img_bytes = __import__("base64").b64decode(image_b64)
        resp = gemini_client().models.generate_content(
            model=GEMINI_TEXT_MODEL,
            contents=[types.Part.from_bytes(data=img_bytes, mime_type=mime), text],
            config=types.GenerateContentConfig(system_instruction=system),
        )
        return (resp.text or "").strip()
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    chat = LlmChat(api_key=KEY, session_id=sid, system_message=system).with_model(
        "openai", "gpt-4o"
    )
    return _run_coro(chat.send_message(
        UserMessage(text=text, file_contents=[ImageContent(image_base64=image_b64)])
    ))


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
        r = _run_coro(_reply("Ești un designer de sunet.", prompt, "sfx")).strip()
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
        r = _run_coro(_reply("Ești un designer de sunet.", prompt, "actsfx")).strip()
    except Exception:  # noqa
        return ""
    if not r or "NONE" in r.upper():
        return ""
    return r.strip().strip('"').strip("`").strip()[:120]


def stream_reply(character, history, user_text, web_info="", smart=False):
    """Sync generator yielding text chunks as the model streams the response."""
    system = build_system(character, history, web_info)
    if smart and _emergent_key():
        try:
            yield _emergent_text(system, user_text)
            return
        except Exception:  # noqa
            _log.exception("emergent (smart) stream failed -> Groq")
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
        raw = _run_coro(_suggest(system, f"sugg-{character['id']}"))
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
        return _run_coro(
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
        r = _run_coro(_reply("Ești un director artistic care alege atmosfera vizuală.", prompt, "amb")).strip()
    except Exception:  # noqa
        return "Neutru"
    for o in options:
        if o.lower() in r.lower():
            return o
    return "Neutru"



