export type Character = {
  id: string;
  user_id: string;
  name: string;
  tagline: string;
  personality: string;
  scenario: string;
  greeting: string;
  description: string;
  avatar_emoji: string;
  avatar_color: string;
  voice_name: string;
  voice_lang: string;
  voice_pitch: number;
  voice_rate: number;
  voice_warmth: number;
  ambient_mood: string;
  is_public: boolean;
  is_template: boolean;
  category: string;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type Conversation = {
  id: string;
  user_id: string;
  character_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  character?: Character;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
};

export type Memory = {
  id: string;
  user_id: string;
  character_id: string | null;
  content: string;
  created_at: string;
  character?: Character;
};

export type Profile = {
  id: string;
  display_name: string;
  avatar_emoji: string;
  bio: string;
  created_at: string;
  updated_at: string;
};

export type Favorite = {
  id: string;
  user_id: string;
  character_id: string;
  created_at: string;
};
