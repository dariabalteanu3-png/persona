# Extended Ambient Sound Cues for Enhanced Immersion
# Used with voice.sound_effect() to create rich soundscapes

SOUND_CUES = {
    # ===== PLOAIE / APĂ =====
    "ploaie_usoara": "soft gentle rain falling on leaves, peaceful ambience, light rainfall",
    "ploaie_moderata": "medium rainfall, rain on roof, cozy indoor weather ambience",
    "ploaie_intensa": "heavy thunderstorm, intense rain, distant thunder rumbling",
    "tunet": "thunder rumbling, electrical storm, dramatic weather, lightning crack",
    "vant_puternic": "strong wind blowing through trees, howling wind, blustery weather",
    "vant_ușor": "gentle breeze, light wind, soft rustling leaves",
    "apa_curgand": "stream flowing, water running, babbling brook, river sound",
    
    # ===== NATURĂ =====
    "pasari_cantand": "birds chirping, dawn chorus, forest birds singing, peaceful nature",
    "insecte": "crickets chirping, cicadas buzzing, summer evening insects, nature sounds",
    "frunziste": "rustling leaves, leaves crunching, forest floor sounds, foliage rustling",
    "padure": "forest ambience, woodland sounds, nature background, birds and insects",
    "ocean": "waves crashing, beach waves, ocean surf, coastal sounds",
    "apa_de_firicel": "gentle water trickling, small stream, peaceful water sounds",
    
    # ===== CASĂ / BUCĂTĂRIE =====
    "cafea_fierband": "coffee brewing, coffee machine hissing, morning coffee ritual",
    "clocot_apa": "boiling water, kettle whistling, pot boiling on stove",
    "tigaie_prăjire": "frying in pan, sizzling food, cooking sounds, breakfast preparation",
    "cuptor": "oven preheating, baking sounds, kitchen ambience",
    "frigider": "refrigerator hum, fridge running, household background",
    "masina_spalat": "washing machine running, laundry cycle, household chore sounds",
    "usca_haine": "clothes dryer running, tumble dry sound, laundry room ambience",
    "televizor": "TV static, television background, white noise, screen glow ambience",
    "candelă": "candle flickering, fireplace crackling, warm cozy fire sounds",
    "foc_grajdar": "fireplace crackling, wood burning, warm fire ambience, cozy warmth",
    
    # ===== TRANSPORT =====
    "tren": "train departing, train whistle, railway station sounds, locomotive engine",
    "autobuz": "bus stopping, hydraulic brakes, city bus ambience, passengers boarding",
    "masina": "car driving, traffic noise, urban street sounds, distant cars passing",
    "avion": "airplane flying, jet engine, departing flight, cabin ambience",
    "bicicla": "bicycle pedaling, chain noise, bell ringing, cycling sounds",
    "scuter": "scooter engine, city commute, urban mobility sounds",
    "tramvai": "tram/streetcar running, electric hum, city transport, bell dinging",
    
    # ===== ANIMALE =====
    "pisici": "cat purring, meowing, kittens playing, feline sounds",
    "caini": "dogs barking, puppy whining, playful dogs, canine sounds",
    "pasari_salbatice": "wild birds calling, forest birds, nature wildlife sounds",
    "insecte_mari": "bees buzzing, wasps, flying insects, buzzing ambience",
    "cal": "horse neighing, hooves clopping, stable sounds, equine",
    "oite": "sheep bleating, farm animals, pastoral ambience",
    "pisari": "fish tank bubbling, aquarium sounds, water filter humming",
    
    # ===== OAMENI / SOCIAL =====
    "conversatie_rusca": "people speaking Russian in background, foreign conversation, ambient voices",
    "conversatie_engleza": "people speaking English background, foreign ambient chatter",
    "conversatie_romana": "people speaking Romanian background, local ambient voices, family talk",
    "cafenea": "coffee shop ambience, people chatting, espresso machine, background voices",
    "restaurant": "restaurant ambience, dining background, cutlery clinking, ambient dining",
    "piata": "market sounds, vendors calling, busy marketplace, shopping ambience",
    "gara": "train station, people announcing, crowd sounds, transportation hub",
    "scoala": "school hallway, children playing, classroom ambience, learning environment",
    "birou": "office ambience, people talking quietly, phone ringing, workplace sounds",
    
    # ===== BIROU / LUCRU =====
    "tastare": "keyboard typing, mouse clicking, computer work sounds, office productivity",
    "imprimanta": "printer running, xerox machine, office copying sounds",
    "telefon_birou": "phone ringing, office phone, dial tone, desk phone sounds",
    "pix_scris": "pen writing, paper rustling, note-taking sounds, office work",
    
    # ===== SPECIAL / AMBIENT =====
    "noapte_linista": "peaceful night ambience, distant crickets, quiet evening, sleep-inducing",
    "dimineatã_calm": "gentle morning ambience, birds starting to sing, sunrise sounds",
    "magneziod": "calm meditation background, minimal ambient, peaceful silence with soft tones",
    "tunet_departat": "distant thunder, storm approaching, atmospheric weather sounds",
    "ploaie_pe_geam": "rain on window, cozy indoor, rain pattering on glass, shelter ambience",
}

# Grouped by category for easy selection
SOUND_CATEGORIES = {
    "🌧️ Ploaie/Apă": [
        ("ploaie_usoara", "Ploaie ușoară"),
        ("ploaie_moderata", "Ploaie medie"),
        ("ploaie_intensa", "Ploaie intensă"),
        ("tunet", "Tunet"),
        ("vant_puternic", "Vânt puternic"),
        ("vant_ușor", "Vânt ușor"),
        ("apa_curgand", "Apă curgând"),
    ],
    "🌿 Natură": [
        ("pasari_cantand", "Păsări cântând"),
        ("insecte", "Insecte (cricheți, greieri)"),
        ("frunziste", "Frunziște (frunze foșnind)"),
        ("padure", "Pădure (ambianță generală)"),
        ("ocean", "Ocean (valuri)"),
        ("apa_de_firicel", "Apă de firicel"),
    ],
    "🏠 Casă/Bucătărie": [
        ("cafea_fierband", "Cafea fierbând"),
        ("clocot_apa", "Apă în clocot"),
        ("tigaie_prăjire", "Tigaie cu prăjitură"),
        ("cuptor", "Cuptor (preîncălzire)"),
        ("frigider", "Frigider (zumzăind)"),
        ("masina_spalat", "Mașină de spălat"),
        ("usca_haine", "Uscător de haine"),
        ("candelă", "Lumânare (tremurând)"),
        ("foc_grajdar", "Foc în grajdar"),
    ],
    "🚗 Transport": [
        ("tren", "Tren"),
        ("autobuz", "Autobuz"),
        ("masina", "Mașină"),
        ("avion", "Avion"),
        ("bicicla", "Bicicletă"),
        ("scuter", "Scuter"),
        ("tramvai", "Tramvai"),
    ],
    "🐾 Animale": [
        ("pisici", "Pisici (miau, pur)"),
        ("caini", "Câini (lătra, uita)"),
        ("pasari_salbatice", "Păsări sălbatice"),
        ("insecte_mari", "Insecte mari (albine, viespi)"),
        ("cal", "Cal (nechezat)"),
        ("oite", "Oi (behăit)"),
        ("pisari", "Pești (acvariu)"),
    ],
    "👥 Oameni/Social": [
        ("conversatie_rusca", "Conversație în rusă (fundal)"),
        ("conversatie_engleza", "Conversație în engleză (fundal)"),
        ("conversatie_romana", "Conversație în română (fundal)"),
        ("cafenea", "Cafenea (ambianță)"),
        ("restaurant", "Restaurant (ambianță)"),
        ("piata", "Piață (ambianță)"),
        ("gara", "Gară (ambianță)"),
        ("scoala", "Școală (ambianță)"),
        ("birou", "Birou (ambianță)"),
    ],
    "💼 Birou/Lucru": [
        ("tastare", "Tastare (keyboard)"),
        ("imprimanta", "Imprimantă/Xerox"),
        ("telefon_birou", "Telefon birou"),
        ("pix_scris", "Pix/Scris"),
    ],
    "✨ Special": [
        ("noapte_linista", "Noapte liniștit"),
        ("dimineatã_calm", "Dimineață calmă"),
        ("magneziod", "Meditație/Ambient minimal"),
        ("tunet_departat", "Tunet departat"),
        ("ploaie_pe_geam", "Ploaie pe geam (cozy)"),
    ],
}


def get_sound_cue(category_key, sound_key):
    """Get the full sound cue description for voice.sound_effect()"""
    return SOUND_CUES.get(sound_key, f"ambient {sound_key}")


def get_category_sounds(category):
    """Get all sounds in a category"""
    return SOUND_CATEGORIES.get(category, [])
