import {useEffect, useRef} from 'react'
import * as L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type {Result} from './lib'

const number = (value: unknown) => value === null || value === undefined || value === '' ? NaN : typeof value === 'number' ? value : Number(value)

function popup(row: Record<string, unknown>, solar: boolean) {
  const node = document.createElement('div')
  const heading = document.createElement('strong')
  heading.textContent = String(row.location_name || row.location_id || 'Location')
  node.append(heading)
  const fields = solar
    ? [['Solar', row.solar_mean, 'kWh/m²/day'], ['Mean temperature', row.temperature_mean_c, '°C'], ['Mean daily rain', row.precipitation_mean_mm_day, 'mm/day'], ['Paired days', row.valid_days, '']]
    : [['Selected ' + String(row.selected_variable || 'measure'), row.selected_measure_value, String(row.selected_measure_unit || '')], ['Mean temperature', row.temperature_mean_c, '°C'], ['Mean daily rain', row.average_daily_rain_mm, 'mm/day'], ['Valid selected days', row.selected_valid_days, ''], ['Missing selected days', row.selected_missing_days, ''], ['Coverage', row.selected_coverage_pct, '%'], ['Difference from loaded-point mean', row.difference_from_loaded_mean, String(row.selected_measure_unit || '')]]
  if (!solar) {
    const detail = document.createElement('div')
    detail.textContent = `${String(row.province || '')} · ${String(row.region || '')} | ${String(row.period_start || '')} to ${String(row.period_end || '')} | ${String(row.model || 'uploaded source')}`
    node.append(detail)
  }
  for (const [label, value, unit] of fields) {
    const line = document.createElement('div')
    const valueNumber = number(value)
    line.textContent = `${label}: ${Number.isFinite(valueNumber) ? valueNumber.toFixed(unit ? 1 : 0) : 'unavailable'} ${unit}`
    node.append(line)
  }
  return node
}

export default function MapView({result, compact}: {result: Result; compact: boolean}) {
  const element = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map|null>(null)
  useEffect(() => {
    if (!element.current) return
    const solar = result.chart_id === 'solar_map'
    const points = result.supporting_data.filter(row => Number.isFinite(number(row.latitude)) && Number.isFinite(number(row.longitude)))
    const map = L.map(element.current, {zoomControl: true, scrollWheelZoom: false})
    mapRef.current = map
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map)
    const locations = L.layerGroup().addTo(map)
    const rainfall = L.layerGroup().addTo(map)
    const solarRadiation = L.layerGroup().addTo(map)
    for (const row of points) {
      const position: L.LatLngExpression = [number(row.latitude), number(row.longitude)]
      const temperature = number(solar ? row.temperature_mean_c : row.temperature_value_c)
      const color = !Number.isFinite(temperature) ? '#788c99' : temperature >= 28 ? '#c74640' : temperature >= 26 ? '#e49a26' : '#277cac'
      L.circleMarker(position, {radius: 9, color, fillColor: color, fillOpacity: 0.85, weight: 2})
        .bindTooltip(String(row.location_name || row.location_id))
        .bindPopup(popup(row, solar))
        .addTo(locations)
      const rain = number(solar ? row.precipitation_mean_mm_day : row.average_daily_rain_mm)
      if (Number.isFinite(rain)) L.circleMarker(position, {radius: Math.min(22, 5 + 2.5 * Math.sqrt(Math.max(0, rain))), color: '#0d6b9a', fillOpacity: 0.25, weight: 1}).bindPopup(popup(row, solar)).addTo(rainfall)
      if (solar && Number.isFinite(number(row.solar_mean))) L.circleMarker(position, {radius: Math.min(22, 4 + 2 * number(row.solar_mean)), color: '#e49a26', fillOpacity: 0.35, weight: 1}).bindPopup(popup(row, true)).addTo(solarRadiation)
    }
    const layers: Record<string, L.Layer> = {'Location temperature': locations, 'Rainfall circles': rainfall}
    if (solar) layers['Solar radiation circles'] = solarRadiation
    L.control.layers(undefined, layers, {collapsed: false}).addTo(map)
    if (points.length > 1) map.fitBounds(L.latLngBounds(points.map(row => [number(row.latitude), number(row.longitude)] as L.LatLngTuple)).pad(0.3))
    else if (points.length === 1) map.setView([number(points[0].latitude), number(points[0].longitude)], 9)
    else map.setView([9.3, 123.3], 7)
    window.setTimeout(() => map.invalidateSize(), 0)
    return () => { mapRef.current = null; map.remove() }
  }, [result])
  return <div className="map-shell"><div className="map-presets"><button onClick={()=>mapRef.current?.setView([12.5, 122.5], 6)}>Nationwide view</button><button onClick={()=>mapRef.current?.setView([9.3068, 123.3054], 9)}>Negros Oriental focus</button></div><div className={compact ? 'map-frame compact' : 'map-frame'} ref={element} aria-label="Interactive map of selected climate locations"/></div>
}
