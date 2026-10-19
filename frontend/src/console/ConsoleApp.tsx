import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';

import { useAuth, type Capability } from '../auth/AuthContext';
import { BRAND } from '../lib/brand';
import { ROLE_LABELS } from '../lib/format';
import { Loading } from '../lib/ui';
import { Account } from './Account';
import { AuditLog } from './AuditLog';
import { Login } from './auth/Login';
import { ForgotPassword, SetPassword } from './auth/PasswordPages';
import { Billing } from './billing/Billing';
import { InvoiceDetail } from './billing/InvoiceDetail';
import { Messages } from './messages/Messages';
import { Reports } from './reports/Reports';
import { Dashboard } from './Dashboard';
import { FeeSetup } from './fees/FeeSetup';
import { Payments } from './payments/Payments';
import { PaymentSettings } from './payments/PaymentSettings';
import { PlatformBilling } from './platform/Billing';
import { PlatformLookup } from './platform/Lookup';
import { PlatformOverview } from './platform/Overview';
import { PlatformPlans } from './platform/Plans';
import { PlatformSchoolDetail } from './platform/SchoolDetail';
import { PlatformSchools } from './platform/Schools';
import { SchoolProfile } from './SchoolProfile';
import { Calendar } from './setup/Calendar';
import { Classes } from './setup/Classes';
import { ImportStudents } from './students/ImportStudents';
import { StudentDetail } from './students/StudentDetail';
import { Students } from './students/Students';
import { StaffPage } from './Staff';
import { Subscription } from './Subscription';

interface NavItem {
  to: string;
  label: string;
  capability: Capability;
  setup?: boolean; // grouped under the Setup menu
}

const SCHOOL_NAV: NavItem[] = [
  { to: '/console', label: 'Overview', capability: 'school.view' },
  { to: '/console/students', label: 'Students', capability: 'students.view' },
  { to: '/console/billing', label: 'Billing', capability: 'fees.view' },
  { to: '/console/payments', label: 'Payments', capability: 'payments.view' },
  { to: '/console/reports', label: 'Reports', capability: 'reports.view' },
  { to: '/console/messages', label: 'Messages', capability: 'messaging.manage' },
  { to: '/console/fees', label: 'Fees', capability: 'fees.view', setup: true },
  { to: '/console/classes', label: 'Classes', capability: 'school.view', setup: true },
  { to: '/console/calendar', label: 'Calendar', capability: 'school.view', setup: true },
  { to: '/console/staff', label: 'Staff', capability: 'staff.view', setup: true },
  { to: '/console/school', label: 'School profile', capability: 'school.view', setup: true },
  { to: '/console/payment-settings', label: 'Payment settings', capability: 'payments.view', setup: true },
  { to: '/console/subscription', label: 'Subscription', capability: 'reports.view', setup: true },
  { to: '/console/audit', label: 'Activity log', capability: 'audit.view', setup: true },
];

const PLATFORM_NAV: NavItem[] = [
  { to: '/console/platform', label: 'Overview', capability: 'platform.manage' },
  { to: '/console/platform/schools', label: 'Schools', capability: 'platform.manage' },
  { to: '/console/platform/billing', label: 'Billing', capability: 'platform.manage' },
  { to: '/console/platform/plans', label: 'Plans', capability: 'platform.manage' },
  { to: '/console/platform/lookup', label: 'Support lookup', capability: 'platform.manage' },
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
          <Route path="billing" element={<Guard capability="fees.view"><Billing /></Guard>} />
          <Route path="invoices/:invoiceId" element={<Guard capability="fees.view"><InvoiceDetail /></Guard>} />
          <Route path="payments" element={<Guard capability="payments.view"><Payments /></Guard>} />
          <Route path="reports" element={<Guard capability="reports.view"><Reports /></Guard>} />
          <Route path="messages" element={<Guard capability="messaging.manage"><Messages /></Guard>} />
          <Route path="payment-settings" element={<Guard capability="payments.view"><PaymentSettings /></Guard>} />
          <Route path="fees" element={<Guard capability="fees.view"><FeeSetup /></Guard>} />
          <Route path="classes" element={<Guard capability="school.view"><Classes /></Guard>} />
          <Route path="calendar" element={<Guard capability="school.view"><Calendar /></Guard>} />
          <Route path="staff" element={<Guard capability="staff.view"><StaffPage /></Guard>} />
          <Route path="audit" element={<Guard capability="audit.view"><AuditLog /></Guard>} />
          <Route path="school" element={<Guard capability="school.view"><SchoolProfile /></Guard>} />
          <Route path="subscription" element={<Guard capability="reports.view"><Subscription /></Guard>} />
          <Route path="platform" element={<Guard capability="platform.manage"><PlatformOverview /></Guard>} />
          <Route path="platform/schools" element={<Guard capability="platform.manage"><PlatformSchools /></Guard>} />
          <Route path="platform/schools/:schoolId" element={<Guard capability="platform.manage"><PlatformSchoolDetail /></Guard>} />
          <Route path="platform/billing" element={<Guard capability="platform.manage"><PlatformBilling /></Guard>} />
          <Route path="platform/plans" element={<Guard capability="platform.manage"><PlatformPlans /></Guard>} />
          <Route path="platform/lookup" element={<Guard capability="platform.manage"><PlatformLookup /></Guard>} />
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
  return can('platform.manage') ? <Navigate to="/console/platform" replace /> : <Dashboard />;
}

function Layout() {
  const { user, can, logout } = useAuth();
  const location = useLocation();
  if (!user) return null;
  const nav = [...SCHOOL_NAV, ...PLATFORM_NAV].filter((item) => can(item.capability));
  const setup = nav.filter((item) => item.setup);
  const inSetup = setup.some((item) => location.pathname.startsWith(item.to));
  return (
    <div className="console">
      <header className="topbar">
        <div className="brand">
          {BRAND}
          {user.school && <span className="school-name">{user.school.name}</span>}
        </div>
        <nav aria-label="Main">
          {!user.mfa_enrolment_required && (
            <>
              {nav
                .filter((item) => !item.setup)
                .map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.to === '/console' || item.to === '/console/platform'}>
                    {item.label}
                  </NavLink>
                ))}
              {setup.length > 0 && (
                <details className="nav-menu" key={location.pathname /* closes after navigating */}>
                  <summary className={inSetup ? 'active' : ''}>Setup</summary>
                  <div className="nav-menu-list">
                    {setup.map((item) => (
                      <NavLink key={item.to} to={item.to}>
                        {item.label}
                      </NavLink>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
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
