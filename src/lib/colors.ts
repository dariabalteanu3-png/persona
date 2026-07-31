export type ColorPreset = {
  id: string;
  name: string;
  from: string;
  to: string;
  ring: string;
  text: string;
};

export const colorPresets: ColorPreset[] = [
  { id: 'rose', name: 'Rose', from: 'from-rose-500', to: 'to-pink-600', ring: 'ring-rose-500', text: 'text-rose-300' },
  { id: 'amber', name: 'Amber', from: 'from-amber-500', to: 'to-orange-600', ring: 'ring-amber-500', text: 'text-amber-300' },
  { id: 'emerald', name: 'Emerald', from: 'from-emerald-500', to: 'to-teal-600', ring: 'ring-emerald-500', text: 'text-emerald-300' },
  { id: 'sky', name: 'Sky', from: 'from-sky-500', to: 'to-blue-600', ring: 'ring-sky-500', text: 'text-sky-300' },
  { id: 'violet', name: 'Violet', from: 'from-violet-500', to: 'to-purple-600', ring: 'ring-violet-500', text: 'text-violet-300' },
  { id: 'fuchsia', name: 'Fuchsia', from: 'from-fuchsia-500', to: 'to-pink-600', ring: 'ring-fuchsia-500', text: 'text-fuchsia-300' },
  { id: 'cyan', name: 'Cyan', from: 'from-cyan-500', to: 'to-sky-600', ring: 'ring-cyan-500', text: 'text-cyan-300' },
  { id: 'lime', name: 'Lime', from: 'from-lime-500', to: 'to-green-600', ring: 'ring-lime-500', text: 'text-lime-300' },
  { id: 'indigo', name: 'Indigo', from: 'from-indigo-500', to: 'to-blue-700', ring: 'ring-indigo-500', text: 'text-indigo-300' },
  { id: 'slate', name: 'Slate', from: 'from-slate-500', to: 'to-slate-700', ring: 'ring-slate-400', text: 'text-slate-300' },
];

export function getColor(id: string): ColorPreset {
  return colorPresets.find((c) => c.id === id) ?? colorPresets[0];
}
