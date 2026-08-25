import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from './AuthContext';

// Real bug found live via Playwright: register()'s finally block never
// called setIsRegistering(false) - login()/verifyMfaCode() both do, this
// one didn't. A user whose registration attempt fails (or even succeeds,
// before the page navigates away) is left with a permanently disabled
// "Create account" button - the whole form becomes unusable without a
// full page reload, since isRegistering never returns to false.

function Probe() {
  const { register, isRegistering, registerError } = useAuth();
  return (
    <div>
      <span data-testid="is-registering">{String(isRegistering)}</span>
      <span data-testid="register-error">{registerError ?? ''}</span>
      <button
        onClick={() => {
          register({ username: 'u', password: 'p', tenantId: 't', role: 'ADMIN' }).catch(() => {});
        }}
      >
        Register
      </button>
    </div>
  );
}

describe('AuthContext register() loading-state lifecycle', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('resets isRegistering to false after a failed registration, not just a successful one', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Username already exists' }),
    } as Response);

    render(<AuthProvider><Probe /></AuthProvider>);

    act(() => {
      screen.getByRole('button', { name: 'Register' }).click();
    });

    await waitFor(() => expect(screen.getByTestId('is-registering').textContent).toBe('true'));
    await waitFor(() => expect(screen.getByTestId('register-error').textContent).toBe('Username already exists'));

    // The actual regression: without the fix, this stays "true" forever
    // and the real submit button in LoginScreen.tsx stays disabled.
    expect(screen.getByTestId('is-registering').textContent).toBe('false');
  });

  it('resets isRegistering to false after a successful registration too', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ username: 'u', tenant_id: 't', role: 'ADMIN' }),
    } as Response);

    render(<AuthProvider><Probe /></AuthProvider>);

    act(() => {
      screen.getByRole('button', { name: 'Register' }).click();
    });

    await waitFor(() => expect(screen.getByTestId('is-registering').textContent).toBe('false'));
    expect(screen.getByTestId('register-error').textContent).toBe('');
  });

  it('the form can be resubmitted after a failed attempt (the real user-facing symptom)', async () => {
    vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ detail: 'Server error' }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({ username: 'u', tenant_id: 't', role: 'ADMIN' }),
      } as Response);

    render(<AuthProvider><Probe /></AuthProvider>);
    const button = screen.getByRole('button', { name: 'Register' });

    act(() => button.click());
    await waitFor(() => expect(screen.getByTestId('register-error').textContent).toBe('Server error'));
    await waitFor(() => expect(screen.getByTestId('is-registering').textContent).toBe('false'));

    // Second click only succeeds (fetch is called again) if isRegistering
    // actually went back to false - a real submit button is `disabled`
    // while isRegistering is true, which is what silently broke this.
    act(() => button.click());
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId('register-error').textContent).toBe(''));
  });
});
