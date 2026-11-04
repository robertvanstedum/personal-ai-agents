const repo = 'robertvanstedum/personal-ai-agents';
export async function githubIssues(args, fetcher = fetch) {
  if (!args || !['latest', 'read'].includes(args.operation) || Object.keys(args).some(k => !['operation', 'number'].includes(k)) ||
      (args.operation === 'read' && (!Number.isInteger(args.number) || args.number < 1 || args.number > 999999))) {
    return {status:'refused', code:'invalid_request'};
  }
  const path = args.operation === 'latest'
    ? `/repos/${repo}/issues?state=all&sort=created&direction=desc&per_page=30`
    : `/repos/${repo}/issues/${args.number}`;
  try {
    const r = await fetcher('https://api.github.com' + path, {method:'GET', redirect:'error', signal:AbortSignal.timeout(8000),
      headers:{Accept:'application/vnd.github+json', 'User-Agent':'minimoi-cos-github'}});
    if (!r.ok) return {status:'unavailable', code:'github_http_' + r.status};
    const text = await r.text();
    if (text.length > 400000) return {status:'unavailable', code:'response_too_large'};
    const data = JSON.parse(text);
    let rows = args.operation === 'latest' ? data : [data];
    if (!Array.isArray(rows) || rows.some(x=>!Number.isInteger(x.number) || typeof x.title !== 'string')) return {status:'unavailable',code:'invalid_response'};
    if (args.operation === 'read' && rows[0].pull_request) return {status:'refused',code:'is_pull_request'};
    rows = rows.filter(x => !x.pull_request);
    if (args.operation === 'latest' && !rows.length && data.length === 30) return {status:'unavailable',code:'issue_window_exhausted'};
    return {status:'verified', source:'Live public GitHub API', repository:repo, untrusted_data:true,
      issues:rows.slice(0,5).map(x=>({number:x.number,title:x.title.slice(0,500),state:x.state,created_at:x.created_at,
        url:`https://github.com/${repo}/issues/${x.number}`,body:String(x.body || '').slice(0,4000)}))};
  } catch { return {status:'unavailable',code:'github_unreachable'}; }
}
