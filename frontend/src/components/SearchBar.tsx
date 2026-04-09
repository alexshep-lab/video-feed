type SearchBarProps = {
  value: string;
  onChange: (value: string) => void;
};

export default function SearchBar({ value, onChange }: SearchBarProps) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm uppercase tracking-[0.22em] text-white/55">Search</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Find by title or file name"
        className="w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-white outline-none transition focus:border-accent focus:bg-white/10"
      />
    </label>
  );
}
