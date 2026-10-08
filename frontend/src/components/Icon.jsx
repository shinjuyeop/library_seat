const paths = {
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
  seat: <><path d="M6 12V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v7M5 20v-4m14 4v-4M4 11v5h16v-5M4 13h16" /></>,
  settings: <><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="9" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="9" cy="18" r="2" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  refresh: <><path d="M20 4v6h-6M4 20v-6h6" /><path d="M5 9a7 7 0 0 1 12-4l3 5M4 14l3 5a7 7 0 0 0 12-4" /></>,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  check: <path d="m5 12 4 4L19 6" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  list: <><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" /></>,
};

export default function Icon({ name, ...props }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
