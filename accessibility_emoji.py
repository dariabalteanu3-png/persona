# Emoji Accessibility with Voice Over Support & Safari Compatibility
# Enhanced screen reader support and browser compatibility

ACCESSIBLE_EMOJIS = [
    {
        "emoji": "😊",
        "label": "zâmbitor",
        "aria_label": "zâmbitor fericit",
        "description": "exprimă bucurie și prietenie",
        "voice_text": "zâmbitor",
    },
    {
        "emoji": "❤️",
        "label": "inimă",
        "aria_label": "inimă roșie",
        "description": "exprimă iubire și afecțiune",
        "voice_text": "inimă",
    },
    {
        "emoji": "😂",
        "label": "râs",
        "aria_label": "râs până la lacrimi",
        "description": "exprimă amuzament și răs",
        "voice_text": "râs",
    },
    {
        "emoji": "🥰",
        "label": "plin de iubire",
        "aria_label": "fată cu ochi de inimă",
        "description": "exprimă iubire și adorație",
        "voice_text": "plin de iubire",
    },
    {
        "emoji": "😍",
        "label": "îndrăgostit",
        "aria_label": "fată cu ochi de inimă",
        "description": "exprimă admirație și iubire",
        "voice_text": "îndrăgostit",
    },
    {
        "emoji": "😘",
        "label": "sărut",
        "aria_label": "față cu sărut",
        "description": "exprimă tandreță și afecțiune",
        "voice_text": "sărut",
    },
    {
        "emoji": "👍",
        "label": "deget ridicat",
        "aria_label": "deget arătător ridicat",
        "description": "exprimă aprobare și acord",
        "voice_text": "ok, de acord",
    },
    {
        "emoji": "🙏",
        "label": "mulțumire",
        "aria_label": "mâini în rugăciune",
        "description": "exprimă mulțumire și recunoștință",
        "voice_text": "mulțumesc",
    },
    {
        "emoji": "🤗",
        "label": "îmbrățișare",
        "aria_label": "fată care deschide brațele",
        "description": "exprimă grijă și îmbrățișare",
        "voice_text": "îmbrățișare",
    },
    {
        "emoji": "😢",
        "label": "trist",
        "aria_label": "fată care plânge",
        "description": "exprimă tristețe și durere",
        "voice_text": "trist",
    },
    {
        "emoji": "😮",
        "label": "surprins",
        "aria_label": "fată surprinzătoare",
        "description": "exprimă surpriză și șoc",
        "voice_text": "surprins, wow",
    },
    {
        "emoji": "🎉",
        "label": "sărbătoare",
        "aria_label": "frunzele de petrecere",
        "description": "exprimă bucurie și sărbătoare",
        "voice_text": "sărbătoare, ura",
    },
    {
        "emoji": "🔥",
        "label": "fierbinte",
        "aria_label": "foc",
        "description": "exprimă admirație și entuziasm",
        "voice_text": "fierbinte, impresionant",
    },
    {
        "emoji": "🌹",
        "label": "trandafir",
        "aria_label": "trandafir roșu",
        "description": "exprimă iubire și frumusețe",
        "voice_text": "trandafir",
    },
    {
        "emoji": "💕",
        "label": "doi inimi",
        "aria_label": "doi inimi roșii",
        "description": "exprimă iubire dublă și afecțiune",
        "voice_text": "doi inimi",
    },
    {
        "emoji": "😴",
        "label": "somn",
        "aria_label": "fată care doarme",
        "description": "exprimă oboseală și somn",
        "voice_text": "somn, obosit",
    },
    {
        "emoji": "😅",
        "label": "râs nervos",
        "aria_label": "fată cu sudoare",
        "description": "exprimă nervozitate și umor",
        "voice_text": "râs nervos",
    },
    {
        "emoji": "🥳",
        "label": "petrecere",
        "aria_label": "fată cu coiful de petrecere",
        "description": "exprimă bucurie și sărbătoare",
        "voice_text": "petrecere, celebrare",
    },
    {
        "emoji": "😎",
        "label": "cool",
        "aria_label": "fată cu ochelari de soare",
        "description": "exprimă atitudine și stil",
        "voice_text": "cool, ușor",
    },
    {
        "emoji": "🤔",
        "label": "gânditor",
        "aria_label": "fată gândind",
        "description": "exprimă gândire și reflecție",
        "voice_text": "gândind, reflectez",
    },
]


def get_accessible_emoji_html(emoji_data, button_key):
    """
    Generate accessible HTML for emoji button with Safari & Chrome compatibility.
    Supports Voice Over (iOS/macOS) and TalkBack (Android).
    """
    emoji = emoji_data["emoji"]
    label = emoji_data["aria_label"]
    voice_text = emoji_data["voice_text"]
    
    html = f'''
    <button 
        id="emoji_btn_{button_key}"
        aria-label="{label}"
        role="button"
        tabindex="0"
        style="
            background: none;
            border: 1px solid #FF7A5955;
            border-radius: 10px;
            padding: 10px 14px;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            min-width: 50px;
            min-height: 50px;
            -webkit-appearance: none;
            -webkit-font-smoothing: antialiased;
            -webkit-touch-callout: none;
        "
        data-emoji="{emoji}"
        data-voice-text="{voice_text}"
        data-label="{label}"
        onmouseenter="this.style.borderColor='#FF7A59'; this.style.transform='scale(1.08)';"
        onmouseleave="this.style.borderColor='#FF7A5955'; this.style.transform='scale(1)';"
        ontouchstart="this.style.borderColor='#FF7A59'; this.style.transform='scale(1.08)';"
        ontouchend="this.style.borderColor='#FF7A5955'; this.style.transform='scale(1)';"
    >
        <span aria-hidden="true" style="font-size: 24px; line-height: 1;">{emoji}</span>
    </button>
    '''
    return html


def get_emoji_section_html():
    """Generate the complete emoji section with accessibility features"""
    html = '''
    <div style="
        margin-top: 1.5rem;
        padding: 1rem;
        border-radius: 14px;
        background: linear-gradient(180deg, #16161c, #0f0f13);
        border: 1px solid #1e1e26;
    ">
        <div style="
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 0.8rem;
        ">
            <h3 style="
                margin: 0;
                font-family: Sora, sans-serif;
                font-size: 1.1rem;
                font-weight: 700;
                color: #ECECEC;
            ">
                😊 Exprimă-te cu Emoji
            </h3>
            <span style="
                font-size: 0.75rem;
                color: #8a8a95;
                padding: 0.3rem 0.6rem;
                background: #1a1a24;
                border-radius: 6px;
                border: 1px solid #2a2a38;
            ">
                Compatibil Safari, Chrome, Firefox
            </span>
        </div>
        
        <p style="
            margin: 0 0 1rem;
            color: #9a9aa6;
            font-size: 0.9rem;
        ">
            Apasă un emoji sau folosește cititorul de ecran (Voice Over / TalkBack). 
            Fiecare emoji are descriere vocală.
        </p>
        
        <div id="emoji_container" style="
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(50px, 1fr));
            gap: 8px;
            margin-bottom: 1rem;
        ">
            <!-- Emoji buttons will be inserted here by Streamlit -->
        </div>
        
        <div style="
            background: #0f1a12;
            border: 1px solid #1f5130;
            border-radius: 8px;
            padding: 0.6rem 0.8rem;
            font-size: 0.78rem;
            color: #8fdca8;
            line-height: 1.5;
        ">
            <strong>♿ Accessibility Info:</strong><br>
            • Tastatura: Tab pentru navigare, Enter/Space pentru selectare<br>
            • Voice Over (iOS): Glisează cu 1 deget, tap dublu pentru selectare<br>
            • TalkBack (Android): Glisează dreapta/stânga, tap dublu pentru selectare<br>
            • Atribuite ARIA labels pe fiecare buton
        </div>
    </div>
    '''
    return html


# Safari-specific compatibility fixes
SAFARI_COMPAT_JS = '''
<script>
// Safari and iOS compatibility enhancements
(function() {
    // Detect Safari/iOS
    const isSafari = /Safari/.test(navigator.userAgent) && !/Chrome/.test(navigator.userAgent);
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    
    // Fix audio playback on iOS (requires user interaction)
    if (isIOS) {
        document.addEventListener('touchstart', function() {
            const audio = document.querySelector('audio');
            if (audio) {
                audio.play().catch(() => {
                    // Autoplay prevented - user will click play
                });
            }
        }, { once: true });
    }
    
    // Enhance button touch targets for Safari
    const buttons = document.querySelectorAll('button');
    buttons.forEach(btn => {
        if (!btn.style.minHeight) {
            btn.style.minHeight = '44px'; // Apple's recommended touch target
            btn.style.minWidth = '44px';
        }
    });
    
    // Fix -webkit-appearance for better iOS rendering
    const inputs = document.querySelectorAll('input, textarea, select');
    inputs.forEach(inp => {
        inp.style.fontSize = '16px'; // Prevent auto-zoom on iOS
        inp.style.webkitAppearance = 'none';
    });
    
    // Prevent double-tap zoom on buttons
    buttons.forEach(btn => {
        btn.addEventListener('touchend', function(e) {
            e.preventDefault();
            this.click();
        });
    });
    
    // Support for VoiceOver gestures (iOS)
    if (window.VoiceOverActive !== undefined) {
        document.body.setAttribute('role', 'application');
    }
})();
</script>
'''

# Chrome/Android compatibility fixes
CHROME_COMPAT_JS = '''
<script>
// Chrome and Android compatibility enhancements
(function() {
    // Detect Chrome/Android
    const isChrome = /Chrome/.test(navigator.userAgent);
    const isAndroid = /Android/.test(navigator.userAgent);
    
    if (isAndroid) {
        // TalkBack support: improve focus visibility
        const style = document.createElement('style');
        style.textContent = `
            *:focus-visible {
                outline: 3px solid #FF7A59;
                outline-offset: 2px;
            }
            button:focus-visible {
                box-shadow: 0 0 8px #FF7A5966;
            }
        `;
        document.head.appendChild(style);
        
        // Improve haptic feedback
        function triggerHaptic() {
            if (navigator.vibrate) {
                navigator.vibrate(10);
            }
        }
        
        document.addEventListener('click', (e) => {
            if (e.target.tagName === 'BUTTON') {
                triggerHaptic();
            }
        });
    }
    
    // Chrome smooth scrolling
    document.documentElement.style.scrollBehavior = 'smooth';
    
    // Enhanced focus management
    let lastFocused = null;
    document.addEventListener('focusin', (e) => {
        lastFocused = e.target;
        if (e.target.tagName === 'BUTTON') {
            e.target.style.boxShadow = '0 0 8px rgba(255, 122, 89, 0.4)';
        }
    });
    
    document.addEventListener('focusout', (e) => {
        if (e.target.tagName === 'BUTTON') {
            e.target.style.boxShadow = 'none';
        }
    });
})();
</script>
'''
