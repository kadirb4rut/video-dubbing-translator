// An empty VITE_API_URL intentionally means same-origin production routing
// through the CloudFront API behaviors. Undefined keeps local development on
// the standalone API port.
const API_URL = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? '' : 'http://localhost:8000');

export async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, { credentials: 'include', ...options });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === 'object' ? payload.detail?.message || payload.detail || 'Request failed' : payload;
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

export async function uploadMedia(file) {
  try {
    const presigned = await apiFetch('/api/media/presign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, content_type: file.type || 'application/octet-stream', size_bytes: file.size }),
    });
    const upload = await fetch(presigned.upload_url, {
      method: presigned.method || 'PUT',
      headers: presigned.headers || {},
      body: file,
    });
    if (!upload.ok) {
      const error = new Error(`Direct media upload failed (${upload.status})`);
      error.status = upload.status;
      throw error;
    }
    return apiFetch(`/api/media/${presigned.asset.id}/complete`, { method: 'POST' });
  } catch (error) {
    // LocalObjectStore intentionally has no presigned URL. Keep local development
    // usable while making S3 the normal browser path in production.
    if (error.status !== 409) throw error;
  }
  const form = new FormData();
  form.append('upload', file);
  return apiFetch('/api/media/upload', { method: 'POST', body: form });
}

export const apiUrl = API_URL;
