import { Navigate, Route, Routes } from 'react-router-dom';

import { AuthProvider } from './auth/AuthContext';
import { ConsoleApp } from './console/ConsoleApp';
import { ParentHome } from './parent/ParentHome';
import { NotFound } from './public/NotFound';
import { PayComplete } from './public/PayComplete';

/**
 * Three areas in one app:
 *   /console  school staff (and platform admins)
 *   /parent   mobile-first parent portal
 *   /verify   public receipt verification (no sign-in)
 */
export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/console" replace />} />
      <Route
        path="/console/*"
        element={
          <AuthProvider>
            <ConsoleApp />
          </AuthProvider>
        }
      />
      <Route path="/parent/*" element={<ParentHome />} />
      <Route path="/pay/complete" element={<PayComplete />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
