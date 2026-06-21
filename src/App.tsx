import React, {useEffect, useState} from 'react';
import ExplorePage from './pages/index';

function useColorMode() {
  const [dark, setDark] = useState(() => {
    if (typeof window === 'undefined') return true;
    const stored = localStorage.getItem('gk:theme');
    if (stored) return stored === 'dark';
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    localStorage.setItem('gk:theme', dark ? 'dark' : 'light');
  }, [dark]);

  return {dark, toggle: () => setDark((d) => !d)};
}

export default function App() {
  const {dark, toggle} = useColorMode();

  return (
    <>
      <nav className="gk-navbar">
        <div className="gk-navbar__left">
          <span className="gk-navbar__logo">G</span>
          <span className="gk-navbar__title">Gurukul</span>
        </div>
        <div className="gk-navbar__right">
          <button
            type="button"
            className="gk-navbar__theme-btn"
            onClick={toggle}
            title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {dark ? '☀' : '☾'}
          </button>
        </div>
      </nav>
      <ExplorePage />
    </>
  );
}
