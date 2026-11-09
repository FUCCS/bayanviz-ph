export const API = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
export type Chart = {id:string; chapter:string; topic:string; function:string; objective:string; needs:string}
export type Location = {id:string;name:string;province:string;region:string;latitude:number;longitude:number}
export type Catalog = {locations:Location[];charts:Chart[];variables:string[];upload_limit_mb:number;upload_limit_rows:number;upload_limit_columns:number;uploads_per_session:number;session_idle_seconds:number}
const weatherDaily:Record<string,string> = {temperature_mean_c:'temperature_2m_mean',temperature_min_c:'temperature_2m_min',temperature_max_c:'temperature_2m_max',precipitation_mm:'precipitation_sum',wind_speed_max_kmh:'wind_speed_10m_max',sunshine_duration_hours:'sunshine_duration',wind_gust_max_kmh:'wind_gusts_10m_max',precipitation_hours:'precipitation_hours',wind_direction_dominant_deg:'wind_direction_10m_dominant',shortwave_radiation_mj_m2:'shortwave_radiation_sum'}
const weatherHourly:Record<string,string> = {relative_humidity_mean_pct:'relative_humidity_2m',apparent_temperature_mean_c:'apparent_temperature',cloud_cover_mean_pct:'cloud_cover',dew_point_mean_c:'dew_point_2m'}

export async function fetchBrowserWeather(session:string, locations:Location[], selected:string[], start:string, end:string, variables:string[]) {
  const daily=variables.filter(name=>weatherDaily[name]).map(name=>weatherDaily[name])
  const hourly=variables.filter(name=>weatherHourly[name]).map(name=>weatherHourly[name])
  if(!daily.length&&!hourly.length)throw new Error('Select supported weather variables')
  const chunks:{location_id:string;start_date:string;end_date:string;payload:Record<string,unknown>}[]=[]
  for(const key of selected){
    const point=locations.find(location=>location.id===key)
    if(!point)throw new Error(`Unknown location: ${key}`)
    for(let first=Date.parse(start+'T00:00:00Z');first<=Date.parse(end+'T00:00:00Z');first+=365*86400000){
      const last=Math.min(Date.parse(end+'T00:00:00Z'),first+364*86400000)
      const from=new Date(first).toISOString().slice(0,10),to=new Date(last).toISOString().slice(0,10)
      const params=new URLSearchParams({latitude:String(point.latitude),longitude:String(point.longitude),start_date:from,end_date:to,timezone:'UTC',models:'era5'})
      if(daily.length)params.set('daily',daily.join(','))
      if(hourly.length)params.set('hourly',hourly.join(','))
      const response=await fetch('https://archive-api.open-meteo.com/v1/archive?'+params)
      if(!response.ok){const detail=await response.json().catch(()=>({}));throw new Error(`${key}: Open-Meteo HTTP ${response.status}${detail.reason?' — '+detail.reason:''}`)}
      chunks.push({location_id:key,start_date:from,end_date:to,payload:await response.json()})
    }
  }
  return request('/api/fetch/weather/browser',session,{method:'POST',body:JSON.stringify({locations:selected,start_date:start,end_date:end,variables,chunks})})
}
export type DatasetInfo = {min_date:string|null;max_date:string|null;records:number;locations:string[];available_locations:string[];selected_observations:number;selected_valid_temperature:number;selected_valid_rainfall:number;source:string;variables:string[];compatibility?:Record<string,{available:boolean;missing_fields:string[];reason:string}>;schema_version?:string;retrieved_at:string|null;model:string[]}
export type Summary = {mean_temperature_c:number|null;maximum_temperature_c:number|null;minimum_temperature_c:number|null;total_precipitation_mm:number|null;average_monthly_precipitation_mm:number|null;rainy_day_frequency_pct:number|null;maximum_wind_kmh:number|null;valid_observations:number;valid_temperature_pct:number;valid_rainfall_pct:number;loaded_records:number;selected_period:string[];observation_period:string[];locations:number;missing_temperature:number;missing_precipitation:number;incomplete_months:number;incomplete_years:number;rainy_threshold_mm:number;source:string;model:string[];retrieved_at:string|null;precipitation_note:string;duplicate_records_retained:number;temperature_range_c:number|null;monthly_temperature_variability_c:number|null;monthly_rainfall_variability_mm:number|null;seasonal_quarter_averages:{calendar_quarter:number;mean_temperature_c:number|null;mean_daily_rain_mm:number|null;valid_temperature_days:number;valid_rain_days:number}[];year_over_year_temperature:{location_id:string;year:number;temperature_change_c:number|null}[];temperature_anomaly_c:number|null;anomaly_reference_period:string[];anomaly_method:string}
export type Result = {analysis_id:string;chart_id:string;renderer:string;figure:{type:'png'|'plotly'|'html';content:any};supporting_data:Record<string,unknown>[];supporting_columns:string[];supporting_count:number;computed_facts:{label:string;shows:string;findings:string;evidence:string;relevance:string;limitations:string[]};statistics?:{pearson_r?:number|null;paired_observations?:number};python_source:{visualization:string;preparation:string;topic:{chapter:string;topic:string;library_function:string;objective:string};highlights:string[]};sources:string[];limitations:string[];units:string;filters:Record<string,unknown>;sample_size:number;dataset_version:string;filter_hash:string}
export async function request(path:string, session:string, options:RequestInit={}) {
  const headers = new Headers(options.headers)
  if (session) headers.set('X-Session-Id', session)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type','application/json')
  const response = await fetch(API + path, {...options,headers})
  if (!response.ok) {
    const body = await response.json().catch(()=>({detail:response.statusText}))
    const detail = body.detail || body
    if (typeof detail === 'object' && detail && 'message' in detail) {
      const errors = Array.isArray(detail.errors) ? detail.errors.join('; ') : ''
      throw new Error(`${detail.message}${errors ? ': ' + errors : ''}`)
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return response.json()
}
export async function download(path:string, session:string, filename:string, options:RequestInit={}) {
  const headers = new Headers(options.headers)
  headers.set('X-Session-Id',session)
  if(options.body) headers.set('Content-Type','application/json')
  const response = await fetch(API + path,{...options,headers})
  if(!response.ok) throw new Error('Download failed')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a'); link.href=url; link.download=filename; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000)
}
