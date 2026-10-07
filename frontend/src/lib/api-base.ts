const configuredApiBase = import.meta.env.VITE_API_BASE;
const developmentApiBase = typeof window === 'undefined'
    ? 'http://localhost:8005'
    : `${window.location.protocol}//${window.location.hostname}:8005`;
export const API_BASE = configuredApiBase === undefined ? developmentApiBase : configuredApiBase;
export const ADMIN_API_TOKEN = import.meta.env.VITE_ADMIN_API_TOKEN || '';

export function withAdminAuth(init: RequestInit = {}): RequestInit {
    if (!ADMIN_API_TOKEN) {
        return init;
    }

    const headers = new Headers(init.headers || {});
    headers.set('Authorization', `Bearer ${ADMIN_API_TOKEN}`);

    return {
        ...init,
        headers,
    };
}
