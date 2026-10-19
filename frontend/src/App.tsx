import { Navigate, Route, Routes } from 'react-router-dom';

import { AuthProvider } from './auth/AuthContext';
import { ConsoleApp } from './console/ConsoleApp';
import { ParentApp } from './parent/ParentApp';
import { NotFound } from './public/NotFound';
import { PayComplete } from './public/PayComplete';
import { Signup } from './public/Signup';
import { VerifyReceipt } from './public/VerifyReceipt';

/**
 * Three areas in one app:
 *   /console  school staff (and platform admins)
 *   /parent   mobile-first parent portal
 *   /verify   public receipt verification (no sign-in)
 *   /signup   a school signs itself up
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
      <Route path="/parent/*" element={<ParentApp />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/pay/complete" element={<PayComplete />} />
      <Route path="/verify/:code" element={<VerifyReceipt />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
