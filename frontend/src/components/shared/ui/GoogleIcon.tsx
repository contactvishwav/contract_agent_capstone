import React from 'react';

// Standard 4-color Google "G" mark, inline (no external asset/font
// request - fine under this app's strict CSP, which has no unnecessary
// img-src/font-src beyond 'self'/data:). Shared between LoginScreen and
// the invite-accept page's "Sign in with Google" buttons.
export const GoogleIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path fill="#FFC107" d="M43.6 20.5H42V20.4H24v7.2h11.3c-1.6 4.5-5.9 7.7-11.3 7.7-6.9 0-12.4-5.6-12.4-12.4S17.1 10.5 24 10.5c3.2 0 6 1.2 8.2 3.1l5.4-5.4C34.5 5.1 29.5 3 24 3 12.4 3 3 12.4 3 24s9.4 21 21 21 21-9.4 21-21c0-1.2-.1-2.4-.4-3.5z" />
    <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.6 15.1 18.9 12.5 24 12.5c3.2 0 6 1.2 8.2 3.1l5.4-5.4C34.5 6.1 29.5 4 24 4c-7.4 0-13.8 4.2-17 10.4z" />
    <path fill="#4CAF50" d="M24 44c5.4 0 10.3-2.1 14-5.4l-6.5-5.5c-2 1.4-4.6 2.4-7.5 2.4-5.3 0-9.7-3.4-11.3-8.1l-6.5 5C9.9 39.6 16.4 44 24 44z" />
    <path fill="#1976D2" d="M43.6 20.5H42V20.4H24v7.2h11.3c-.8 2.2-2.2 4.1-4.1 5.4l6.5 5.5C41.5 35.5 44 30.2 44 24c0-1.2-.1-2.4-.4-3.5z" />
  </svg>
);
