const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = {mode: 'demo', scope: 'library', view: 'discover', status: 'all', data: null, selectedRun: null, detailId: null, starting: false};
const titles = {
  discover: ['Tender discovery', 'A clearer view of the projects that fit your expertise.'],
  keywords: ['Search strategy', 'The right phrases make all the difference.'],
  history: ['Run history', 'Every search, saved for a clearer picture over time.'],
  settings: ['Setup & scheduling', 'Connect your search providers and keep discovery moving.']
};
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function safeUrl(value) {try {const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '#';} catch {return '#';}}
function dateLabel(value) {return value ? new Date(value).toLocaleString('en-IN', {timeZone:'Asia/Kolkata', day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}) + ' IST' : 'No run yet';}
function name(value) {return String(value).replaceAll('_', ' ');}
function providerName(value) {return value === 'codex' ? 'Codex search' : value.includes('gpt') ? 'GPT search' : 'Ollama search';}
function durationLabel(seconds) {return seconds === undefined ? '' : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;}
function matchThenValue(a, b) {return b.score-a.score || (b.tender_value_inr ?? -1)-(a.tender_value_inr ?? -1) || a.title.localeCompare(b.title);}
function priceOnly(a, b, descending) {
  const left = a.tender_value_inr, right = b.tender_value_inr;
  const leftKnown = Number.isFinite(left), rightKnown = Number.isFinite(right);
  if (leftKnown !== rightKnown) return leftKnown ? -1 : 1;
  return (leftKnown ? (descending ? right-left : left-right) : 0) || a.title.localeCompare(b.title);
}
function toast(message) {$('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('#toast').hidden = true, 3500);}
function banner(message) {$('#error-banner').textContent = message || ''; $('#error-banner').hidden = !message;}
async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Dashboard-Token':state.data?.token || ''}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}
let refreshSequence = 0;
let lastSnapshot = '';
async function refresh() {
  const sequence = ++refreshSequence;
  const mode = state.mode;
  const query = new URLSearchParams({mode, scope:state.scope});
  if (state.selectedRun) query.set('run', state.selectedRun);
  try {
    const data = await api('/api/state?' + query);
    if (sequence !== refreshSequence || mode !== state.mode) return;
    const signature = mode + state.scope + (state.selectedRun || '') + JSON.stringify(data);
    state.data = data;
    if (signature !== lastSnapshot) {render(); lastSnapshot = signature;}
    banner(null);
  } catch (error) {if (sequence === refreshSequence) banner('Could not load workspace. ' + error.message);}
}
function setView(view) {
  state.view = view;
  $$('.view').forEach(element => element.hidden = element.id !== 'view-' + view);
  $$('.nav').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  $('#page-title').textContent = titles[view][0]; $('#page-subtitle').textContent = titles[view][1];
  $('#breadcrumb').textContent = {discover:'Discover', keywords:'Search strategy',history:'Run history',settings:'Setup & scheduling'}[view];
}
function render() {
  const {report, history, profile, configuration, job} = state.data;
  const candidates = report?.candidates || [];
  const count = status => candidates.filter(item => item.status === status).length;
  $('#metric-total').textContent = candidates.length;
  $('#metric-shortlist').textContent = count('shortlist'); $('#metric-review').textContent = count('review');
  $('#metric-coverage').textContent = report ? `${report.successful_searches}/${report.attempted_searches}` : '—';
  $('#coverage-foot').textContent = report ? `${report.queries.length} capability queries · ${state.mode === 'demo' ? 'synthetic data' : 'web search'}` : 'Provider searches completed';
  if (report?.scope === 'library') $('#coverage-foot').textContent = `Latest run · ${report.queries.length} capability queries`;
  $('#nav-count').textContent = candidates.length; $('#result-count').textContent = candidates.length;
  $('#tab-all').textContent = candidates.length;
  ['shortlist','review','rejected'].forEach(status => $('#tab-' + status).textContent = count(status));
  $('#last-run').textContent = `${state.selectedRun ? 'Saved run' : 'Last discovery'} · ${dateLabel(report?.created_at)}`;
  if (report?.timing) $('#last-run').textContent += ' · ' + durationLabel(report.timing.processing_seconds);
  $('#latest-run').hidden = !state.selectedRun;
  $('#latest-run').textContent = state.scope === 'library' ? 'Back to library ↗' : 'Back to latest run ↗';
  $('#run-health').textContent = report ? `${report.health === 'ok' ? '●' : '◈'} ${report.health === 'ok' ? 'Run complete' : report.health + ' run'}` : 'Ready to discover';
  if (report?.ranking_cache) $('#run-health').textContent += ` · ${report.ranking_cache.hits} cached ranks`;
  $('#context-note').textContent = state.mode === 'demo' ? 'Synthetic examples for presentation' : 'Live discovery · verify original notices';
  if (report?.library_summary) {
    const summary = report.library_summary;
    $('#context-note').textContent = `${summary.new} new · ${summary.rediscovered} rediscovered · ${summary.not_seen} not seen this run`;
  }
  $$('[data-mode]').forEach(button => button.classList.toggle('selected', button.dataset.mode === state.mode));
  $$('[data-scope]').forEach(button => button.classList.toggle('selected', button.dataset.scope === (state.selectedRun ? 'run' : state.scope)));
  $('#lifecycle-filter').disabled = report?.scope !== 'library';
  $('#export').disabled = !report;
  $('#capability-chips').innerHTML = ['Musical fountains','Multimedia shows','Water features','Aeration'].map(term => `<span class="chip">${esc(term)}</span>`).join('');
  $('#gpt-source-count').textContent = candidates.filter(c => c.providers.some(p => p.includes('gpt'))).length;
  $('#codex-source-count').textContent = candidates.filter(c => c.providers.includes('codex')).length;
  $('#ollama-source-count').textContent = candidates.filter(c => c.providers.some(p => p.includes('ollama'))).length;
  $('#keyword-groups').innerHTML = Object.entries(profile.groups).map(([group, phrases], index) => `<section class="panel keyword-card"><span class="keyword-number">CAPABILITY ${String(index+1).padStart(2,'0')}</span><h3>${esc(name(group))}</h3>${phrases.map(term => `<span class="chip">${esc(term)}</span>`).join('')}</section>`).join('');
  $('#query-list').innerHTML = report?.queries.map(query => `<div class="query-row"><strong>${esc(name(query.group))}</strong><code>${esc(query.query)}</code></div>`).join('') || '<p class="subtle">Run discovery to generate a keyword plan.</p>';
  $('#history-list').innerHTML = history.length ? history.map(run => `<div class="history-row"><span class="source-icon">◷</span><div><strong>${esc(dateLabel(run.created_at))}</strong><p>${run.count} discovered · ${run.shortlisted} shortlisted · ${esc(run.health)}</p></div><button class="button secondary" data-open-run="${esc(run.id)}">View run ↗</button></div>`).join('') : '<div class="empty">Your discovery runs will appear here.</div>';
  $('#connections').innerHTML = [['OpenAI', configuration.gpt_model, configuration.openai_key], ['Ollama web search', 'Hosted search API', configuration.ollama_key]].map(([label, model, configured]) => `<div class="connection-row"><div>${esc(label)}<small>${esc(model)}</small></div><span class="badge ${configured ? 'shortlist' : 'review'}">${configured ? 'Key configured' : 'Key needed'}</span></div>`).join('') + `<div class="connection-row"><div>Optional Ollama ranking model<small>${esc(configuration.ollama_model)}</small></div><span class="badge">Availability not checked</span></div>`;
  $('#connections').insertAdjacentHTML('afterbegin', `<div class="connection-row"><div>Codex search & ranking<small>ChatGPT sign-in · no API key</small></div><span class="badge ${configuration.codex_cli ? 'shortlist' : 'review'}">${configuration.codex_cli ? 'CLI found; sign-in checked on run' : 'CLI not found'}</span></div>`);
  const running = job?.status === 'running';
  $('#new-run').disabled = running || state.starting;
  $('#job-panel').hidden = !job && !report?.issues.length;
  if (job) {
    $('#job-title').textContent = running ? `Discovery running · ${job.mode} workspace` : `Last session run ${job.status}`;
    $('#job-log').textContent = [...(job.logs || []), ...(report?.issues || [])].join('\n');
  } else if (report?.issues.length) {
    $('#job-title').textContent = `${report.issues.length} run issue${report.issues.length === 1 ? '' : 's'} · check activity`;
    $('#job-log').textContent = report.issues.join('\n');
  }
  renderCandidates();
  if (state.detailId && $('#detail-dialog').open) renderDetail();
}
function renderCandidates() {
  const all = state.data?.report?.candidates || [];
  const query = $('#search').value.toLowerCase().trim();
  const provider = $('#provider-filter').value;
  const lifecycle = state.data?.report?.scope === 'library' ? $('#lifecycle-filter').value : 'all';
  const candidates = all.filter(item => (state.status === 'all' || item.status === state.status) &&
    (provider === 'all' || item.providers.some(p => p.includes(provider))) &&
    (lifecycle === 'all' || item.discovery_status === lifecycle) &&
    (!query || [item.title, item.reason, ...item.snippets, ...item.groups].join(' ').toLowerCase().includes(query)));
  const sort = $('#sort').value;
  candidates.sort((a,b) => sort === 'price-desc' ? priceOnly(a,b,true) : sort === 'price-asc' ? priceOnly(a,b,false) : sort === 'title' ? a.title.localeCompare(b.title) : sort === 'new' ? (b.change === 'new') - (a.change === 'new') || matchThenValue(a,b) : matchThenValue(a,b));
  $('#list-count').textContent = `Showing ${candidates.length} of ${all.length} opportunities`;
  $('#candidate-list').innerHTML = candidates.length ? candidates.map(item => {
    const hostname = new URL(safeUrl(item.url), location.origin).hostname;
    const feedback = state.data.feedback[item.id];
    return `<article class="candidate"><div class="candidate-icon" aria-hidden="true">${item.groups.includes('multimedia') || item.groups.includes('shows') ? '✧' : '≈'}</div><div class="candidate-body"><div class="candidate-top"><button class="candidate-title" data-detail="${esc(item.id)}">${esc(item.title)}</button><span class="score ${item.score < .4 ? 'low' : item.score < .7 ? 'medium' : ''}">${Math.round(item.score*100)}% match</span></div><div class="candidate-meta"><span>${esc(hostname)}</span><span>·</span><span>${esc(item.groups.slice(0,2).map(name).join(' / '))}</span><span>·</span><span>${item.deadline ? 'Deadline ' + esc(item.deadline) + ' (unverified)' : 'Deadline not confirmed'}</span></div><div class="tender-value">${esc(item.tender_value_display || 'Not stated')}<span>Estimated tender value · unverified</span></div><p class="candidate-desc">${esc(item.reason)}</p><div class="candidate-bottom"><div class="badges"><span class="badge ${esc(item.status)}">${{shortlist:'Shortlisted',review:'Needs review',rejected:'Rejected'}[item.status] || esc(item.status)}</span>${item.discovery_status ? `<span class="badge new">${({new:'New this run',rediscovered:'Rediscovered',not_seen:'Not seen this run'})[item.discovery_status]}</span>` : item.change === 'new' ? '<span class="badge new">New</span>' : ''}${item.sources?.length > 1 ? `<span class="badge">${item.sources.length} sources</span>` : ''}${item.providers.map(provider => `<span class="badge">${providerName(provider)}</span>`).join('')}${feedback ? `<span class="badge">Reviewed: ${esc(feedback.label)}</span>` : ''}</div><button class="candidate-open" data-detail="${esc(item.id)}">View details ↗</button></div></div></article>`;
  }).join('') : `<div class="empty"><strong>${all.length ? 'No matches in this view' : 'A fresh start for discovery'}</strong>${all.length ? 'Try another keyword, provider or status filter.' : state.mode === 'demo' ? 'Run the offline demo to explore a synthetic shortlist.' : 'Sign in to Codex, then launch live discovery and ranking.'}</div>`;
}
function renderDetail() {
  const item = state.data.report?.candidates.find(c => c.id === state.detailId);
  if (!item) {$('#detail-dialog').close(); return;}
  const label = state.data.feedback[item.id]?.label;
  $('#detail-content').innerHTML = `<h2>${esc(item.title)}</h2><div class="badges"><span class="badge ${esc(item.status)}">${esc(item.status)}</span><span class="badge">${Math.round(item.score*100)}% capability overlap</span><span class="badge">${esc(state.mode)}</span></div><p>${esc(item.reason)}</p><blockquote>${esc(item.evidence)}</blockquote><div class="detail-meta">${item.first_seen ? `<div><small>FIRST DISCOVERED</small>${esc(dateLabel(item.first_seen))}</div><div><small>LAST SEEN IN DISCOVERY</small>${esc(dateLabel(item.last_seen))}</div><div><small>TENDER ID</small>${esc((item.tender_ids || []).join(', ') || 'Not identified')}</div><div><small>DISCOVERY STATUS</small>${esc(({new:'New this run',rediscovered:'Rediscovered',not_seen:'Not seen this run'})[item.discovery_status])}</div>` : ''}<div><small>ESTIMATED TENDER VALUE</small>${esc(item.tender_value_display || 'Not stated')}<small>${esc(item.tender_value_status || 'not stated')}</small></div><div><small>DEADLINE</small>${esc(item.deadline || 'Unknown')}<small>${esc(item.deadline_status)}</small></div><div><small>ELIGIBILITY</small>Not assessed</div><div><small>DISCOVERED BY</small>${esc(item.providers.join(', '))}</div><div><small>SOURCE STATUS</small>Unverified search text</div></div><a class="button primary" href="${esc(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">Open original source ↗</a><a class="detail-source" href="${esc(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">${esc(item.url)}</a><details class="detail-provenance"><summary>Sources, search provenance & snippets</summary>${(item.sources || []).map(source => `<p><a href="${esc(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">${esc(source.url)}</a><br>Last discovered: ${esc(dateLabel(source.last_seen))}</p>`).join('')}${(item.tender_value_evidence || []).map(text => `<p>Value evidence: ${esc(text)}</p>`).join('')}<p>${esc(item.evidence_kind.join(' · '))}</p>${item.queries.map(query => `<p>${esc(query)}</p>`).join('')}${item.snippets.map(text => `<p>${esc(text)}</p>`).join('')}</details><div class="feedback-row"><span>Was this a useful opportunity?</span><button class="button secondary ${label === 'relevant' ? 'chosen' : ''}" data-feedback="relevant">✓ Relevant</button><button class="button secondary ${label === 'irrelevant' ? 'chosen' : ''}" data-feedback="irrelevant">× Irrelevant</button></div><p class="small-note">Your review improves future keyword allocation. It does not change this run’s original model score.</p>`;
}
$$('.nav').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
$$('[data-mode]').forEach(button => button.addEventListener('click', () => {state.mode = button.dataset.mode; state.selectedRun = null; state.detailId = null; $('#detail-dialog').close(); refresh();}));
$$('[data-scope]').forEach(button => button.addEventListener('click', () => {state.scope = button.dataset.scope; state.selectedRun = null; state.detailId = null; $('#detail-dialog').close(); refresh();}));
$$('[data-status]').forEach(button => button.addEventListener('click', () => {state.status = button.dataset.status; $$('[data-status]').forEach(b => b.classList.toggle('active', b === button)); renderCandidates();}));
['search','provider-filter','lifecycle-filter','sort'].forEach(id => $('#' + id).addEventListener(id === 'search' ? 'input' : 'change', renderCandidates));
$('#strategy-link').addEventListener('click', () => setView('keywords'));
$('#latest-run').addEventListener('click', () => {state.selectedRun = null; refresh();});
$('#toggle-log').addEventListener('click', () => {const log = $('#job-log'); log.hidden = !log.hidden; $('#toggle-log').textContent = log.hidden ? 'Show activity' : 'Hide activity';});
$('#new-run').addEventListener('click', () => {$('#run-mode').value = state.mode; $('#run-error').hidden = true; $('#run-dialog').showModal();});
['close-run','cancel-run'].forEach(id => $('#' + id).addEventListener('click', () => $('#run-dialog').close()));
$('#close-detail').addEventListener('click', () => $('#detail-dialog').close());
$('#run-form').addEventListener('submit', async event => {
  event.preventDefault(); state.starting = true; $('#start-run').disabled = true; $('#run-error').hidden = true;
  try {
    const mode = $('#run-mode').value;
    await api('/api/run', {mode, provider:$('#run-provider').value, ranker:$('#run-ranker').value, query_budget:Number($('#run-budget').value)});
    state.mode = mode; state.selectedRun = null; state.detailId = null; $('#run-dialog').close(); setView('discover'); toast('Discovery started'); await refresh();
  } catch (error) {$('#run-error').textContent = error.message; $('#run-error').hidden = false;}
  finally {state.starting = false; $('#start-run').disabled = false;}
});
document.addEventListener('click', async event => {
  const detail = event.target.closest('[data-detail]');
  if (detail) {state.detailId = detail.dataset.detail; renderDetail(); $('#detail-dialog').showModal();}
  const run = event.target.closest('[data-open-run]');
  if (run) {state.selectedRun = run.dataset.openRun; setView('discover'); await refresh();}
  const feedback = event.target.closest('[data-feedback]');
  if (feedback) {
    feedback.disabled = true;
    try {const item = state.data.report.candidates.find(c => c.id === state.detailId); await api('/api/feedback', {mode:state.mode,url:item.url,label:feedback.dataset.feedback}); toast('Review saved for future searches'); await refresh();}
    catch (error) {toast(error.message); feedback.disabled = false;}
  }
});
$('#export').addEventListener('click', () => {
  if (!state.data?.report) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.data.report,null,2)],{type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = `ripples-tenders-${state.mode}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
refresh();
setInterval(refresh, 2500);
