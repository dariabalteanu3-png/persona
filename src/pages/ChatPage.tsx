import { useState, useEffect, useRef, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from '@/context/RouterContext';
import { getColor } from '@/lib/colors';
import { getAmbientSound } from '@/lib/ambient';
import { audioEngine } from '@/lib/audioEngine';
import { speak, stopSpeaking, loadVoices, type VoiceSettings } from '@/lib/voice';
import type { Character, Message } from '@/types/database';
import { ArrowLeft, Send, Volume2, Square, Loader2, Bookmark, MessageCircle, Sparkles, Trash2, VolumeX } from 'lucide-react';

export function ChatPage({ characterId, conversationId }: { characterId: string; conversationId?: string }) {
  const { user } = useAuth();
  const { navigate } = useRouter();
  const [character, setCharacter] = useState<Character | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [convId, setConvId] = useState<string | undefined>(conversationId);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [ambientStarted, setAmbientStarted] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const voiceSettings: VoiceSettings = character
    ? {
        voice_name: character.voice_name,
        voice_lang: character.voice_lang,
        voice_pitch: character.voice_pitch,
        voice_rate: character.voice_rate,
        voice_warmth: character.voice_warmth,
      }
    : { voice_name: '', voice_lang: 'en-US', voice_pitch: 1, voice_rate: 1, voice_warmth: 0.5 };

  const loadData = useCallback(async () => {
    if (!user) return;
    const { data: char } = await supabase
      .from('characters')
      .select('*')
      .eq('id', characterId)
      .maybeSingle();
    if (char) {
      setCharacter(char as Character);
      loadVoices();

      let activeConvId = convId;
      if (!activeConvId) {
        const { data: newConv } = await supabase
          .from('conversations')
          .insert({ user_id: user.id, character_id: characterId, title: (char as Character).name })
          .select()
          .single();
        if (newConv) {
          activeConvId = (newConv as { id: string }).id;
          setConvId(activeConvId);
        }
      }

      if (activeConvId) {
        const { data: msgs } = await supabase
          .from('messages')
          .select('*')
          .eq('conversation_id', activeConvId)
          .order('created_at', { ascending: true });
        setMessages((msgs as Message[]) ?? []);

        // If no messages, insert the greeting
        if ((!msgs || msgs.length === 0) && (char as Character).greeting) {
          const greeting = (char as Character).greeting;
          const { data: greetingMsg } = await supabase
            .from('messages')
            .insert({ conversation_id: activeConvId, role: 'assistant', content: greeting })
            .select()
            .single();
          if (greetingMsg) {
            setMessages([greetingMsg as Message]);
            if (voiceEnabled) {
              speak(greeting, voiceSettings);
              setSpeakingId((greetingMsg as Message).id);
            }
          }
        }
      }
      setLoading(false);
    } else {
      setLoading(false);
    }
  }, [user, characterId, convId, voiceEnabled, voiceSettings]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Start ambient mood for character
  useEffect(() => {
    if (character && character.ambient_mood && character.ambient_mood !== 'none' && !ambientStarted) {
      const sound = getAmbientSound(character.ambient_mood);
      if (sound) {
        audioEngine.play(character.ambient_mood, 0.3);
        setAmbientStarted(true);
      }
    }
    return () => {
      if (character && character.ambient_mood && character.ambient_mood !== 'none') {
        audioEngine.stop(character.ambient_mood);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character?.id]);

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, sending]);

  const handleSend = async () => {
    if (!input.trim() || sending || !convId || !character) return;
    const content = input.trim();
    setInput('');
    if (inputRef.current) inputRef.current.style.height = 'auto';

    // Insert user message
    const { data: userMsg } = await supabase
      .from('messages')
      .insert({ conversation_id: convId, role: 'user', content })
      .select()
      .single();
    if (userMsg) {
      setMessages((m) => [...m, userMsg as Message]);
    }

    // Update conversation timestamp
    await supabase.from('conversations').update({ updated_at: new Date().toISOString() }).eq('id', convId);

    setSending(true);
    try {
      const { data: sessionData } = await supabase.auth.getSession();
      const accessToken = sessionData.session?.access_token;
      if (!accessToken) throw new Error('Your session has expired. Please sign in again.');

      const apiUrl = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/chat`;
      const res = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${accessToken}`,
          apikey: import.meta.env.VITE_SUPABASE_ANON_KEY,
        },
        body: JSON.stringify({
          characterName: character.name,
          personality: character.personality,
          scenario: character.scenario,
          greeting: character.greeting,
          history: messages.map((m) => ({ role: m.role, content: m.content })),
          message: content,
        }),
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `Chat failed (${res.status})`);
      }
      const json = await res.json();
      if (!json.reply) throw new Error('No reply received');

      const { data: aiMsg } = await supabase
        .from('messages')
        .insert({ conversation_id: convId, role: 'assistant', content: json.reply })
        .select()
        .single();
      if (aiMsg) {
        setMessages((m) => [...m, aiMsg as Message]);
        if (voiceEnabled) {
          speak(json.reply, voiceSettings);
          setSpeakingId((aiMsg as Message).id);
        }
      }
    } catch (err) {
      const errMsg = (err as Error).message;
      const { data: aiMsg } = await supabase
        .from('messages')
        .insert({ conversation_id: convId, role: 'assistant', content: `*(Something went wrong: ${errMsg}. Please try again.)*` })
        .select()
        .single();
      if (aiMsg) setMessages((m) => [...m, aiMsg as Message]);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const replayVoice = (msg: Message) => {
    if (speakingId === msg.id) {
      stopSpeaking();
      setSpeakingId(null);
    } else {
      speak(msg.content, voiceSettings);
      setSpeakingId(msg.id);
    }
  };

  const saveMemory = async (msg: Message) => {
    if (!user || !character) return;
    await supabase.from('memories').insert({
      user_id: user.id,
      character_id: character.id,
      content: msg.content,
    });
    // visual feedback
    setMessages((m) => m.map((mm) => mm.id === msg.id ? mm : mm));
  };

  const clearConversation = async () => {
    if (!convId) return;
    if (!confirm('Clear all messages in this conversation? This cannot be undone.')) return;
    await supabase.from('messages').delete().eq('conversation_id', convId);
    setMessages([]);
  };

  if (loading || !character) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-rose-400" />
      </div>
    );
  }

  const color = getColor(character.avatar_color);

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] lg:h-[calc(100vh-7rem)]">
      {/* Chat header */}
      <div className="card p-4 mb-4 flex items-center gap-3 shrink-0">
        <button onClick={() => navigate({ name: 'home' })} className="btn-ghost p-2">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className={`w-11 h-11 rounded-xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-xl shadow-lg`}>
          {character.avatar_emoji}
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="font-semibold text-white truncate">{character.name}</h2>
          <p className="text-xs text-zinc-500 truncate">{character.tagline}</p>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              if (voiceEnabled) {
                stopSpeaking();
                setVoiceEnabled(false);
                setSpeakingId(null);
              } else {
                setVoiceEnabled(true);
              }
            }}
            className={`w-9 h-9 rounded-lg flex items-center justify-center transition-all ${voiceEnabled ? 'text-rose-400 bg-rose-500/10' : 'text-zinc-500 bg-white/5'}`}
            title={voiceEnabled ? 'Voice on' : 'Voice off'}
          >
            {voiceEnabled ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
          </button>
          <button onClick={clearConversation} className="w-9 h-9 rounded-lg flex items-center justify-center text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10 transition-all" title="Clear conversation">
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-4 pr-1 -mr-1">
        {messages.length === 0 && !sending && (
          <div className="text-center py-12">
            <div className={`inline-flex w-16 h-16 rounded-2xl bg-gradient-to-br ${color.from} ${color.to} items-center justify-center text-3xl mb-4 shadow-lg animate-float`}>
              {character.avatar_emoji}
            </div>
            <p className="text-zinc-400">Say hello to {character.name}!</p>
          </div>
        )}

        {messages.map((msg) => {
          const isUser = msg.role === 'user';
          return (
            <div
              key={msg.id}
              className={`flex gap-3 animate-fade-in-up ${isUser ? 'flex-row-reverse' : ''}`}
            >
              <div className={`shrink-0 w-9 h-9 rounded-xl flex items-center justify-center text-base shadow-md ${
                isUser ? 'bg-white/10' : `bg-gradient-to-br ${color.from} ${color.to}`
              }`}>
                {isUser ? '🧑' : character.avatar_emoji}
              </div>
              <div className={`group max-w-[75%] ${isUser ? 'items-end' : ''}`}>
                <div className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  isUser
                    ? 'bg-gradient-to-br from-rose-500 to-pink-600 text-white rounded-tr-md'
                    : 'glass text-zinc-100 rounded-tl-md'
                }`}>
                  {msg.content}
                </div>
                {!isUser && (
                  <div className="flex items-center gap-1 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => replayVoice(msg)}
                      className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-all ${
                        speakingId === msg.id ? 'text-rose-400 bg-rose-500/10' : 'text-zinc-500 hover:text-white hover:bg-white/5'
                      }`}
                    >
                      {speakingId === msg.id ? <Square className="w-3 h-3" /> : <Volume2 className="w-3 h-3" />}
                      {speakingId === msg.id ? 'Stop' : 'Listen'}
                    </button>
                    <button
                      onClick={() => saveMemory(msg)}
                      className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-zinc-500 hover:text-amber-400 hover:bg-amber-500/10 transition-all"
                    >
                      <Bookmark className="w-3 h-3" /> Save
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {sending && (
          <div className="flex gap-3 animate-fade-in">
            <div className={`shrink-0 w-9 h-9 rounded-xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-base shadow-md`}>
              {character.avatar_emoji}
            </div>
            <div className="glass rounded-2xl rounded-tl-md px-4 py-3.5 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-rose-400 animate-pulse" />
              <span className="w-2 h-2 rounded-full bg-rose-400 animate-pulse" style={{ animationDelay: '0.2s' }} />
              <span className="w-2 h-2 rounded-full bg-rose-400 animate-pulse" style={{ animationDelay: '0.4s' }} />
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="mt-4 shrink-0">
        <div className="card p-2 flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
            }}
            onKeyDown={handleKeyDown}
            placeholder={`Message ${character.name}...`}
            rows={1}
            className="flex-1 bg-transparent px-3 py-2.5 text-sm text-white placeholder:text-zinc-500 focus:outline-none resize-none max-h-[120px]"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || sending}
            className="w-10 h-10 rounded-xl bg-gradient-to-br from-rose-500 to-pink-600 flex items-center justify-center text-white shadow-lg shadow-rose-500/20 disabled:opacity-30 disabled:pointer-events-none hover:from-rose-400 hover:to-pink-500 transition-all active:scale-95"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
        <p className="text-xs text-zinc-600 text-center mt-2">
          {voiceEnabled ? '🔊 Voice on — responses will be spoken aloud' : '🔇 Voice off'} · Press Enter to send
        </p>
      </div>
    </div>
  );
}
