const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, { credentials: 'include', ...options });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === 'object' ? payload.detail?.message || payload.detail || 'Request failed' : payload;
    throw new Error(message);
  }
  return payload;
}

export async function uploadMedia(file) {
  const form = new FormData();
  form.append('upload', file);
  return apiFetch('/api/media/upload', { method: 'POST', body: form });
}

export const apiUrl = API_URL;
