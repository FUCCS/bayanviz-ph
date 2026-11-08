export const API = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
export type Chart = {id:string; chapter:string; topic:string; function:string; objective:string; needs:string}
export type Location = {id:string;name:string;province:string;region:string;latitude:number;longitude:number}
export type Catalog = {locations:Location[];charts:Chart[];categories:string[];upload_limit_mb:number;upload_limit_rows:number;upload_limit_columns:number;uploads_per_session:number}
export type Result = {analysis_id:string;chart_id:string;renderer:string;figure:{type:'png'|'plotly'|'html';content:any};supporting_data:Record<string,unknown>[];supporting_columns:string[];supporting_count:number;computed_facts:{label:string;shows:string;findings:string;evidence:string;relevance:string;limitations:string[]};python_source:{visualization:string;preparation:string;topic:{chapter:string;topic:string;library_function:string;objective:string};highlights:string[]};sources:string[];limitations:string[];units:string;filters:Record<string,unknown>;sample_size:number;dataset_version:string;filter_hash:string}
export async function request(path:string, session:string, options:RequestInit={}) {
  const headers = new Headers(options.headers)
  if (session) headers.set('X-Session-Id', session)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type','application/json')
  const response = await fetch(API + path, {...options,headers})
  if (!response.ok) {
    const body = await response.json().catch(()=>({detail:response.statusText}))
    throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body))
  }
  return response.json()
}
export async function download(path:string, session:string, filename:string) {
  const response = await fetch(API + path,{headers:{'X-Session-Id':session}})
  if(!response.ok) throw new Error('Download failed')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a'); link.href=url; link.download=filename; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000)
}
