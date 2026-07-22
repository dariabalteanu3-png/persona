# Timezone, Seasons & Day/Night Cycle Management
# Romania-focused with automatic season detection and day/night ambiance

from datetime import datetime
from zoneinfo import ZoneInfo

# Romania timezone (permanent)
ROMANIA_TIMEZONE = "Europe/Bucharest"

# Season date ranges (for Northern Hemisphere - Romania)
SEASONS = {
    "🌸 Primăvară": {
        "months": [3, 4, 5],
        "color": "#4ade80",
        "mood_prefix": "primăvăratică și plin de speranță",
        "emoji": "🌸",
    },
    "☀️ Vară": {
        "months": [6, 7, 8],
        "color": "#fbbf24",
        "mood_prefix": "cald și plin de energie",
        "emoji": "☀️",
    },
    "🍂 Toamnă": {
        "months": [9, 10, 11],
        "color": "#f97316",
        "mood_prefix": "nostalgic și gânditor",
        "emoji": "🍂",
    },
    "❄️ Iarnă": {
        "months": [12, 1, 2],
        "color": "#3b82f6",
        "mood_prefix": "cozy și plin de magie",
        "emoji": "❄️",
    },
}


def get_current_time_romania():
    """Get current time in Romania timezone"""
    return datetime.now(ZoneInfo(ROMANIA_TIMEZONE))


def get_time_display():
    """Get formatted time display for UI (HH:MM)"""
    now = get_current_time_romania()
    return now.strftime("%H:%M")


def get_day_period():
    """
    Determine if it's day or night in Romania
    - Night: 18:00 - 06:00 (6 PM - 6 AM)
    - Day: 06:00 - 18:00 (6 AM - 6 PM)
    """
    now = get_current_time_romania()
    hour = now.hour
    
    if 6 <= hour < 18:
        return {
            "period": "day",
            "emoji": "☀️",
            "name": "Zi",
            "icon": "☀️",
            "color": "#fbbf24",
            "ambiance_prefix": "insorit și plin de energie",
        }
    else:
        return {
            "period": "night",
            "emoji": "🌙",
            "name": "Noapte",
            "icon": "🌙",
            "color": "#3b82f6",
            "ambiance_prefix": "liniștit și plin de mister",
        }


def get_season():
    """Get current season in Romania"""
    now = get_current_time_romania()
    month = now.month
    
    for season_name, season_data in SEASONS.items():
        if month in season_data["months"]:
            return {
                "name": season_name,
                "emoji": season_data["emoji"],
                "color": season_data["color"],
                "mood_prefix": season_data["mood_prefix"],
                "months": season_data["months"],
            }
    
    # Fallback (shouldn't happen)
    return {
        "name": "❄️ Iarnă",
        "emoji": "❄️",
        "color": "#3b82f6",
        "mood_prefix": "cozy și plin de magie",
    }


def get_time_of_day_mood():
    """
    Get mood/ambiance based on time of day in Romania
    Used to enhance character responses
    """
    now = get_current_time_romania()
    hour = now.hour
    
    if 5 <= hour < 8:
        return {
            "name": "🌅 Dimineață (Devreme)",
            "mood": "blând și relaxat, cu o notă de somn",
            "ambiance": "pasari_cantand, noapte_linista",
        }
    elif 8 <= hour < 12:
        return {
            "name": "☀️ Dimineață (Târziu)",
            "mood": "energic și optimist",
            "ambiance": "pasari_cantand, cafenea",
        }
    elif 12 <= hour < 14:
        return {
            "name": "🍽️ Prânz",
            "mood": "vesel și socabil",
            "ambiance": "restaurant, cafenea",
        }
    elif 14 <= hour < 17:
        return {
            "name": "☀️ După-amiază",
            "mood": "concentrat și productiv",
            "ambiance": "birou, tastare",
        }
    elif 17 <= hour < 19:
        return {
            "name": "🌇 Seară (Devreme)",
            "mood": "calm și reflexiv",
            "ambiance": "vant_ușor, tunet_departat",
        }
    elif 19 <= hour < 22:
        return {
            "name": "🌙 Seară (Târziu)",
            "mood": "tandru și romantic",
            "ambiance": "candelă, noapte_linista",
        }
    else:  # 22:00 - 05:00
        return {
            "name": "🌙 Noapte",
            "mood": "liniștit și blând, plin de tăcere și mister",
            "ambiance": "noapte_linista, cricket, vant_ușor",
        }


def get_timezone_header_html():
    """Generate HTML header with timezone info, time, season, and day/night"""
    now = get_current_time_romania()
    time_display = now.strftime("%H:%M")
    date_display = now.strftime("%A, %d %B")  # e.g., "Tuesday, 22 July"
    date_display_ro = translate_date_to_romanian(date_display)
    
    day_info = get_day_period()
    season_info = get_season()
    
    html = f'''
    <div style="
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding: 1rem;
        background: linear-gradient(135deg, #17171f 0%, #1a1a24 100%);
        border: 1px solid #2a2a38;
        border-radius: 12px;
        margin-bottom: 1rem;
        flex-wrap: wrap;
    ">
        <!-- Timezone & Time -->
        <div style="display: flex; align-items: center; gap: 0.5rem;">
            <span style="font-size: 1.1rem;">🌍</span>
            <div>
                <div style="
                    font-size: 0.75rem;
                    color: #8a8a95;
                    font-weight: 600;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                ">România</div>
                <div style="
                    font-size: 1.4rem;
                    font-weight: 700;
                    font-family: 'Courier New', monospace;
                    color: #ECECEC;
                ">{time_display}</div>
            </div>
        </div>
        
        <!-- Date -->
        <div style="
            font-size: 0.85rem;
            color: #9a9aa6;
            text-align: center;
        ">
            {date_display_ro}
        </div>
        
        <!-- Day/Night -->
        <div style="
            display: flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.5rem 0.8rem;
            background: rgba({get_rgb_from_hex(day_info['color'])}, 0.1);
            border: 1px solid {day_info['color']}66;
            border-radius: 8px;
        ">
            <span style="font-size: 1rem;">{day_info['emoji']}</span>
            <span style="color: {day_info['color']}; font-weight: 600; font-size: 0.85rem;">
                {day_info['name']}
            </span>
        </div>
        
        <!-- Season -->
        <div style="
            display: flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.5rem 0.8rem;
            background: rgba({get_rgb_from_hex(season_info['color'])}, 0.1);
            border: 1px solid {season_info['color']}66;
            border-radius: 8px;
        ">
            <span style="font-size: 1rem;">{season_info['emoji']}</span>
            <span style="color: {season_info['color']}; font-weight: 600; font-size: 0.85rem;">
                {season_info['name']}
            </span>
        </div>
    </div>
    '''
    return html


def translate_date_to_romanian(english_date):
    """Translate English date format to Romanian"""
    translations = {
        "Monday": "Luni",
        "Tuesday": "Marți",
        "Wednesday": "Miercuri",
        "Thursday": "Joi",
        "Friday": "Vineri",
        "Saturday": "Sâmbătă",
        "Sunday": "Duminică",
        "January": "ianuarie",
        "February": "februarie",
        "March": "martie",
        "April": "aprilie",
        "May": "mai",
        "June": "iunie",
        "July": "iulie",
        "August": "august",
        "September": "septembrie",
        "October": "octombrie",
        "November": "noiembrie",
        "December": "decembrie",
    }
    
    result = english_date
    for eng, ro in translations.items():
        result = result.replace(eng, ro)
    
    return result


def get_rgb_from_hex(hex_color):
    """Convert hex color to RGB for rgba() CSS"""
    hex_color = hex_color.lstrip("#")
    return ",".join(str(int(hex_color[i : i + 2], 16)) for i in (0, 2, 4))


def get_character_timezone_mood(character_name):
    """
    Generate a personalized mood message for the character
    based on current time, season, and day/night cycle
    """
    time_info = get_time_of_day_mood()
    season_info = get_season()
    
    mood = f"{character_name} e {time_info['mood']} (e {time_info['name'].lower()} în România)"
    
    return {
        "mood": mood,
        "time_period": time_info["name"],
        "ambiance": time_info["ambiance"],
        "season": season_info["name"],
    }


# Session state helpers
def setup_timezone_in_session(st_session):
    """Initialize timezone and time-based values in Streamlit session"""
    if "_tz_init" not in st_session:
        st_session._tz_init = True
        st_session.timezone_display = get_time_display()
        st_session.current_season = get_season()
        st_session.day_period = get_day_period()
