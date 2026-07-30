async function handle(res) {
  if (!res.ok) {
    let detail
    try {
      detail = (await res.json()).detail
    } catch {
      detail = res.statusText
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) return null
  return res.json()
}

export const get = (path) => fetch(path).then(handle)

export const post = (path, body) =>
  fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(handle)

export const put = (path, body) =>
  fetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(handle)

export const del = (path) => fetch(path, { method: 'DELETE' }).then(handle)

export const postForm = (path, formData) =>
  fetch(path, { method: 'POST', body: formData }).then(handle)
