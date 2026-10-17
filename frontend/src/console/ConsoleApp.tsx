import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';

import { useAuth, type Capability } from '../auth/AuthContext';
import { ROLE_LABELS } from '../lib/format';
import { Loading } from '../lib/ui';
import { Account } from './Account';
import { AuditLog } from './AuditLog';
import { Login } from './auth/Login';
import { ForgotPassword, SetPassword } from './auth/PasswordPages';
import { Dashboard } from './Dashboard';
import { PlatformSchools } from './platform/Schools';
import { SchoolProfile } from './SchoolProfile';
import { Calendar } from './setup/Calendar';
import { Classes } from './setup/Classes';
import { ImportStudents } from './students/ImportStudents';
import { StudentDetail } from './students/StudentDetail';
import { Students } from './students/Students';
import { StaffPage } from './Staff';

interface NavItem {
  to: string;
  label: string;
  capability: Capability;
}

const SCHOOL_NAV: NavItem[] = [
  { to: '/console', label: 'Overview', capability: 'school.view' },
  { to: '/console/students', label: 'Students', capability: 'students.view' },
  { to: '/console/classes', label: 'Classes', capability: 'school.view' },
  { to: '/console/calendar', label: 'Calendar', capability: 'school.view' },
  { to: '/console/staff', label: 'Staff', capability: 'staff.view' },
  { to: '/console/audit', label: 'Activity log', capability: 'audit.view' },
  { to: '/console/school', label: 'School profile', capability: 'school.view' },
];

const PLATFORM_NAV: NavItem[] = [
  { to: '/console/platform/schools', label: 'Schools', capability: 'platform.manage' },
  { to: '/console/platform/audit', label: 'Activity log', capability: 'platform.manage' },
];

export function ConsoleApp() {
  return (
    <Routes>
      <Route path="login" element={<Login />} />
      <Route path="forgot-password" element={<ForgotPassword />} />
      <Route path="set-password" element={<SetPassword />} />
      <Route element={<RequireSignIn />}>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="account" element={<Account />} />
          <Route path="students" element={<Guard capability="students.view"><Students /></Guard>} />
          <Route path="students/import" element={<Guard capability="students.manage"><ImportStudents /></Guard>} />
          <Route path="students/:studentId" element={<Guard capability="students.view"><StudentDetail /></Guard>} />
          <Route path="classes" element={<Guard capability="school.view"><Classes /></Guard>} />
          <Route path="calendar" element={<Guard capability="school.view"><Calendar /></Guard>} />
          <Route path="staff" element={<Guard capability="staff.view"><StaffPage /></Guard>} />
          <Route path="audit" element={<Guard capability="audit.view"><AuditLog /></Guard>} />
          <Route path="school" element={<Guard capability="school.view"><SchoolProfile /></Guard>} />
          <Route path="platform/schools" element={<Guard capability="platform.manage"><PlatformSchools /></Guard>} />
          <Route path="platform/audit" element={<Guard capability="platform.manage"><AuditLog platform /></Guard>} />
          <Route path="*" element={<Navigate to="/console" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}

function RequireSignIn() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/console/login" replace state={{ from: location.pathname }} />;
  // Roles that must use two-factor sign-in can only reach their account page until they enrol
  if (user.mfa_enrolment_required && location.pathname !== '/console/account') {
    return <Navigate to="/console/account" replace />;
  }
  return <Outlet />;
}

function Guard({ capability, children }: { capability: Capability; children: JSX.Element }) {
  const { can } = useAuth();
  return can(capability) ? children : <Navigate to="/console" replace />;
}

function Home() {
  const { can } = useAuth();
  return can('platform.manage') ? <Navigate to="/console/platform/schools" replace /> : <Dashboard />;
}

function Layout() {
  const { user, can, logout } = useAuth();
  if (!user) return null;
  const nav = [...SCHOOL_NAV, ...PLATFORM_NAV].filter((item) => can(item.capability));
  return (
    <div className="console">
      <header className="topbar">
        <div className="brand">
          SchoolPay
          {user.school && <span className="school-name">{user.school.name}</span>}
        </div>
        <nav aria-label="Main">
          {!user.mfa_enrolment_required &&
            nav.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === '/console'}>
                {item.label}
              </NavLink>
            ))}
        </nav>
        <div className="user-menu">
          <NavLink to="/console/account" title="Your account">
            {user.full_name} <span className="muted small">({ROLE_LABELS[user.role] ?? user.role})</span>
          </NavLink>
          <button className="secondary small" onClick={() => void logout()}>
            Sign out
          </button>
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </div>
  );
}
