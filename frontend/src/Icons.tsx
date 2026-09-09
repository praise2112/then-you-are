const PATHS: Record<string, { d: string; solid?: boolean; circle?: boolean }> = {
  link: { d: "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5" },
  x: { d: "M4 3h4.5l5 6.8L19.4 3H22l-7.2 8.3L22.5 21H18l-5.3-7.2L6.2 21H3.6l7.7-8.9L4 3z", solid: true },
  play: { d: "M10 8.5l6 3.5-6 3.5z", solid: true, circle: true },
  share: { d: "M12 16V4M8.5 7.5L12 4l3.5 3.5M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5" },
  flag: { d: "M6 21V4M6 4h11l-2.5 4L17 12H6" },
  speech: { d: "M20 5H4a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h3v4l4.5-4H20a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1z" },
  bill: { d: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6" },
  quill: { d: "M4 20L20 4M20 4c-7 0-11 3-12.5 7.5L4 20l8.5-3.5C17 15 20 11 20 4z" },
};

export function Icon({ name }: { name: keyof typeof PATHS }) {
  const icon = PATHS[name];
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      {icon.circle && <circle cx="12" cy="12" r="9" />}
      <path d={icon.d} className={icon.solid ? "solid" : undefined} />
    </svg>
  );
}
