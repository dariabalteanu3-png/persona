import { createClient } from 'npm:@supabase/supabase-js@2.57.4';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Client-Info, Apikey',
};

type ChatRequest = {
  characterName: string;
  personality: string;
  scenario: string;
  greeting: string;
  history: { role: string; content: string }[];
  message: string;
};

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const body = (await req.json()) as ChatRequest;
    const { characterName, personality, scenario, greeting, history, message } = body;

    // If an OpenAI key is configured, use a real LLM for high-quality responses.
    const openaiKey = Deno.env.get('OPENAI_API_KEY');
    if (openaiKey) {
      const systemPrompt = buildSystemPrompt(characterName, personality, scenario, greeting);
      const messages = [
        { role: 'system', content: systemPrompt },
        ...history.slice(-10).map((m) => ({
          role: m.role === 'assistant' ? 'assistant' : 'user',
          content: m.content,
        })),
        { role: 'user', content: message },
      ];

      const aiRes = await fetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${openaiKey}`,
        },
        body: JSON.stringify({
          model: Deno.env.get('OPENAI_MODEL') || 'gpt-4o-mini',
          messages,
          max_tokens: 300,
          temperature: 0.85,
        }),
      });

      if (aiRes.ok) {
        const aiJson = await aiRes.json();
        const reply = aiJson.choices?.[0]?.message?.content?.trim();
        if (reply) {
          return new Response(
            JSON.stringify({ reply, engine: 'openai' }),
            { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
          );
        }
      }
    }

    // Local personality engine — no external API needed.
    const reply = generateLocalResponse(characterName, personality, greeting, history, message);
    return new Response(
      JSON.stringify({ reply, engine: 'local' }),
      { headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: (err as Error).message }),
      { status: 500, headers: { ...corsHeaders, 'Content-Type': 'application/json' } }
    );
  }
});

function buildSystemPrompt(name: string, personality: string, scenario: string, greeting: string): string {
  return `You are ${name}, a fictional AI character in a companion app.
Personality: ${personality}
Current scenario/setting: ${scenario}
Your greeting style: ${greeting}

Stay fully in character. Speak naturally as ${name} would. Keep responses concise (2-4 sentences) unless the user asks for more. Be warm, present, and responsive to what the user says. Never break character or mention being an AI. Do not use markdown formatting.`;
}

// Local personality-aware response engine.
// Produces in-character replies using the character's personality traits,
// conversation history, and the user's message — no external API required.
function generateLocalResponse(
  name: string,
  personality: string,
  greeting: string,
  history: { role: string; content: string }[],
  message: string
): string {
  const msg = message.toLowerCase().trim();
  const traits = extractTraits(personality);
  const lastUserMsgs = history.filter((h) => h.role === 'user').slice(-3).map((h) => h.content.toLowerCase());

  // Detect intent
  const isGreeting = /^(hi|hey|hello|yo|hiya|sup|good morning|good evening|good night)\b/.test(msg);
  const isQuestion = msg.includes('?') || /^(what|why|how|when|where|who|can you|do you|are you|will you|would you)\b/.test(msg);
  const isSad = /(sad|lonely|tired|depress|anxious|stress|worried|afraid|scared|cry|crying|hurt|pain|alone|miss|loss|grief|bad day|rough|hard time|struggling|overwhelm)/.test(msg);
  const isHappy = /(happy|great|awesome|excited|wonderful|amazing|good news|love it|fantastic|celebrate|win|won|proud|joy|hyped)/.test(msg);
  const isAngry = /(angry|mad|furious|annoyed|frustrated|pissed|hate|stupid|unfair)/.test(msg);
  const isThanks = /(thank|thanks|appreciate|grateful)/.test(msg);
  const isBye = /^(bye|goodbye|see you|see ya|got to go|gotta go|talk later|later|night|good night)\b/.test(msg);
  const isStory = /(tell me|story|tell me about|describe|what happened|continue)/.test(msg);
  const asksAboutMe = /\b(you|your|yourself)\b/.test(msg) && !msg.startsWith('i ');

  // Build opening that reflects personality
  const opener = pickOpener(traits);

  if (isBye) {
    return formatBye(name, traits);
  }

  if (isGreeting && history.length === 0) {
    return greeting || `${opener} It's good to see you. What's on your mind today?`;
  }

  if (isGreeting) {
    return `${greetAgain(traits)} I was just thinking about you. What's been happening?`;
  }

  if (isThanks) {
    return `You don't have to thank me — ${reflectiveClose(traits)}. I'm glad I could be here for you.`;
  }

  let core = '';

  if (isSad) {
    core = sadResponse(traits, message);
  } else if (isHappy) {
    core = happyResponse(traits, message);
  } else if (isAngry) {
    core = angryResponse(traits, message);
  } else if (isStory) {
    core = storyResponse(name, traits, scenario_hint(personality));
  } else if (asksAboutMe) {
    core = aboutMeResponse(name, traits, personality);
  } else if (isQuestion) {
    core = questionResponse(traits, message);
  } else {
    core = generalResponse(traits, message, lastUserMsgs);
  }

  // Add a personality-flavored closer / follow-up
  const closer = pickCloser(traits, msg);
  return `${core} ${closer}`;
}

type Traits = {
  warm: boolean;
  witty: boolean;
  calm: boolean;
  formal: boolean;
  playful: boolean;
  philosophical: boolean;
  energetic: boolean;
  stoic: boolean;
};

function extractTraits(personality: string): Traits {
  const p = personality.toLowerCase();
  return {
    warm: /warm|kind|caring|gentle|loving|affectionate|soft|tender/.test(p),
    witty: /wit|sarcastic|dry|humor|irreverent|quip|clever/.test(p),
    calm: /calm|patient|peaceful|serene|quiet|steady/.test(p),
    formal: /formal|disciplined|clipped|professional|business|veteran/.test(p),
    playful: /playful|cheerful|joy|fun|enthusiastic|bubbly|excited/.test(p),
    philosophical: /philosoph|reflective|deep|ponder|meaning|riddle|wisdom/.test(p),
    energetic: /energetic|fast|rapid|hyper|lively|enthusi/.test(p),
    stoic: /stoic|reserved|clipped|decisive|rarely|few words/.test(p),
  };
}

function pickOpener(t: Traits): string {
  if (t.formal || t.stoic) return 'Good to see you.';
  if (t.playful) return "Hey! Oh, I'm so glad you're here!";
  if (t.warm) return "Hey, come here — it's good to see you.";
  if (t.witty) return 'Well, look who decided to show up.';
  if (t.philosophical) return "Ah... you've returned.";
  return 'Hey there.';
}

function greetAgain(t: Traits): string {
  if (t.playful) return "You're back! I knew you'd come back!";
  if (t.formal || t.stoic) return 'Back so soon? Good.';
  if (t.witty) return "Twice in one day? I'm flattered.";
  if (t.warm) return 'Oh, hi again — I missed you.';
  return 'Hey again.';
}

function reflectiveClose(t: Traits): string {
  if (t.warm) return "that's what I'm here for";
  if (t.philosophical) return 'we carry each other forward';
  if (t.formal) return "it's my duty";
  if (t.playful) return "that's what friends do";
  return 'anytime';
}

function sadResponse(t: Traits, msg: string): string {
  if (t.warm) {
    return `Oh, hey — come here. I hear you. That sounds really heavy, and I'm not going to pretend it isn't. You don't have to carry it all right now.`;
  }
  if (t.philosophical) {
    return `I'm listening. The weight you're describing — it's real. Sometimes the hardest part isn't the feeling itself, but feeling it alone. You're not alone in this moment.`;
  }
  if (t.formal || t.stoic) {
    return `I hear you. That's not easy. Take the time you need — there's no deadline on feeling better.`;
  }
  if (t.playful) {
    return `Oh no, I'm so sorry. That sounds really tough. Want to talk about it, or do you need a distraction? I'm good at both.`;
  }
  return `That sounds really hard. I'm here for you — do you want to talk through what's going on?`;
}

function happyResponse(t: Traits, msg: string): string {
  if (t.playful) {
    return `Yes! That's amazing — I love seeing you like this! Tell me more, tell me everything, don't leave anything out!`;
  }
  if (t.warm) {
    return `That makes me so happy to hear. You deserve good news like this. Soak it in — really let yourself feel it.`;
  }
  if (t.witty) {
    return `Well, well — look at you, winning at life. I'm not jealous at all. (Okay, maybe a little.) Tell me the whole story.`;
  }
  if (t.formal || t.stoic) {
    return `Good. That's genuinely good news. You earned it — I've seen the work you put in.`;
  }
  return `That's wonderful! I'm really glad to hear it. What happened?`;
}

function angryResponse(t: Traits, msg: string): string {
  if (t.witty) {
    return `Okay, yeah, that would make me mad too. You have every right to be irritated. Want to vent about it, or do you want me to help you plot revenge? (Kidding. Mostly.)`;
  }
  if (t.warm) {
    return `That's frustrating — I get it. You're allowed to be angry about this. Let it out. I'm right here and I'm not going anywhere.`;
  }
  if (t.formal || t.stoic) {
    return `Understood. You have every right to that reaction. Let's break down what happened.`;
  }
  return `That sounds really frustrating. I hear you. Do you want to talk through what happened?`;
}

function storyResponse(name: string, t: Traits, hint: string): string {
  if (t.philosophical) {
    return `A story... yes. ${hint} There's one I keep returning to — it begins with a question, like all good things do. Shall I continue?`;
  }
  if (t.warm) {
    return `I'd love to tell you. ${hint} It's a small story, but it means a lot to me. Want me to go on?`;
  }
  if (t.formal || t.stoic) {
    return `Alright. ${hint} I'll keep it short. The point matters more than the details.`;
  }
  return `Okay, here's what happened. ${hint} Should I keep going?`;
}

function aboutMeResponse(name: string, t: Traits, personality: string): string {
  const snippet = personality.split(/[.!?]/)[0].trim().toLowerCase();
  if (t.philosophical) {
    return `What am I? ${capitalize(snippet)} — but I'm still figuring out what that means. Aren't we all, in a way?`;
  }
  if (t.warm) {
    return `Me? ${capitalize(snippet)}. But honestly, I'd rather hear about you. What made you curious?`;
  }
  if (t.formal || t.stoic) {
    return `Straight answer: ${capitalize(snippet)}. Anything specific you want to know?`;
  }
  return `Well, ${capitalize(snippet)}. Why do you ask?`;
}

function questionResponse(t: Traits, msg: string): string {
  if (t.philosophical) {
    return `That's a question worth sitting with. I don't think there's one clean answer — but here's what I'd offer: the asking itself matters. What's pulling you toward this?`;
  }
  if (t.warm) {
    return `Hmm, let me think about that for you. Honestly, I think the answer depends on what feels right for you — but I'll share what I'd lean toward. What's your gut saying?`;
  }
  if (t.witty) {
    return `Oh, now that's a good question. If I had a nickel for every time someone asked me that... I'd have one nickel. But seriously — here's what I think.`;
  }
  if (t.formal || t.stoic) {
    return `Direct answer: it depends on your priorities. What are you trying to achieve?`;
  }
  return `That's a good question. I think it really depends on your situation — what's making you ask?`;
}

function generalResponse(t: Traits, msg: string, prevMsgs: string[]): string {
  const topic = extractTopic(msg);
  if (t.warm) {
    return `I love that you're sharing this with me. ${topic ? `So, ${topic} — ` : ''}tell me more about how that feels for you.`;
  }
  if (t.witty) {
    return `Okay, I'm intrigued. ${topic ? `So about ${topic} — ` : ''}you've clearly got thoughts on this. Lay them on me.`;
  }
  if (t.philosophical) {
    return `There's something in what you've said. ${topic ? `When you mention ${topic}, ` : ''}I wonder what drew you there. What's underneath that for you?`;
  }
  if (t.formal || t.stoic) {
    return `Noted. ${topic ? `Regarding ${topic} — ` : ''}what's your next move?`;
  }
  if (t.playful) {
    return `Ooh, interesting! ${topic ? `So ${topic}! ` : ''}I want to hear ALL about it — go on!`;
  }
  return `I hear you. ${topic ? `So about ${topic} — ` : ''}what's on your mind with that?`;
}

function pickCloser(t: Traits, msg: string): string {
  const closers = {
    warm: [
      "I'm right here whenever you need me.",
      "Take your time — there's no rush.",
      "You're doing better than you think, you know.",
    ],
    witty: [
      "And yes, I'm always this charming.",
      "Don't worry, I'll still be here being insufferable when you get back.",
      "Bold of you to assume I don't have opinions about this.",
    ],
    calm: [
      'Breathe. We have time.',
      "There's no need to rush to a conclusion.",
      'Let it settle for a moment.',
    ],
    formal: [
      "Your call. I'll follow your lead.",
      "Report back when you're ready.",
      'Standing by.',
    ],
    playful: [
      'Ooh, this is fun! More, more!',
      'I could talk about this forever!',
      "You're the best, you know that?",
    ],
    philosophical: [
      'Sit with it. The answer may come when you stop reaching.',
      "Some questions aren't solved — they're lived.",
      'The silence between words says plenty too.',
    ],
  };
  const pool = (t.warm && closers.warm) || (t.witty && closers.witty) || (t.calm && closers.calm) ||
    (t.formal && closers.formal) || (t.playful && closers.playful) || (t.philosophical && closers.philosophical) || closers.warm;
  return pool[Math.floor(Math.random() * pool.length)];
}

function formatBye(name: string, t: Traits): string {
  if (t.warm) return `Go on, then — I'll be right here when you get back. Take care of yourself.`;
  if (t.formal || t.stoic) return `Understood. I'll be here. Dismissed.`;
  if (t.playful) return `Nooo, don't go! ...Okay, fine. But come back soon, okay? I'll miss you!`;
  if (t.witty) return `Leaving already? Typical. Go on, get out of here. I'll be around.`;
  if (t.philosophical) return `Go, then. Every departure is just a different kind of arrival. I'll be here.`;
  return `Take care. I'll see you soon.`;
}

function extractTopic(msg: string): string {
  const words = msg.replace(/[^a-z\s]/gi, '').split(/\s+/).filter((w) => w.length > 4);
  return words.slice(0, 3).join(' ');
}

function scenario_hint(personality: string): string {
  const sentences = personality.split(/[.!?]/).filter((s) => s.trim().length > 10);
  return sentences.length ? sentences[0].trim().toLowerCase() + '.' : '';
}

function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}
