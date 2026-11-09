import {afterEach, describe, expect, it, vi} from 'vitest'
import {fetchBrowserWeather, request} from './lib'

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

describe('browser weather fallback', () => {
  it('requests Open-Meteo then sends the response to Python for validation', async () => {
    const provider = {latitude:9.3068,longitude:123.3054,daily:{time:['2025-01-01'],temperature_2m_mean:[26]},daily_units:{temperature_2m_mean:'°C'}}
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(provider),{status:200})).mockResolvedValueOnce(new Response(JSON.stringify({records:1,status:'Browser API relay'}),{status:200}))
    vi.stubGlobal('fetch',fetchMock)
    const locations=[{id:'dumaguete',name:'Dumaguete',province:'Negros Oriental',region:'Negros Island Region',latitude:9.3068,longitude:123.3054}]
    await expect(fetchBrowserWeather('session-token',locations,['dumaguete'],'2025-01-01','2025-01-01',['temperature_mean_c'])).resolves.toMatchObject({records:1})
    expect(fetchMock.mock.calls[0][0]).toContain('daily=temperature_2m_mean')
    const [url,options]=fetchMock.mock.calls[1]
    expect(url).toContain('/api/fetch/weather/browser')
    expect(options.headers.get('X-Session-Id')).toBe('session-token')
    const body=JSON.parse(options.body)
    expect(body.chunks).toHaveLength(1)
    expect(body.chunks[0].payload).toEqual(provider)
  })
})
