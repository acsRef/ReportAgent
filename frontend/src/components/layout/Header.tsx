import { NavLink } from 'react-router-dom';
import './Header.css';

const navItems = [
  { to: '/', label: '报表查询', icon: '◈' },
  { to: '/dashboard', label: '看板编辑', icon: '◇' },
];

export default function Header() {
  return (
    <header className="header">
      <div className="header-inner">
        <NavLink to="/" className="header-logo">
          <div className="header-logo-mark">R</div>
          <span className="header-logo-text">ReportAgent</span>
        </NavLink>

        <nav className="header-nav">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `header-nav-item ${isActive ? 'active' : ''}`
              }
              end={item.to === '/'}
            >
              <span className="header-nav-icon">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="header-right">
          <div className="header-status">
            <span className="header-status-dot" />
            AI Ready
          </div>
        </div>
      </div>
    </header>
  );
}
