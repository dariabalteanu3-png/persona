import { useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import { Sparkles, Mail, Lock, User, Loader2, Eye, EyeOff } from 'lucide-react';

export function AuthPage() {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<'signin' | 'signup'>('signup');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const result =
      mode === 'signin'
        ? await signIn(email.trim(), password)
        : await signUp(email.trim(), password, displayName.trim() || 'Friend');
    setLoading(false);
    if (result.error) {
      setError(result.error);
    }
  };

  return (
    <div className="min-h-screen bg-mesh bg-[#0a0a0f] flex items-center justify-center p-4 overflow-hidden relative">
      {/* Floating decorative orbs */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 -left-40 w-96 h-96 rounded-full bg-rose-500/10 blur-3xl animate-float" />
        <div className="absolute -bottom-40 -right-40 w-96 h-96 rounded-full bg-fuchsia-500/10 blur-3xl animate-float" style={{ animationDelay: '2s' }} />
        <div className="absolute top-1/2 left-1/3 w-72 h-72 rounded-full bg-violet-500/8 blur-3xl animate-float" style={{ animationDelay: '4s' }} />
      </div>

      <div className="relative w-full max-w-md animate-fade-in-up">
        {/* Logo / Hero */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-rose-500 to-pink-600 shadow-xl shadow-rose-500/30 mb-4 animate-float">
            <Sparkles className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-3xl font-bold text-gradient mb-2">Persona</h1>
          <p className="text-sm text-zinc-400">Bring characters to life. Chat with AI companions who have personality, voice, and a world of their own.</p>
        </div>

        {/* Card */}
        <div className="glass-strong rounded-3xl p-8 shadow-2xl">
          {/* Tabs */}
          <div className="flex gap-1 p-1 rounded-xl bg-white/[0.04] mb-6">
            <button
              onClick={() => { setMode('signup'); setError(null); }}
              className={`flex-1 rounded-lg py-2 text-sm font-semibold transition-all ${
                mode === 'signup' ? 'bg-gradient-to-br from-rose-500 to-pink-600 text-white shadow-lg shadow-rose-500/20' : 'text-zinc-400 hover:text-white'
              }`}
            >
              Create account
            </button>
            <button
              onClick={() => { setMode('signin'); setError(null); }}
              className={`flex-1 rounded-lg py-2 text-sm font-semibold transition-all ${
                mode === 'signin' ? 'bg-gradient-to-br from-rose-500 to-pink-600 text-white shadow-lg shadow-rose-500/20' : 'text-zinc-400 hover:text-white'
              }`}
            >
              Sign in
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === 'signup' && (
              <div className="animate-fade-in">
                <label className="label-text">Display name</label>
                <div className="relative">
                  <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
                  <input
                    type="text"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="What should we call you?"
                    className="input-field pl-11"
                  />
                </div>
              </div>
            )}

            <div>
              <label className="label-text">Email</label>
              <div className="relative">
                <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="input-field pl-11"
                />
              </div>
            </div>

            <div>
              <label className="label-text">Password</label>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
                <input
                  type={showPw ? 'text' : 'password'}
                  required
                  minLength={6}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 6 characters"
                  className="input-field pl-11 pr-11"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((v) => !v)}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 transition-colors"
                >
                  {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {error && (
              <div className="rounded-xl bg-rose-500/10 border border-rose-500/20 px-4 py-3 text-sm text-rose-300 animate-fade-in">
                {error}
              </div>
            )}

            <button type="submit" disabled={loading} className="btn-primary w-full py-3">
              {loading ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Please wait...</>
              ) : mode === 'signin' ? (
                'Sign in'
              ) : (
                <>Create my account</>
              )}
            </button>
          </form>

          <p className="text-center text-xs text-zinc-500 mt-6">
            {mode === 'signup' ? 'Already have an account?' : 'New to Persona?'}{' '}
            <button
              onClick={() => { setMode(mode === 'signup' ? 'signin' : 'signup'); setError(null); }}
              className="text-rose-400 hover:text-rose-300 font-semibold transition-colors"
            >
              {mode === 'signup' ? 'Sign in instead' : 'Create one free'}
            </button>
          </p>
        </div>

        <p className="text-center text-xs text-zinc-600 mt-6">
          Free and open-source. Your characters and conversations are saved privately.
        </p>
      </div>
    </div>
  );
}
