/*
# Persona — AI Companion App Schema

## Overview
Creates the full database schema for Persona, an AI companion app where users
create characters with personalities, chat with them, save memories, and browse
public/template characters.

## New Tables

1. **profiles** — extends auth.users with display name, avatar emoji, and bio.
   - id (uuid, PK, references auth.users)
   - display_name (text)
   - avatar_emoji (text, default 🎭)
   - bio (text)
   - created_at, updated_at (timestamptz)

2. **characters** — AI personas created by users.
   - id (uuid, PK)
   - user_id (uuid, FK → auth.users, defaults to auth.uid())
   - name, tagline, personality, scenario, greeting, description (text)
   - avatar_emoji (text, default 🎭)
   - avatar_color (text, for gradient seed)
   - voice_name, voice_lang, voice_pitch, voice_rate, voice_warmth (voice settings)
   - ambient_mood (text, default ambient preset for this character)
   - is_public (boolean, default false)
   - is_template (boolean, default false)
   - category (text)
   - tags (text[])
   - created_at, updated_at

3. **conversations** — chat sessions between a user and a character.
   - id (uuid, PK)
   - user_id (uuid, FK → auth.users)
   - character_id (uuid, FK → characters, cascade delete)
   - title (text)
   - created_at, updated_at

4. **messages** — individual messages in a conversation.
   - id (uuid, PK)
   - conversation_id (uuid, FK → conversations, cascade delete)
   - role (text: 'user' | 'assistant')
   - content (text)
   - created_at

5. **memories** — user-saved memorable moments or facts.
   - id (uuid, PK)
   - user_id (uuid, FK → auth.users)
   - character_id (uuid, FK → characters, nullable, set null on delete)
   - content (text)
   - created_at

6. **favorites** — user bookmarks on public/template characters.
   - id (uuid, PK)
   - user_id (uuid, FK → auth.users)
   - character_id (uuid, FK → characters)
   - created_at
   - UNIQUE(user_id, character_id)

## Security (RLS)
- profiles: owner-only CRUD (auth.uid() = id).
- characters: owner full CRUD; all authenticated users can SELECT public or template characters.
- conversations: owner-only CRUD.
- messages: CRUD scoped through parent conversation ownership.
- memories: owner-only CRUD.
- favorites: owner-only CRUD.

## Notes
- Owner columns default to auth.uid() so client inserts that omit user_id succeed.
- Public characters are readable by any authenticated user (community/explore feature).
*/

-- Profiles
CREATE TABLE IF NOT EXISTS profiles (
  id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  display_name text NOT NULL DEFAULT '',
  avatar_emoji text NOT NULL DEFAULT '🎭',
  bio text DEFAULT '',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_own_profile" ON profiles;
CREATE POLICY "select_own_profile" ON profiles FOR SELECT
  TO authenticated USING (auth.uid() = id);
DROP POLICY IF EXISTS "insert_own_profile" ON profiles;
CREATE POLICY "insert_own_profile" ON profiles FOR INSERT
  TO authenticated WITH CHECK (auth.uid() = id);
DROP POLICY IF EXISTS "update_own_profile" ON profiles;
CREATE POLICY "update_own_profile" ON profiles FOR UPDATE
  TO authenticated USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
DROP POLICY IF EXISTS "delete_own_profile" ON profiles;
CREATE POLICY "delete_own_profile" ON profiles FOR DELETE
  TO authenticated USING (auth.uid() = id);

-- Characters
CREATE TABLE IF NOT EXISTS characters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  name text NOT NULL,
  tagline text DEFAULT '',
  personality text DEFAULT '',
  scenario text DEFAULT '',
  greeting text DEFAULT '',
  description text DEFAULT '',
  avatar_emoji text NOT NULL DEFAULT '🎭',
  avatar_color text DEFAULT 'rose',
  voice_name text DEFAULT '',
  voice_lang text DEFAULT 'en-US',
  voice_pitch float DEFAULT 1.0,
  voice_rate float DEFAULT 1.0,
  voice_warmth float DEFAULT 0.5,
  ambient_mood text DEFAULT 'none',
  is_public boolean DEFAULT false,
  is_template boolean DEFAULT false,
  category text DEFAULT 'personal',
  tags text[] DEFAULT '{}',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
ALTER TABLE characters ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_characters_user ON characters(user_id);
CREATE INDEX IF NOT EXISTS idx_characters_public ON characters(is_public) WHERE is_public = true;

DROP POLICY IF EXISTS "select_own_or_public_characters" ON characters;
CREATE POLICY "select_own_or_public_characters" ON characters FOR SELECT
  TO authenticated USING (auth.uid() = user_id OR is_public = true OR is_template = true);
DROP POLICY IF EXISTS "insert_own_characters" ON characters;
CREATE POLICY "insert_own_characters" ON characters FOR INSERT
  TO authenticated WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "update_own_characters" ON characters;
CREATE POLICY "update_own_characters" ON characters FOR UPDATE
  TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "delete_own_characters" ON characters;
CREATE POLICY "delete_own_characters" ON characters FOR DELETE
  TO authenticated USING (auth.uid() = user_id);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  character_id uuid NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  title text NOT NULL DEFAULT 'New conversation',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_character ON conversations(character_id);

DROP POLICY IF EXISTS "select_own_conversations" ON conversations;
CREATE POLICY "select_own_conversations" ON conversations FOR SELECT
  TO authenticated USING (auth.uid() = user_id);
DROP POLICY IF EXISTS "insert_own_conversations" ON conversations;
CREATE POLICY "insert_own_conversations" ON conversations FOR INSERT
  TO authenticated WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "update_own_conversations" ON conversations;
CREATE POLICY "update_own_conversations" ON conversations FOR UPDATE
  TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "delete_own_conversations" ON conversations;
CREATE POLICY "delete_own_conversations" ON conversations FOR DELETE
  TO authenticated USING (auth.uid() = user_id);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('user', 'assistant')),
  content text NOT NULL,
  created_at timestamptz DEFAULT now()
);
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);

DROP POLICY IF EXISTS "select_own_messages" ON messages;
CREATE POLICY "select_own_messages" ON messages FOR SELECT
  TO authenticated USING (
    EXISTS (SELECT 1 FROM conversations WHERE conversations.id = messages.conversation_id AND conversations.user_id = auth.uid())
  );
DROP POLICY IF EXISTS "insert_own_messages" ON messages;
CREATE POLICY "insert_own_messages" ON messages FOR INSERT
  TO authenticated WITH CHECK (
    EXISTS (SELECT 1 FROM conversations WHERE conversations.id = messages.conversation_id AND conversations.user_id = auth.uid())
  );
DROP POLICY IF EXISTS "update_own_messages" ON messages;
CREATE POLICY "update_own_messages" ON messages FOR UPDATE
  TO authenticated USING (
    EXISTS (SELECT 1 FROM conversations WHERE conversations.id = messages.conversation_id AND conversations.user_id = auth.uid())
  );
DROP POLICY IF EXISTS "delete_own_messages" ON messages;
CREATE POLICY "delete_own_messages" ON messages FOR DELETE
  TO authenticated USING (
    EXISTS (SELECT 1 FROM conversations WHERE conversations.id = messages.conversation_id AND conversations.user_id = auth.uid())
  );

-- Memories
CREATE TABLE IF NOT EXISTS memories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  character_id uuid REFERENCES characters(id) ON DELETE SET NULL,
  content text NOT NULL,
  created_at timestamptz DEFAULT now()
);
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);

DROP POLICY IF EXISTS "select_own_memories" ON memories;
CREATE POLICY "select_own_memories" ON memories FOR SELECT
  TO authenticated USING (auth.uid() = user_id);
DROP POLICY IF EXISTS "insert_own_memories" ON memories;
CREATE POLICY "insert_own_memories" ON memories FOR INSERT
  TO authenticated WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "update_own_memories" ON memories;
CREATE POLICY "update_own_memories" ON memories FOR UPDATE
  TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "delete_own_memories" ON memories;
CREATE POLICY "delete_own_memories" ON memories FOR DELETE
  TO authenticated USING (auth.uid() = user_id);

-- Favorites
CREATE TABLE IF NOT EXISTS favorites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  character_id uuid NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  created_at timestamptz DEFAULT now(),
  UNIQUE(user_id, character_id)
);
ALTER TABLE favorites ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);

DROP POLICY IF EXISTS "select_own_favorites" ON favorites;
CREATE POLICY "select_own_favorites" ON favorites FOR SELECT
  TO authenticated USING (auth.uid() = user_id);
DROP POLICY IF EXISTS "insert_own_favorites" ON favorites;
CREATE POLICY "insert_own_favorites" ON favorites FOR INSERT
  TO authenticated WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "delete_own_favorites" ON favorites;
CREATE POLICY "delete_own_favorites" ON favorites FOR DELETE
  TO authenticated USING (auth.uid() = user_id);

-- Auto-create profile on signup
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id, display_name)
  VALUES (NEW.id, COALESCE(NEW.raw_user_meta_data->>'display_name', ''))
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
