import { useEffect, useRef, useState } from 'react';
import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import Home from './pages/Home';
import Calls from './pages/Calls';

function Nav() {
  const [authNote, setAuthNote] = useState(false);
  const noteTimer = useRef(null);
  useEffect(() => () => clearTimeout(noteTimer.current), []);
  const comingSoon = () => {
    setAuthNote(true);
    clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => setAuthNote(false), 4000);
  };
  return (
    <nav className="shell-nav" aria-label="Primary">
      <div className="shell-nav__inner">
        <NavLink to="/" className="shell-nav__brand">
          The Negotiator
        </NavLink>
        <span className="shell-nav__dots" aria-hidden="true" />
        <div className="shell-nav__links">
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'active' : '')}>
            Home
          </NavLink>
          <NavLink to="/calls" className={({ isActive }) => (isActive ? 'active' : '')}>
            Calls
          </NavLink>
          <button type="button" className="shell-nav__auth" onClick={comingSoon}>
            Log in
          </button>
          <button type="button" className="shell-nav__auth shell-nav__auth--signup" onClick={comingSoon}>
            Sign up
          </button>
        </div>
      </div>
      {authNote && (
        <p className="shell-nav__note micro muted" role="status">
          Accounts arrive with live calling — coming soon.
        </p>
      )}
    </nav>
  );
}

export default function App() {
  return (
    <>
      <Nav />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/calls" element={<Calls />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
