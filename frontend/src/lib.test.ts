import {afterEach, describe, expect, it, vi} from 'vitest'
import {request} from './lib'

afterEach(() => vi.unstubAllGlobals())

describe('API request handling', () => {
  it('keeps the anonymous session in the request header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ok: true}), {status: 200, headers: {'Content-Type': 'application/json'}}))
    vi.stubGlobal('fetch', fetchMock)
    await expect(request('/api/analysis', 'session-token', {method: 'POST', body: JSON.stringify({chart_id: 'bar'})})).resolves.toEqual({ok: true})
    const [, options] = fetchMock.mock.calls[0]
    expect(options.headers.get('X-Session-Id')).toBe('session-token')
    expect(options.headers.get('Content-Type')).toBe('application/json')
  })

  it('shows the backend error instead of silently substituting data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({detail: 'No valid observations after filtering'}), {status: 422, headers: {'Content-Type': 'application/json'}})))
    await expect(request('/api/analysis', 'session-token')).rejects.toThrow('No valid observations after filtering')
  })
})
