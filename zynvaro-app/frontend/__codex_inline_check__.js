




// ════════════════════════════════════════════════════════════════
// CONFIG
// ════════════════════════════════════════════════════════════════
const API = '';
let authToken = localStorage.getItem('zynvaro_token');
let currentUser = JSON.parse(localStorage.getItem('zynvaro_user') || 'null');
let selectedTier = null;
let activePolicyData = null;
const APP_QUERY = new URLSearchParams(window.location.search);
const MOCK_PAYMENT_FALLBACK_ENABLED =
  APP_QUERY.get('mockPayments') === '1' ||
  localStorage.getItem('zynvaro_mock_payments') === '1';

// ════════════════════════════════════════════════════════════════
// UTILS
// ════════════════════════════════════════════════════════════════
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `${type==='success'?'✅':type==='error'?'❌':'ℹ️'} ${esc(msg)}`;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), type === 'error' ? 6000 : 4000);
}

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  const res = await fetch(`${API}${path}`, { ...opts, headers: { ...headers, ...opts.headers } });
  if (res.status === 401 && authToken) {
    logout();
    throw new Error('Session expired. Please log in again.');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Request failed' }));
    // Pydantic validation errors return detail as an array of objects
    const msg = Array.isArray(err.detail)
      ? err.detail.map(e => e.msg || e.message || JSON.stringify(e)).join('; ')
      : (err.detail || 'Request failed');
    throw new Error(msg);
  }
  // 204 No Content has no body — return null instead of crashing on json()
  if (res.status === 204 || res.headers.get('content-length') === '0') return null;
  return res.json();
}

function refreshIcons() { if (window.lucide) lucide.createIcons(); }

function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s ?? '');
  return d.innerHTML;
}
function fmt(n) { return '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 }); }
function fmtScore(s) { const n = Number(s); return (isNaN(n) ? 0 : n).toFixed(0) + '/100'; }
function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}
function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}
function riskClass(score) {
  if (score < 0.4) return 'risk-low';
  if (score < 0.7) return 'risk-medium';
  return 'risk-high';
}
function riskLabel(score) {
  if (score < 0.4) return '🟢 Low Risk';
  if (score < 0.7) return '🟡 Moderate';
  return '🔴 High Risk';
}
function triggerIcon(type) {
  const map = {
    'Heavy Rainfall':'🌧️','Extreme Rain / Flooding':'🌊','Severe Heatwave':'🔥',
    'Hazardous AQI':'🏭','Platform Outage':'☁️','Civil Disruption':'🚨'
  };
  return map[type] || '⚡';
}
function scoreColor(s) {
  if (s >= 75) return '#00C853';
  if (s >= 45) return '#F59E0B';
  return '#EF4444';
}

function currentWorkerCity() {
  return currentUser?.effective_city || currentUser?.city || 'Bangalore';
}

function currentWorkerCityLabel() {
  if (!currentUser) return currentWorkerCity();
  if (currentUser.effective_city && currentUser.effective_city !== currentUser.city) {
    return `${currentUser.effective_city} (live)`;
  }
  return currentWorkerCity();
}

function currentWorkerPlatform() {
  return currentUser?.platform || 'Blinkit';
}

function currentCityTriggerHistoryPath(limit = 5) {
  const params = new URLSearchParams({
    city: currentWorkerCity(),
    limit: String(limit),
  });
  return `/triggers/?${params.toString()}`;
}

function currentLiveTriggerPath(cityOverride = null, platformOverride = null) {
  const params = new URLSearchParams({
    city: cityOverride || currentWorkerCity(),
    platform: platformOverride || currentWorkerPlatform(),
  });
  return `/triggers/live?${params.toString()}`;
}

function paymentModeCopy() {
  return MOCK_PAYMENT_FALLBACK_ENABLED
    ? 'Demo payment mode is enabled. Direct activation or renewal can happen without Razorpay Checkout.'
    : 'Premiums are charged through Razorpay Checkout in test mode. If the popup cannot open, the policy will not auto-activate or auto-renew.';
}

function renewModeCopy() {
  return MOCK_PAYMENT_FALLBACK_ENABLED
    ? 'Demo payment mode is enabled. Renewals may fall back to a direct extension for testing.'
    : 'Renewals open Razorpay Checkout manually in test mode. No automatic debit runs in the background.';
}

function updatePaymentModeNotice() {
  const activateNote = document.getElementById('payment-gateway-note');
  if (activateNote) activateNote.textContent = paymentModeCopy();

  const renewNote = document.getElementById('policy-renew-note');
  if (renewNote) renewNote.textContent = renewModeCopy();
}

function resetActivateButton(tierName) {
  const freshBtn = document.getElementById('btn-activate-policy');
  if (freshBtn) {
    freshBtn.disabled = false;
    freshBtn.innerHTML = `Activate ${tierName}`;
  }
}

function handlePaymentGatewayFallback(actionLabel, directAction, resetUi) {
  if (MOCK_PAYMENT_FALLBACK_ENABLED) {
    toast(`${actionLabel} is running in demo payment mode. Razorpay Checkout was skipped on purpose.`, 'info');
    return directAction();
  }

  if (typeof resetUi === 'function') resetUi();
  toast(
    `Razorpay Checkout is unavailable for ${actionLabel}. No automatic debit was attempted. Configure Razorpay or enable ?mockPayments=1 for demo fallback.`,
    'error'
  );
  return null;
}

// ════════════════════════════════════════════════════════════════
// AUTH
// ════════════════════════════════════════════════════════════════
function showAuthTab(tab) {
  document.getElementById('login-form').classList.toggle('hidden', tab !== 'login');
  document.getElementById('register-form').classList.toggle('hidden', tab !== 'register');
  document.querySelectorAll('.auth-tab').forEach((t, i) => t.classList.toggle('active', (i===0) === (tab==='login')));
}

async function handleLogin() {
  const phone = document.getElementById('login-phone').value.trim();
  const pass  = document.getElementById('login-pass').value;
  if (!phone || !pass) return toast('Fill in all fields', 'error');

  const btn = document.getElementById('btn-login');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Logging in...';

  try {
    const fd = new FormData();
    fd.append('username', phone); fd.append('password', pass);
    const res = await fetch(`${API}/auth/login`, { method: 'POST', body: fd });
    if (!res.ok) throw new Error('Invalid credentials');
    const data = await res.json();

    authToken = data.access_token;
    localStorage.setItem('zynvaro_token', authToken);
    const profile = await api('/auth/me');
    currentUser = profile;
    localStorage.setItem('zynvaro_user', JSON.stringify(profile));

    toast(`Welcome back, ${profile.full_name.split(' ')[0]}! 🎉`, 'success');
    showApp();
  } catch(e) {
    toast(e.message, 'error');
  } finally {
    btn.disabled = false; btn.innerHTML = 'Login to Zynvaro';
  }
}

async function handleRegister() {
  const name    = document.getElementById('reg-name').value.trim();
  const phone   = document.getElementById('reg-phone').value.trim();
  const pass    = document.getElementById('reg-pass').value;
  const city    = document.getElementById('reg-city').value;
  const pincode = document.getElementById('reg-pincode').value.trim();
  const platform= document.getElementById('reg-platform').value;
  const shift   = document.getElementById('reg-shift').value;

  if (!name || !phone || !pass || !city || !pincode) return toast('Fill in all required fields', 'error');
  if (pass.length < 6) return toast('Password must be at least 6 characters', 'error');
  if (phone.length < 10) return toast('Enter a valid 10-digit mobile number', 'error');

  const btn = document.getElementById('btn-register');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Creating account...';

  try {
    const data = await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ full_name:name, phone, password:pass, city, pincode, platform, shift })
    });
    authToken = data.access_token;
    localStorage.setItem('zynvaro_token', authToken);
    const profile = await api('/auth/me');
    currentUser = profile;
    localStorage.setItem('zynvaro_user', JSON.stringify(profile));
    toast(`Account created! Welcome, ${name.split(' ')[0]} 🚀`, 'success');
    showApp();
  } catch(e) {
    toast(e.message, 'error');
  } finally {
    btn.disabled = false; btn.innerHTML = 'Create Account & Get Protected';
  }
}

function logout() {
  document.getElementById('profile-menu').classList.add('hidden');
  authToken = null; currentUser = null;
  selectedTier = null; activePolicyData = null;
  localStorage.removeItem('zynvaro_token');
  localStorage.removeItem('zynvaro_user');
  document.getElementById('app-wrapper').classList.add('hidden');
  document.getElementById('auth-wrapper').classList.remove('hidden');
  toast('Logged out', 'info');
}

function toggleProfileMenu() {
  const menu = document.getElementById('profile-menu');
  menu.classList.toggle('hidden');
}

// Close profile menu when clicking elsewhere
document.addEventListener('click', e => {
  const menu = document.getElementById('profile-menu');
  const avatar = document.getElementById('nav-avatar');
  if (menu && !menu.classList.contains('hidden') && !menu.contains(e.target) && e.target !== avatar) {
    menu.classList.add('hidden');
  }
});

// ════════════════════════════════════════════════════════════════
// APP SHELL
// ════════════════════════════════════════════════════════════════
function showApp() {
  document.getElementById('auth-wrapper').classList.add('hidden');
  document.getElementById('app-wrapper').classList.remove('hidden');

  if (currentUser) {
    const first = currentUser.full_name.split(' ')[0];
    document.getElementById('nav-avatar').textContent = first[0];
    document.getElementById('header-user-name').textContent = first;
    document.getElementById('header-user-city').textContent =
      `${currentWorkerCityLabel()} · ${currentUser.platform}`;
    // Profile menu details
    document.getElementById('pm-name').textContent = currentUser.full_name;
    document.getElementById('pm-detail').textContent =
      `${currentWorkerCityLabel()} · ${currentUser.platform} · ${currentUser.shift}`;

    const hour = new Date().getHours();
    const greet = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
    const greetEl = document.getElementById('dash-greeting');
    if (greetEl) greetEl.textContent = `${greet}, ${first} 👋`;
  }

  loadDashboard();
  setTimeout(refreshIcons, 150);

  // Phase 3: Send GPS to backend for fraud detection (non-blocking, silent)
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(pos => {
      api('/auth/me/location', {
        method: 'POST',
        body: JSON.stringify({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      }).then(loc => {
        currentUser = {
          ...currentUser,
          last_known_lat: loc.lat,
          last_known_lng: loc.lng,
          last_location_at: loc.last_location_at,
          effective_city: loc.effective_city || currentUser?.effective_city || currentUser?.city,
          location_source: loc.location_source || currentUser?.location_source,
          location_fresh: typeof loc.location_fresh === 'boolean' ? loc.location_fresh : currentUser?.location_fresh,
          location_age_minutes: loc.location_age_minutes ?? currentUser?.location_age_minutes,
        };
        localStorage.setItem('zynvaro_user', JSON.stringify(currentUser));

        const headerCity = document.getElementById('header-user-city');
        if (headerCity) headerCity.textContent = `${currentWorkerCityLabel()} · ${currentWorkerPlatform()}`;
        const profileDetail = document.getElementById('pm-detail');
        if (profileDetail) profileDetail.textContent = `${currentWorkerCityLabel()} · ${currentWorkerPlatform()} · ${currentUser.shift}`;
        if (document.getElementById('page-dashboard')?.classList.contains('active')) {
          loadDashboard();
        }
        if (document.getElementById('page-triggers')?.classList.contains('active')) {
          syncTriggerPageContext();
          loadLiveConditions();
          loadTriggerLiveStatus();
          loadTriggerEvents();
        }
      }).catch(() => {});  // Silent — GPS is optional
    }, () => {}, { timeout: 5000, maximumAge: 300000 });
  }
}

function navTo(page) {
  const pageEl = document.getElementById(`page-${page}`);
  if (!pageEl) { console.error('navTo: unknown page', page); return; }

  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link, .bottom-nav-item').forEach(l => l.classList.remove('active'));

  pageEl.classList.add('active');
  pageEl.scrollTop = 0;
  document.querySelectorAll(`[onclick="navTo('${page}')"]`).forEach(l => l.classList.add('active'));

  // Push history state so back button works within the app
  if (history.state?.page !== page) {
    history.pushState({ page }, '', `#${page}`);
  }

  if (page === 'dashboard') loadDashboard();
  if (page === 'policy')    loadPolicyPage();
  if (page === 'triggers')  { syncTriggerPageContext(); loadLiveConditions(); loadTriggerTypes(); loadTriggerLiveStatus(); loadTriggerEvents(); }
  if (page === 'claims')    loadClaimsPage();
  if (page === 'admin')     loadAdminPage();
  // Refresh Lucide SVG icons after DOM update
  setTimeout(refreshIcons, 150);
}

// ════════════════════════════════════════════════════════════════
// DASHBOARD
// ════════════════════════════════════════════════════════════════
async function loadDashboard() {
  try {
    const triggerHistoryPath = currentUser ? currentCityTriggerHistoryPath(5) : '/triggers/?limit=5';
    const [policy, claimStats, triggers, weeklySummary] = await Promise.all([
      api('/policies/active').catch(() => null),
      api('/claims/stats').catch(() => null),
      api(triggerHistoryPath).catch(() => []),
      api('/claims/my-weekly-summary').catch(() => null),
    ]);

    activePolicyData = policy;

    // ── Stats row ──
    document.getElementById('stat-policy-status').innerHTML =
      policy ? '<span style="color:var(--green)">ACTIVE</span>' : '<span style="color:var(--red)">NONE</span>';
    document.getElementById('stat-premium').textContent   = policy ? fmt(policy.weekly_premium)+'/wk' : '—';
    document.getElementById('stat-max-payout').textContent = policy ? fmt(policy.max_weekly_payout) : '—';
    document.getElementById('stat-total-saved').textContent = claimStats ? fmt(claimStats.total_payout_inr) : '₹0';

    // ── Earnings Protected Widget (Phase 3) ──
    const ewWidget = document.getElementById('earnings-widget');
    const ewEmpty = document.getElementById('earnings-widget-empty');
    if (weeklySummary && weeklySummary.active_coverage) {
      ewWidget.style.display = 'block';
      ewEmpty.style.display = 'none';
      document.getElementById('ew-total-protected').textContent = fmt(weeklySummary.earnings_protected_total);
      const used = weeklySummary.earnings_protected_this_week;
      const max = weeklySummary.max_weekly_payout;
      const remaining = weeklySummary.coverage_remaining_this_week;
      const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0;
      document.getElementById('ew-coverage-used').textContent = `${fmt(used)} / ${fmt(max)}`;
      document.getElementById('ew-coverage-bar').style.width = pct + '%';
      document.getElementById('ew-coverage-bar').style.background = pct > 80 ? 'var(--red)' : pct > 50 ? 'var(--yellow)' : 'var(--green)';
      document.getElementById('ew-coverage-remaining').textContent = `${fmt(remaining)} remaining this week`;
      document.getElementById('ew-claims-week').textContent = weeklySummary.claims_this_week;
      document.getElementById('ew-disruptions').textContent = weeklySummary.disruptions_this_week;
      document.getElementById('ew-premiums-paid').textContent = fmt(weeklySummary.total_premiums_paid);
    } else if (ewWidget && ewEmpty) {
      ewWidget.style.display = 'none';
      ewEmpty.style.display = 'block';
    }

    // ── Hero card ──
    const badge2 = document.getElementById('dash-policy-badge2');
    const heroCta = document.getElementById('dash-hero-cta');
    const heroDaysRow = document.getElementById('hero-days-row');
    const warningBanner = document.getElementById('dash-warning-banner');
    if (policy) {
      badge2.className = 'hero-badge hero-badge-active';
      badge2.textContent = `🛡️ ${policy.tier}`;
      document.getElementById('stat-premium-hero').textContent = `${fmt(policy.weekly_premium)}/week`;
      heroCta.textContent = 'View Policy Details';
      heroCta.onclick = () => navTo('policy');
      // Show days remaining row
      const daysLeft = Math.max(0, Math.ceil((new Date(policy.end_date) - Date.now()) / 86400000));
      document.getElementById('hero-days-num').textContent = daysLeft;
      document.getElementById('hero-tier-name').textContent = policy.tier;
      heroDaysRow.classList.remove('hidden');
      document.getElementById('hero-sub').classList.add('hidden');
      warningBanner.classList.add('hidden');
    } else {
      badge2.className = 'hero-badge hero-badge-none';
      badge2.textContent = '⚠ Unprotected';
      document.getElementById('stat-premium-hero').textContent = '';
      heroCta.textContent = 'Get Protected Now';
      heroCta.onclick = () => navTo('policy');
      heroDaysRow.classList.add('hidden');
      document.getElementById('hero-sub').classList.remove('hidden');
      // Warning banner if zone risk is high
      const zr = currentUser?.zone_risk_score || 0;
      if (zr > 0.5) {
        document.getElementById('dash-warning-body').textContent =
          `Your zone risk is ${(zr*100).toFixed(0)}%. A trigger event today means ₹0 payout.`;
        warningBanner.classList.remove('hidden');
      } else {
        warningBanner.classList.add('hidden');
      }
    }

    // ── Zone risk ──
    if (currentUser) {
      const zr = currentUser.zone_risk_score || 0;
      const zrColor = zr > 0.7 ? 'var(--red)' : zr > 0.45 ? 'var(--yellow)' : 'var(--green)';
      document.getElementById('stat-zone-risk').innerHTML =
        `<span style="color:${zrColor}">${(zr*100).toFixed(0)}%</span>`;
    }

    // ── Legacy compat ──
    const policyEl = document.getElementById('dash-policy-detail');
    if (policy) {
      document.getElementById('dash-policy-badge').innerHTML =
        `<span class="risk-chip risk-low">✅ ACTIVE</span>`;
      policyEl.innerHTML = `<b>${esc(policy.tier)}</b> · #${esc(policy.policy_number)}`;
    } else {
      document.getElementById('dash-policy-badge').innerHTML =
        `<span class="risk-chip risk-high">❌ UNPROTECTED</span>`;
      policyEl.innerHTML = `No active policy.`;
    }

    // ── Risk profile — static header renders instantly, AI narrative loads async ──
    if (currentUser) {
      const riskScore = currentUser.zone_risk_score;
      document.getElementById('dash-risk-profile').innerHTML = `
        <div class="list-item" style="border:none;padding:0 0 12px;">
          <div class="clay-icon clay-md clay-orange">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"></path><circle cx="12" cy="10" r="3"></circle></svg>
          </div>
          <div class="list-body">
            <div class="list-title">${esc(currentWorkerCityLabel())} · ${esc(currentUser.pincode)}</div>
            <div class="list-sub">${esc(currentUser.platform)} · ${esc(currentUser.shift)}</div>
          </div>
          <div class="list-right">
            <span class="risk-chip ${riskClass(riskScore)}">${riskLabel(riskScore)}</span>
          </div>
        </div>
        <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-bottom:12px;">
          <span>Claims: <b style="color:var(--text)">${currentUser.claim_history_count}</b></span>
          <span>Clean streak: <b style="color:var(--text)">${currentUser.disruption_streak} wks</b></span>
          <span>Zone risk: <b style="color:var(--text)">${(riskScore*100).toFixed(0)}%</b></span>
        </div>
        <div id="risk-narrative-wrap" style="border-top:1px solid var(--border);padding-top:12px;">
          <p class="text-muted text-sm" style="font-style:italic;">🤖 Generating AI risk analysis...</p>
        </div>`;
      // Non-blocking — fills in AI narrative after primary dashboard renders
      loadRiskNarrative();
    }

    // ── Trigger feed ──
    const cityName = currentWorkerCity();
    renderTriggerFeed(triggers, 'dash-trigger-feed', {
      emptyMessage: `No saved disruptions in ${cityName} yet. Open Triggers to run a live check.`,
    });

    // ── Live conditions (non-blocking) ──
    loadDashLiveConditions();

  } catch(e) {
    console.error(e);
    if (e.message !== 'Session expired. Please log in again.') {
      toast('Failed to load dashboard data', 'error');
    }
  }
}

// ════════════════════════════════════════════════════════════════
// AI RISK NARRATIVE (non-blocking, called from loadDashboard)
// ════════════════════════════════════════════════════════════════
async function loadRiskNarrative() {
  try {
    const rp = await api('/policies/risk-profile');
    const wrap = document.getElementById('risk-narrative-wrap');
    if (!wrap) return;

    const badge = rp.llm_powered
      ? `<span style="font-size:10px;background:var(--purple-d);color:var(--purple);padding:2px 8px;border-radius:50px;font-weight:700;white-space:nowrap;">✨ Claude AI</span>`
      : `<span style="font-size:10px;background:var(--card3);color:var(--muted);padding:2px 8px;border-radius:50px;white-space:nowrap;">Rule-based</span> <span class="info-tip" style="width:16px;height:16px;font-size:9px;">i<span class="tip-text">Currently using rule-based template narratives. Phase 3: Connect Anthropic Claude API for AI-generated personalized risk explanations per worker.</span></span>`;

    const keyRisks = (rp.key_risks || []).length
      ? `<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px;">
           ${rp.key_risks.map(r => `<span style="font-size:11px;background:var(--red-d);color:var(--red);padding:2px 8px;border-radius:50px;">⚠ ${r}</span>`).join('')}
         </div>`
      : '';

    const seasonAlert = rp.seasonal_alert
      ? `<div style="margin-top:10px;font-size:12px;color:var(--yellow);line-height:1.5;"><span style="margin-right:4px;">🌦</span>${rp.seasonal_alert}</div>`
      : '';

    const tierTip = rp.tier_tip
      ? `<div style="margin-top:10px;font-size:12px;color:var(--blue);line-height:1.5;"><span style="margin-right:4px;">💡</span>${rp.tier_tip}</div>`
      : '';

    const premRow = rp.weekly_premium
      ? `<div style="margin-top:12px;display:flex;gap:16px;flex-wrap:wrap;font-size:12px;border-top:1px solid var(--border);padding-top:10px;">
           <span style="color:var(--muted);">Suggested premium <b style="color:var(--text);">${fmt(rp.weekly_premium)}/wk</b></span>
           <span style="color:var(--muted);">Max daily payout <b style="color:var(--text);">${fmt(rp.max_daily_payout)}</b></span>
           <span style="color:var(--muted);">Income coverage <b style="color:var(--text);">${rp.income_replacement}%</b></span>
         </div>`
      : '';

    wrap.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <span style="font-size:12px;font-weight:700;color:var(--text);">AI Risk Analysis</span>
        ${badge}
      </div>
      <p class="narrative-text" id="risk-narrative-text">${rp.narrative}</p>
      <button class="read-more-btn" onclick="document.getElementById('risk-narrative-text').classList.toggle('expanded'); this.textContent = this.textContent === 'Read more' ? 'Show less' : 'Read more';">Read more</button>
      ${keyRisks}
      ${seasonAlert}
      ${tierTip}
      ${premRow}
    `;
  } catch(e) {
    const wrap = document.getElementById('risk-narrative-wrap');
    if (wrap) wrap.innerHTML = '<p class="text-muted text-sm">Risk analysis unavailable.</p>';
  }
}

function renderTriggerFeed(events, containerId, opts = {}) {
  const el = document.getElementById(containerId);
  if (!events || events.length === 0) {
    const msg = opts.emptyMessage || '✅ No active disruptions. Monitoring continues...';
    el.innerHTML = `<p class="text-muted text-sm" style="padding:8px 0;">${msg}</p>`;
    return;
  }
  el.innerHTML = events.map(e => {
    const simBadge = e.is_simulated
      ? '<span style="font-size:9px;padding:2px 6px;border-radius:50px;background:rgba(249,115,22,0.15);color:#f97316;font-weight:700;letter-spacing:.3px;">SIMULATED</span>'
      : '<span style="font-size:9px;padding:2px 6px;border-radius:50px;background:rgba(34,197,94,0.12);color:#22c55e;font-weight:700;letter-spacing:.3px;">ORGANIC</span>';
    return `
    <div class="trigger-item">
      <div class="trigger-icon-wrap">${triggerIcon(e.trigger_type)}</div>
      <div style="flex:1;min-width:0;">
        <div class="flex items-center gap-2 mb-1" style="flex-wrap:wrap;">
          <b class="text-sm">${esc(e.trigger_type)}</b>
          <span class="trigger-badge sev-${e.severity}">${e.severity}</span>
          ${simBadge}
        </div>
        <p class="text-muted text-xs" style="line-height:1.4;">${esc(e.description || '')}</p>
        ${signalBadgeRow({
          confidence: e.confidence_score,
          sourcePrimary: e.source_primary,
          sourceSecondary: e.source_secondary,
          sourceLog: e.source_log,
          confidenceLabel: 'Signal',
        })}
        <p class="text-xs mt-1" style="color:var(--muted)">
          📍 ${esc(e.city)} · ${e.measured_value} ${esc(e.unit)} · ${timeAgo(e.detected_at)}
        </p>
      </div>
    </div>
  `;
  }).join('');
}

async function loadTriggers() {
  try {
    const events = await api(currentCityTriggerHistoryPath(5));
    if (!events.length) {
      document.getElementById('dash-trigger-feed').innerHTML =
        `<p class="text-muted text-sm" style="padding:8px 0;">No saved disruptions in ${esc(currentWorkerCity())} yet. Open Triggers to run a live check.</p>`;
      return;
    }
    renderTriggerFeed(events, 'dash-trigger-feed');
  } catch(e) {
    console.error('[loadTriggers]', e);
    const el = document.getElementById('dash-trigger-feed');
    if (el) el.innerHTML = '<p class="text-muted text-sm" style="padding:8px 0;">Failed to load triggers.</p>';
  }
}

// ════════════════════════════════════════════════════════════════
// POLICY PAGE
// ════════════════════════════════════════════════════════════════
function syncTriggerPageContext() {
  const simCity = document.getElementById('sim-city');
  if (simCity && currentWorkerCity()) simCity.value = currentWorkerCity();

  const liveEl = document.getElementById('trigger-live-status');
  if (liveEl && currentUser) {
    liveEl.innerHTML = `<p class="text-muted text-sm">Checking ${esc(currentWorkerCity())} · ${esc(currentWorkerPlatform())}...</p>`;
  }
}

function renderLiveCheckResult(data) {
  const el = document.getElementById('trigger-live-status');
  if (!el) return;

  const hierarchy = data?.source_hierarchy || {};
  const badges = Object.entries(hierarchy).map(([label, meta]) => `
    <div style="padding:8px 10px;border-radius:12px;background:var(--card2);min-width:170px;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:3px;">
        <b style="font-size:11px;">${esc(label)}</b>
        <span style="font-size:10px;color:${meta.source_tier === 'fallback' ? 'var(--muted)' : meta.source_tier === 'secondary' ? 'var(--yellow)' : 'var(--green)'};">${esc((meta.source_tier || 'unknown').toUpperCase())}</span>
      </div>
      <div style="font-size:10px;color:var(--muted);line-height:1.4;">${esc(meta.status || data?.source_status?.[label] || '')}</div>
      <div style="font-size:9px;color:var(--muted);margin-top:3px;">${Number(meta.confidence_score || 0).toFixed(0)}% confidence${meta.claim_allowed ? ' · review gate active' : ' · monitoring only'}</div>
    </div>
  `).join('') || Object.entries(data?.source_status || {}).map(([label, status]) => `
    <span style="font-size:11px;padding:4px 8px;border-radius:999px;background:var(--card2);color:var(--muted);">
      ${esc(label)}: ${esc(status)}
    </span>
  `).join('');

  const eventsHtml = (data.events || []).map(e => `
    <div class="trigger-item">
      <div class="trigger-icon-wrap">${triggerIcon(e.trigger_type)}</div>
      <div style="flex:1;min-width:0;">
        <div class="flex items-center gap-2 mb-1" style="flex-wrap:wrap;">
          <b class="text-sm">${esc(e.trigger_type)}</b>
          <span class="trigger-badge sev-${e.severity}">${e.severity}</span>
          ${e.is_validated ? '<span class="trigger-badge" style="background:var(--green-d);color:var(--green);">Verified</span>' : '<span class="trigger-badge" style="background:rgba(245,158,11,0.15);color:var(--yellow);">Review Required</span>'}
        </div>
        <p class="text-xs text-muted" style="line-height:1.4;">${esc(e.description || '')}</p>
        ${signalBadgeRow({
          confidence: e.confidence_score,
          sourcePrimary: e.source_primary,
          sourceSecondary: e.source_secondary,
          sourceLog: e.source_log,
          confidenceLabel: 'Signal',
        })}
        <div class="flex gap-3 mt-1 text-xs text-muted" style="flex-wrap:wrap;">
          <span>${esc(e.city || data.city)}</span>
          <span>${e.measured_value} ${esc(e.unit || '')}</span>
          <span>${e.detected_at ? timeAgo(e.detected_at) : 'just now'}</span>
        </div>
      </div>
    </div>
  `).join('');

  const emptyText = data.triggers_fired > 0 && (!data.events || !data.events.length)
    ? `A trigger crossed threshold in ${data.city}, but it was already saved recently so no duplicate event was created.`
    : `No trigger thresholds are crossed right now in ${data.city}.`;

  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
      <div style="font-size:13px;font-weight:700;">${esc(data.city)} · ${esc(data.platform || currentWorkerPlatform())}</div>
      <div class="text-xs text-muted">Updated ${timeAgo(data.checked_at)}</div>
    </div>
    <p class="text-xs text-muted" style="line-height:1.5;margin-bottom:10px;">${esc(data.monitoring_note || 'Live monitoring check complete.')}</p>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;">${badges}</div>
    ${eventsHtml || `<p class="text-muted text-sm" style="padding:8px 0;">${esc(emptyText)}</p>`}
  `;
}

async function loadTriggerLiveStatus() {
  const el = document.getElementById('trigger-live-status');
  if (el) {
    el.innerHTML = `<p class="text-muted text-sm">Checking ${esc(currentWorkerCity())} · ${esc(currentWorkerPlatform())} through live monitoring...</p>`;
  }

  try {
    const data = await api(currentLiveTriggerPath());
    renderLiveCheckResult(data);
    loadTriggerEvents();
  } catch (e) {
    if (el) {
      el.innerHTML = `<p class="text-muted text-sm" style="padding:8px 0;">Live check failed: ${esc(e.message)}</p>`;
    }
  }
}

// ════════════════════════════════════════════════════════════════
// LIVE CONDITIONS (real-time weather / AQI / platform readings)
// ════════════════════════════════════════════════════════════════
function conditionsApiPath(cityOverride = null, platformOverride = null) {
  const params = new URLSearchParams({
    city: cityOverride || currentWorkerCity(),
    platform: platformOverride || currentWorkerPlatform(),
  });
  return `/triggers/conditions?${params.toString()}`;
}

function aqiColor(val) {
  if (val <= 50) return 'var(--green)';
  if (val <= 100) return '#a3e635';
  if (val <= 150) return 'var(--yellow)';
  if (val <= 200) return 'var(--orange)';
  if (val <= 300) return '#dc2626';
  return '#7f1d1d';
}

function sourceBadge(src) {
  const lower = (src || '').toLowerCase();
  const isFallback = lower.includes('mock') || lower.includes('fallback');
  const isSecondary = lower.includes('live') || lower.includes('probe') || lower.includes('gdelt') || lower.includes('waqi') || lower.includes('openweathermap') || lower.includes('continuity') || lower.includes('secondary');
  const bg = isFallback ? 'var(--card3)' : isSecondary ? 'rgba(245,158,11,0.15)' : 'var(--green-d)';
  const color = isFallback ? 'var(--muted)' : isSecondary ? 'var(--yellow)' : 'var(--green)';
  const label = isFallback ? '○ FALLBACK' : isSecondary ? '◐ CONTINUITY' : '● OFFICIAL';
  return `<span style="font-size:9px;padding:2px 6px;border-radius:50px;background:${bg};color:${color};font-weight:700;letter-spacing:.3px;">${label}</span>`;
}

function confidenceBadge(score, label = 'Confidence') {
  const parsed = Number(score);
  const hasScore = Number.isFinite(parsed);
  const color = !hasScore ? 'var(--muted)' : parsed >= 80 ? '#22c55e' : parsed >= 55 ? '#f59e0b' : '#ef4444';
  const bg = !hasScore ? 'rgba(255,255,255,0.08)' : parsed >= 80 ? 'rgba(34,197,94,0.12)' : parsed >= 55 ? 'rgba(245,158,11,0.12)' : 'rgba(239,68,68,0.12)';
  const text = !hasScore ? `${label} Pending` : `${parsed.toFixed(0)}% ${label}`;
  return `<span style="font-size:9px;padding:2px 6px;border-radius:50px;background:${bg};color:${color};font-weight:700;letter-spacing:.3px;">${esc(text.toUpperCase())}</span>`;
}

function sourceTierFromSignals(...parts) {
  const lower = parts.filter(Boolean).join(' ').toLowerCase();
  if (lower.includes('mock') || lower.includes('fallback') || lower.includes('simulat') || lower.includes('override') || lower.includes('unavailable')) return 'fallback';
  if (lower.includes('live') || lower.includes('probe') || lower.includes('gdelt') || lower.includes('waqi') || lower.includes('openweathermap') || lower.includes('continuity')) return 'secondary';
  return 'official';
}

function sourceSummary(primary, secondary) {
  return [primary, secondary].filter(Boolean).join(' + ') || 'Source pending';
}

function signalBadgeRow({
  confidence = null,
  sourcePrimary = '',
  sourceSecondary = '',
  sourceLog = '',
  sourceTier = null,
  confidenceLabel = 'Confidence',
} = {}) {
  const summary = sourceSummary(sourcePrimary, sourceSecondary);
  const tier = sourceTier || sourceTierFromSignals(sourcePrimary, sourceSecondary, sourceLog);
  return `
    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px;">
      ${confidenceBadge(confidence, confidenceLabel)}
      ${sourceBadge(`${tier} ${summary}`)}
      <span style="font-size:10px;color:var(--muted);">${esc(summary)}</span>
    </div>
  `;
}

function renderLiveConditionsCard(data, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;

  const w = data.weather;
  const a = data.aqi;
  const p = data.platform_status;
  const c = data.civil_disruption;
  const hierarchy = data.source_hierarchy || {};
  const wMeta = hierarchy.weather || {};
  const aMeta = hierarchy.aqi || {};
  const pMeta = hierarchy.platform || {};
  const cMeta = hierarchy.civil || {};

  // Temperature emoji
  const tempEmoji = w.temperature_c >= 40 ? '🔥' : w.temperature_c >= 30 ? '☀️' : w.temperature_c >= 20 ? '🌤️' : '❄️';
  // Rain status
  const rainNow = w.rain_1h_mm > 0;
  const rainEmoji = rainNow ? '🌧️' : '☁️';
  const rainPct = Math.min(100, (w.rain_24h_est_mm / w.rain_threshold_mm) * 100);
  // AQI
  const aqiPct = Math.min(100, (a.value / a.threshold) * 100);
  // Platform
  const platUp = p.status === 'UP';

  el.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
      <!-- Temperature -->
      <div style="background:var(--card2);border-radius:12px;padding:12px;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
          <span style="font-size:20px;">${tempEmoji}</span>
          <span style="font-size:22px;font-weight:800;color:var(--text);">${w.temperature_c}°C</span>
        </div>
        <div style="font-size:11px;color:var(--muted);line-height:1.4;">
          ${esc(w.description)}
          <div style="margin-top:3px;">Heatwave at ${w.heat_threshold_c}°C</div>
        </div>
        <div style="margin-top:4px;">${sourceBadge(w.source)}</div>
        <div style="font-size:9px;color:var(--muted);margin-top:4px;line-height:1.4;">${esc(wMeta.status || '')}</div>
      </div>

      <!-- AQI -->
      <div style="background:var(--card2);border-radius:12px;padding:12px;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
          <span style="font-size:20px;">🫁</span>
          <span style="font-size:22px;font-weight:800;color:${aqiColor(a.value)};">${a.value}</span>
        </div>
        <div style="font-size:11px;color:var(--muted);line-height:1.4;">
          ${esc(a.category)}
          <div style="margin-top:3px;">Hazardous at ${a.threshold}</div>
        </div>
        <div style="margin-top:4px;">${sourceBadge(a.source)}</div>
        <div style="font-size:9px;color:var(--muted);margin-top:4px;line-height:1.4;">${esc(aMeta.status || '')}</div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">
      <!-- Rainfall -->
      <div style="background:var(--card2);border-radius:12px;padding:12px;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
          <span style="font-size:18px;">${rainEmoji}</span>
          <span style="font-size:16px;font-weight:700;color:var(--text);">${w.rain_24h_est_mm} mm</span>
        </div>
        <div style="font-size:10px;color:var(--muted);margin-bottom:4px;">Rain (24h est) · Trigger at ${w.rain_threshold_mm}mm</div>
        <div style="height:4px;background:var(--card3);border-radius:4px;overflow:hidden;">
          <div style="height:100%;width:${rainPct}%;background:${rainPct > 80 ? 'var(--red)' : rainPct > 50 ? 'var(--yellow)' : 'var(--blue)'};border-radius:4px;transition:width .3s;"></div>
        </div>
        <div style="font-size:9px;color:var(--muted);margin-top:2px;">${rainPct.toFixed(0)}% of threshold${w.rain_1h_mm > 0 ? ' · ' + w.rain_1h_mm + ' mm/h now' : ''}</div>
        <div style="font-size:9px;color:var(--muted);margin-top:4px;line-height:1.4;">${esc(wMeta.status || '')}</div>
      </div>

      <!-- Platform -->
      <div style="background:var(--card2);border-radius:12px;padding:12px;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
          <span style="font-size:18px;">${platUp ? '✅' : '🔴'}</span>
          <span style="font-size:16px;font-weight:700;color:${platUp ? 'var(--green)' : 'var(--red)'};">${p.name}</span>
        </div>
        <div style="font-size:11px;color:var(--muted);line-height:1.4;">
          ${platUp ? 'Online' : 'DOWN'} · ${p.latency_ms}ms
        </div>
        <div style="margin-top:4px;">${sourceBadge(p.source)}</div>
        <div style="font-size:9px;color:var(--muted);margin-top:4px;line-height:1.4;">${esc(pMeta.status || '')}</div>
      </div>
    </div>

    <!-- Civil Disruption (single row) -->
    <div style="background:var(--card2);border-radius:12px;padding:10px 12px;display:flex;align-items:center;gap:10px;margin-bottom:8px;">
      <span style="font-size:18px;">${c.active ? '🚨' : '✅'}</span>
      <div style="flex:1;">
        <div style="font-size:13px;font-weight:700;color:${c.active ? 'var(--red)' : 'var(--green)'};">
          ${c.active ? 'Active: ' + esc(c.type || 'Disruption') : 'No Civil Disruption'}
        </div>
        <div style="font-size:10px;color:var(--muted);">${c.article_count} news articles in last 6h · ${sourceBadge(c.source)}</div>
        <div style="font-size:9px;color:var(--muted);margin-top:4px;line-height:1.4;">${esc(cMeta.status || '')}</div>
      </div>
    </div>

    <div style="font-size:10px;color:var(--muted);text-align:right;">
      ${esc(data.city)} · Updated ${timeAgo(data.checked_at)}
    </div>
  `;
}

function renderDashConditionsCompact(data, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;

  const w = data.weather;
  const a = data.aqi;
  const p = data.platform_status;
  const tempEmoji = w.temperature_c >= 40 ? '🔥' : w.temperature_c >= 30 ? '☀️' : w.temperature_c >= 20 ? '🌤️' : '❄️';
  const platUp = p.status === 'UP';

  el.innerHTML = `
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
      <div style="display:flex;align-items:center;gap:5px;background:var(--card2);padding:6px 12px;border-radius:10px;">
        <span>${tempEmoji}</span>
        <span style="font-weight:800;font-size:15px;">${w.temperature_c}°C</span>
        <span style="font-size:10px;color:var(--muted);">${esc(w.description)}</span>
      </div>
      <div style="display:flex;align-items:center;gap:5px;background:var(--card2);padding:6px 12px;border-radius:10px;">
        <span>🫁</span>
        <span style="font-weight:800;font-size:15px;color:${aqiColor(a.value)};">${a.value}</span>
        <span style="font-size:10px;color:var(--muted);">AQI</span>
      </div>
      <div style="display:flex;align-items:center;gap:5px;background:var(--card2);padding:6px 12px;border-radius:10px;">
        <span>${w.rain_1h_mm > 0 ? '🌧️' : '☁️'}</span>
        <span style="font-weight:700;font-size:13px;">${w.rain_24h_est_mm}mm</span>
      </div>
      <div style="display:flex;align-items:center;gap:5px;background:var(--card2);padding:6px 12px;border-radius:10px;">
        <span>${platUp ? '✅' : '🔴'}</span>
        <span style="font-size:12px;font-weight:600;">${p.name}</span>
        <span style="font-size:10px;color:var(--muted);">${p.latency_ms}ms</span>
      </div>
    </div>
    <div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">
      ${Object.entries(data.sources).map(([k,v]) => `${sourceBadge(v)} <span style="font-size:9px;color:var(--muted);">${esc(k)}</span>`).join('  ')}
    </div>
  `;
}

async function loadLiveConditions() {
  const el = document.getElementById('live-conditions-content');
  if (el) el.innerHTML = `<p class="text-muted text-sm">Fetching real-time data for ${esc(currentWorkerCity())}...</p>`;
  try {
    const data = await api(conditionsApiPath());
    renderLiveConditionsCard(data, 'live-conditions-content');
  } catch(e) {
    if (el) el.innerHTML = `<p class="text-muted text-sm">Failed to load conditions: ${esc(e.message)}</p>`;
  }
}

async function loadDashLiveConditions() {
  const el = document.getElementById('dash-conditions-content');
  if (el) el.innerHTML = `<p class="text-muted text-sm">Loading...</p>`;
  try {
    const data = await api(conditionsApiPath());
    renderDashConditionsCompact(data, 'dash-conditions-content');
  } catch(e) {
    if (el) el.innerHTML = `<p class="text-muted text-sm" style="font-size:11px;">Live conditions unavailable</p>`;
  }
}

async function loadPolicyPage() {
  try {
    const [quotesData, activePolicy] = await Promise.all([
      api('/policies/quote/all'),
      api('/policies/active').catch(() => null),
    ]);
    activePolicyData = activePolicy;
    updatePaymentModeNotice();

    if (activePolicy) {
      document.getElementById('policy-active-banner').classList.remove('hidden');
      document.getElementById('pol-active-tier').textContent = `Active: ${activePolicy.tier}`;
      document.getElementById('pol-active-detail').textContent =
        `${fmt(activePolicy.weekly_premium)}/week · Expires ${new Date(activePolicy.end_date).toLocaleDateString('en-IN')} · Max ${fmt(activePolicy.max_weekly_payout)}/week`;
    } else {
      document.getElementById('policy-active-banner').classList.add('hidden');
    }

    // Store tier data in a lookup so we don't need to inline JSON in onclick attributes
    const tiersEl = document.getElementById('tier-cards');
    const tierEmojis = ['🟢', '🔵', '🟣'];
    window._tierQuotes = {};
    quotesData.tiers.forEach(t => { window._tierQuotes[t.tier] = t; });
    tiersEl.innerHTML = quotesData.tiers.map((t, i) => {
      const safeTier = esc(t.tier);
      const tierAccent = t.tier === 'Basic Shield' ? ' tier-basic' : t.tier === 'Standard Guard' ? ' tier-standard' : ' tier-pro';
      return `
      <div class="tier-card${tierAccent}${t.tier === 'Standard Guard' ? ' recommended' : ''}${activePolicy?.tier === t.tier ? ' selected' : ''}"
           onclick="selectTierByName('${safeTier}')">
        ${t.tier === 'Standard Guard' ? '<div class="tier-badge">⭐ Recommended</div>' : ''}
        <div class="tier-name">${tierEmojis[i]} ${esc(t.tier)}</div>
        <div class="tier-price">${fmt(t.weekly_premium)}<span>/week</span></div>
        <p class="text-xs text-muted mt-1">Base: ${fmt(t.base_premium)} · AI-adjusted for your zone</p>
        <ul class="tier-features mt-3">
          ${getTierFeatures(t.tier).map(f =>
            `<li class="${f.yes?'yes':'no'}">${esc(f.label)}</li>`
          ).join('')}
        </ul>
        <div class="mt-3 text-sm">
          <span class="text-green fw-700">Up to ${fmt(t.max_weekly_payout)}/week</span>
        </div>
        <button class="btn btn-white btn-full mt-3" style="font-size:14px;"
          onclick="event.stopPropagation();selectTierByName('${safeTier}')">
          View Breakdown →
        </button>
      </div>
    `}).join('');

  } catch(e) {
    toast('Failed to load quotes: ' + e.message, 'error');
  }
}

function getTierFeatures(tier) {
  const all = [
    { label: 'AQI > 400 / Heatwave',         tiers: ['Basic Shield','Standard Guard','Pro Armor'] },
    { label: 'Heavy Rainfall',                tiers: ['Standard Guard','Pro Armor'] },
    { label: 'Extreme Rain / Flooding',       tiers: ['Standard Guard','Pro Armor'] },
    { label: 'Platform Outage',               tiers: ['Pro Armor'] },
    { label: 'Civil Disruption',              tiers: ['Pro Armor'] },
  ];
  return all.map(f => ({ label: f.label, yes: f.tiers.includes(tier) }));
}

function selectTierByName(tierName) {
  const data = window._tierQuotes?.[tierName];
  if (!data) { toast('Tier data not found', 'error'); return; }
  selectTier(tierName, data);
}

function selectTier(tierName, quoteData) {
  selectedTier = { name: tierName, quote: quoteData };
  document.querySelectorAll('.tier-card').forEach(c => c.classList.remove('selected'));
  document.querySelectorAll('.tier-card').forEach(c => {
    if (c.querySelector('.tier-name')?.textContent?.includes(tierName)) c.classList.add('selected');
  });

  const bk = quoteData.breakdown;
  document.getElementById('breakdown-tier-name').textContent = tierName;
  const activateBtn = document.getElementById('btn-activate-policy');
  activateBtn.textContent = `Pay & Activate ${fmt(quoteData.weekly_premium)}`;
  activateBtn.disabled = false;
  activateBtn.style.opacity = '1';

  document.getElementById('breakdown-rows').innerHTML = `
    <div class="breakdown-row"><span><i data-lucide="calculator" style="width:14px;height:14px;display:inline;vertical-align:-2px;margin-right:4px;"></i>Base Premium</span><span>${fmt(quoteData.base_premium)}</span></div>
    <div class="breakdown-row"><span><i data-lucide="map-pin" style="width:14px;height:14px;display:inline;vertical-align:-2px;margin-right:4px;"></i>Zone Risk (${(bk.zone_risk_score*100).toFixed(0)}%)</span><span class="breakdown-pos">+${fmt(bk.zone_loading_inr)}</span></div>
    <div class="breakdown-row"><span><i data-lucide="cloud-sun" style="width:14px;height:14px;display:inline;vertical-align:-2px;margin-right:4px;"></i>Seasonal Adjustment</span><span class="${bk.seasonal_loading_inr>0?'breakdown-pos':'breakdown-neg'}">${bk.seasonal_loading_inr>0?'+':''}${fmt(bk.seasonal_loading_inr)}</span></div>
    <div class="breakdown-row"><span><i data-lucide="file-text" style="width:14px;height:14px;display:inline;vertical-align:-2px;margin-right:4px;"></i>Claim History (${bk.claim_history_count})</span><span class="breakdown-pos">+${fmt(bk.claim_loading_inr)}</span></div>
    <div class="breakdown-row"><span><i data-lucide="trophy" style="width:14px;height:14px;display:inline;vertical-align:-2px;margin-right:4px;"></i>Streak Discount (${bk.streak_weeks} wks)</span><span class="breakdown-neg">${fmt(bk.streak_discount_inr)}</span></div>
    <div class="breakdown-row" style="border-top:2px solid var(--orange);padding-top:12px;margin-top:4px;"><span><i data-lucide="wallet" style="width:14px;height:14px;display:inline;vertical-align:-2px;margin-right:4px;"></i>Your Weekly Premium</span><span style="color:var(--orange)">${fmt(quoteData.weekly_premium)}</span></div>
  `;

  document.getElementById('breakdown-explanation').innerHTML =
    `<div style="font-size:13px;font-weight:700;margin-bottom:8px;color:var(--muted);">🤖 AI EXPLANATION</div>` +
    quoteData.explanation.map(e => `<p class="text-sm mt-1" style="color:var(--muted);">• ${e}</p>`).join('');

  document.getElementById('premium-breakdown').classList.remove('hidden');
  setTimeout(refreshIcons, 50);
  // Scroll within the active page container, not the body
  const breakdownEl = document.getElementById('premium-breakdown');
  const pageEl = document.getElementById('page-policy');
  if (breakdownEl && pageEl) {
    const offsetTop = breakdownEl.offsetTop - pageEl.offsetTop - 80;
    pageEl.scrollTo({ top: offsetTop, behavior: 'smooth' });
  }
}

function hidePremiumBreakdown() {
  document.getElementById('premium-breakdown').classList.add('hidden');
  document.querySelectorAll('.tier-card').forEach(c => c.classList.remove('selected'));
  selectedTier = null;
}

// Fallback: direct activation without payment (when Razorpay unavailable)
async function activatePolicyDirect(tierName) {
  try {
    await api('/policies/', { method: 'POST', body: JSON.stringify({ tier: tierName }) });
    toast(`${tierName} activated! You're now protected.`, 'success');
    hidePremiumBreakdown();
    loadPolicyPage();
    const profile = await api('/auth/me');
    currentUser = profile;
    localStorage.setItem('zynvaro_user', JSON.stringify(profile));
  } catch(e) {
    toast(e.message, 'error');
    const freshBtn = document.getElementById('btn-activate-policy');
    if (freshBtn) { freshBtn.disabled = false; freshBtn.innerHTML = `Activate ${tierName}`; }
  }
}

// Phase 3: Razorpay Checkout flow for policy activation
async function activatePolicy() {
  if (!selectedTier) return;
  const tierName = selectedTier.name;
  const btn = document.getElementById('btn-activate-policy');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Processing...';

  // Only use direct activation when demo payment mode is explicitly enabled.
  if (typeof Razorpay === 'undefined') {
    return handlePaymentGatewayFallback('policy activation', () => activatePolicyDirect(tierName), () => resetActivateButton(tierName));
  }

  try {
    // Step 1: Create Razorpay Order via backend
    const order = await api('/policies/create-order', {
      method: 'POST',
      body: JSON.stringify({ tier: tierName })
    });

    // Backend returned a mock order instead of a real Razorpay order.
    if (!order.key_id || order.order_id === 'MOCK_ORDER') {
      return handlePaymentGatewayFallback('policy activation', () => activatePolicyDirect(tierName), () => resetActivateButton(tierName));
    }

    // Step 2: Open Razorpay Checkout popup
    const options = {
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      name: 'Zynvaro',
      description: `${tierName} — Weekly Income Shield`,
      image: '/static/Zynvaro-bg-removed.png',
      order_id: order.order_id,
      handler: async function(response) {
        try {
          const result = await api('/policies/verify-payment', {
            method: 'POST',
            body: JSON.stringify({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
              tier: tierName,
            })
          });
          showPaymentSuccessModal({
            paymentId: response.razorpay_payment_id,
            amount: order.weekly_premium,
            tierName: tierName,
            policyNumber: result.policy_number,
          });
          hidePremiumBreakdown();
          const profile = await api('/auth/me');
          currentUser = profile;
          localStorage.setItem('zynvaro_user', JSON.stringify(profile));
          loadPolicyPage();
        } catch(e) {
          showPaymentRecoveryModal(response.razorpay_payment_id);
        }
      },
      modal: {
        ondismiss: function() {
          toast('Payment cancelled. Policy not activated.', 'info');
          const freshBtn = document.getElementById('btn-activate-policy');
          if (freshBtn) { freshBtn.disabled = false; freshBtn.innerHTML = `Activate ${tierName}`; }
        }
      },
      prefill: {
        name: currentUser?.full_name || '',
        contact: currentUser?.phone || '',
      },
      theme: { color: '#FF6B35' },
    };

    // Removed alert to allow the judge to intentionally fail the payment.
    const rzp = new Razorpay(options);
    rzp.on('payment.failed', function(resp) {
      toast('Razorpay Payment failed.', 'error');
      const freshBtn = document.getElementById('btn-activate-policy');
      if (freshBtn) { freshBtn.disabled = false; freshBtn.innerHTML = `Activate ${tierName}`; }
      
      // [DEMO PRIVILEGE] Prompt for forced activation
      setTimeout(() => {
          if (confirm("🚨 Razorpay Payment Failed (Test Mode)\n\n[DEMO PRIVILEGE]\nWould you like to use an Admin Override to bypass the payment gateway and force-activate this policy anyway?")) {
              activatePolicyDirect(tierName);
          }
      }, 500);
    });
    rzp.open();

  } catch(e) {
    toast('Failed to initiate payment: ' + e.message, 'error');
    const freshBtn = document.getElementById('btn-activate-policy');
    if (freshBtn) { freshBtn.disabled = false; freshBtn.innerHTML = `Activate ${tierName}`; }
  }
}

// Payment Success Modal
function showPaymentSuccessModal({paymentId, amount, tierName, policyNumber}) {
  document.getElementById('pay-amount').textContent = fmt(amount);
  document.getElementById('pay-id').textContent = paymentId;
  document.getElementById('pay-tier').textContent = tierName;
  document.getElementById('pay-policy').textContent = policyNumber || '—';
  document.getElementById('pay-modal-overlay').classList.remove('hidden');
  if (typeof launchConfetti === 'function') launchConfetti();
}

function closePaymentModal() {
  document.getElementById('pay-modal-overlay').classList.add('hidden');
  document.querySelectorAll('.confetti-piece').forEach(c => c.remove());
}

// Payment Recovery Modal (edge case: payment OK but verify failed)
function showPaymentRecoveryModal(paymentId) {
  document.getElementById('recovery-pay-id').textContent = paymentId;
  document.getElementById('pay-recovery-overlay').classList.remove('hidden');
}

function closeRecoveryModal() {
  document.getElementById('pay-recovery-overlay').classList.add('hidden');
}

async function cancelPolicy() {
  if (!activePolicyData) return;
  if (!confirm('Cancel your active policy? You will lose income protection.')) return;
  try {
    await api(`/policies/${activePolicyData.id}`, { method: 'DELETE' });
    toast('Policy cancelled.', 'info');
    activePolicyData = null;
  } catch(e) {
    toast(e.message, 'error');
  }
  // Always refresh — sync UI with actual server state even on error
  loadPolicyPage();
}

async function renewPolicyDirect() {
  try {
    const data = await api('/policies/renew', { method: 'POST' });
    toast(`Policy renewed! New expiry: ${new Date(data.end_date).toLocaleDateString('en-IN')}`, 'success');
  } catch(e) {
    toast(e.message || 'Renewal failed', 'error');
  }
  loadPolicyPage();
}

async function renewPolicy() {
  if (!activePolicyData) return;
  const tierName = activePolicyData.tier;

  if (typeof Razorpay === 'undefined') {
    return handlePaymentGatewayFallback('policy renewal', renewPolicyDirect);
  }

  try {
    const order = await api('/policies/renew-order', { method: 'POST' });
    if (!order.key_id || order.order_id === 'MOCK_ORDER') {
      return handlePaymentGatewayFallback('policy renewal', renewPolicyDirect);
    }

    const options = {
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      name: 'Zynvaro',
      description: `${tierName} — Weekly Renewal`,
      image: '/static/Zynvaro-bg-removed.png',
      order_id: order.order_id,
      handler: async function(response) {
        try {
          const result = await api('/policies/verify-renewal', {
            method: 'POST',
            body: JSON.stringify({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
              tier: tierName,
            })
          });
          showPaymentSuccessModal({
            paymentId: response.razorpay_payment_id,
            amount: order.weekly_premium,
            tierName: tierName + ' (Renewed)',
            policyNumber: result.policy_number,
          });
          loadPolicyPage();
        } catch(e) {
          showPaymentRecoveryModal(response.razorpay_payment_id);
        }
      },
      modal: { ondismiss: function() { toast('Renewal cancelled.', 'info'); } },
      prefill: { name: currentUser?.full_name || '', contact: currentUser?.phone || '' },
      theme: { color: '#FF6B35' },
    };
    // Removed alert to allow intentional failure.
    const rzp = new Razorpay(options);
    rzp.on('payment.failed', function(resp) {
      toast('Payment failed: ' + (resp.error?.description || 'Unknown'), 'error');
      
      // [DEMO PRIVILEGE] Prompt for forced renewal
      setTimeout(() => {
          if (confirm("🚨 Razorpay Payment Failed (Test Mode)\n\n[DEMO PRIVILEGE]\nWould you like to use an Admin Override to bypass the payment gateway and force-renew this policy anyway?")) {
              renewPolicyDirect();
          }
      }, 500);
    });
    rzp.open();
  } catch(e) {
    toast('Renewal failed: ' + e.message, 'error');
  }
}

// ════════════════════════════════════════════════════════════════
// TRIGGERS PAGE
// ════════════════════════════════════════════════════════════════
async function loadTriggerTypes() {
  try {
    const types = await api('/triggers/types');
    document.getElementById('trigger-types-list').innerHTML = types.map(t => `
      <div class="trigger-item">
        <div class="trigger-icon-wrap">${triggerIcon(t.trigger_type)}</div>
        <div style="flex:1;min-width:0;">
          <div class="list-title">${esc(t.trigger_type)}</div>
          <div class="list-sub">
            Threshold: <b style="color:var(--orange)">${t.threshold} ${esc(t.unit)}</b>
            · ${esc(t.source_primary)}
          </div>
        </div>
      </div>
    `).join('');
  } catch(e) {
    document.getElementById('trigger-types-list').innerHTML =
      '<p class="text-muted text-sm" style="padding:8px 0;">Failed to load trigger types.</p>';
  }
}

async function loadTriggerEvents() {
  try {
    const events = await api(currentCityTriggerHistoryPath(15));
    const el = document.getElementById('trigger-events-list');
    if (!events.length) {
      el.innerHTML = `<p class="text-muted text-sm" style="padding:8px 0;">No saved events yet in ${esc(currentWorkerCity())}. Use Live Check above or Simulate below.</p>`;
      return;
    }
    el.innerHTML = events.map(e => `
      <div class="trigger-item">
        <div class="trigger-icon-wrap">${triggerIcon(e.trigger_type)}</div>
        <div style="flex:1;min-width:0;">
          <div class="flex items-center gap-2 mb-1" style="flex-wrap:wrap;">
            <b class="text-sm">${esc(e.trigger_type)}</b>
            <span class="trigger-badge sev-${e.severity}">${e.severity}</span>
            ${e.is_validated ? '<span class="trigger-badge" style="background:var(--green-d);color:var(--green);">✅ Verified</span>' : ''}
          </div>
          <p class="text-xs text-muted" style="line-height:1.4;">${esc(e.description || '')}</p>
          ${signalBadgeRow({
            confidence: e.confidence_score,
            sourcePrimary: e.source_primary,
            sourceSecondary: e.source_secondary,
            sourceLog: e.source_log,
            confidenceLabel: 'Signal',
          })}
          <div class="flex gap-3 mt-1 text-xs text-muted" style="flex-wrap:wrap;">
            <span>📍 ${esc(e.city)}</span>
            <span>📊 ${e.measured_value} ${esc(e.unit)}</span>
            <span>🕐 ${timeAgo(e.detected_at)}</span>
          </div>
        </div>
      </div>
    `).join('');
  } catch(e) {
    const el = document.getElementById('trigger-events-list');
    if (el) el.innerHTML = '<p class="text-muted text-sm" style="padding:8px 0;">Failed to load events.</p>';
  }
}

// ════════════════════════════════════════════════════════════════
// WHAT-IF COMPARISON (real value vs threshold vs simulated)
// ════════════════════════════════════════════════════════════════
const SIM_VALUES = {
  'Heavy Rainfall': { sim: 72.5, unit: 'mm/24hr', field: 'rain' },
  'Extreme Rain / Flooding': { sim: 210, unit: 'mm/24hr', field: 'rain' },
  'Severe Heatwave': { sim: 46.2, unit: '°C', field: 'temp' },
  'Hazardous AQI': { sim: 485, unit: 'AQI', field: 'aqi' },
  'Platform Outage': { sim: 20, unit: 'min down', field: 'platform' },
  'Civil Disruption': { sim: 6, unit: 'hours', field: 'civil' },
};

const SIM_THRESHOLDS = {
  'Heavy Rainfall': 64.5,
  'Extreme Rain / Flooding': 204.5,
  'Severe Heatwave': 45,
  'Hazardous AQI': 400,
  'Platform Outage': 15,
  'Civil Disruption': 4,
};

let _lastSimConditions = null;

async function updateSimComparison() {
  const trigType = document.getElementById('sim-trigger-type').value;
  const city = document.getElementById('sim-city').value;
  const panel = document.getElementById('sim-comparison');
  if (!panel) return;

  panel.style.display = 'block';
  panel.innerHTML = `<p class="text-muted text-sm">Fetching current ${esc(trigType)} reading for ${esc(city)}...</p>`;

  try {
    const data = await api(conditionsApiPath(city, currentWorkerPlatform()));
    _lastSimConditions = data;
    const cfg = SIM_VALUES[trigType] || { sim: 100, unit: '', field: '' };
    const threshold = SIM_THRESHOLDS[trigType] || 100;

    // Extract current value based on trigger type
    let current = 0;
    let sourceLabel = '';
    const f = cfg.field;
    if (f === 'rain') {
      current = data.weather?.rain_24h_est_mm || 0;
      sourceLabel = data.sources?.weather || 'API';
    } else if (f === 'temp') {
      current = data.weather?.temperature_c || 0;
      sourceLabel = data.sources?.weather || 'API';
    } else if (f === 'aqi') {
      current = data.aqi?.value || 0;
      sourceLabel = data.sources?.aqi || 'API';
    } else if (f === 'platform') {
      current = data.platform_status?.status === 'DOWN' ? 20 : 0;
      sourceLabel = data.sources?.platform || 'API';
    } else if (f === 'civil') {
      current = data.civil_disruption?.active ? 6 : 0;
      sourceLabel = data.sources?.civil || 'API';
    }

    const pct = Math.min(100, (current / threshold) * 100);
    const gap = threshold - current;
    const isLive = sourceLabel.toLowerCase().includes('live');

    panel.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;">
        <span style="font-size:12px;font-weight:700;color:var(--text);">📊 Real vs Simulated</span>
        ${isLive
          ? '<span style="font-size:9px;padding:2px 6px;border-radius:50px;background:var(--green-d);color:var(--green);font-weight:700;">● LIVE</span>'
          : '<span style="font-size:9px;padding:2px 6px;border-radius:50px;background:var(--card3);color:var(--muted);font-weight:700;">○ MOCK</span>'
        }
      </div>

      <div style="display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:center;margin-bottom:10px;">
        <div style="text-align:center;">
          <div style="font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;">Current</div>
          <div style="font-size:20px;font-weight:800;color:var(--blue);">${current}</div>
          <div style="font-size:10px;color:var(--muted);">${cfg.unit}</div>
        </div>
        <div style="text-align:center;">
          <div style="font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;">Threshold</div>
          <div style="font-size:20px;font-weight:800;color:var(--yellow);">${threshold}</div>
          <div style="font-size:10px;color:var(--muted);">${cfg.unit}</div>
        </div>
        <div style="text-align:center;">
          <div style="font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;">Simulated</div>
          <div style="font-size:20px;font-weight:800;color:var(--red);">${cfg.sim}</div>
          <div style="font-size:10px;color:var(--muted);">${cfg.unit}</div>
        </div>
      </div>

      <div style="height:4px;background:var(--card3);border-radius:4px;overflow:hidden;margin-bottom:4px;">
        <div style="height:100%;width:${pct}%;background:${pct > 80 ? 'var(--red)' : pct > 50 ? 'var(--yellow)' : 'var(--blue)'};border-radius:4px;transition:width .3s;"></div>
      </div>
      <div style="font-size:10px;color:var(--muted);">
        ${pct.toFixed(0)}% of threshold · ${gap > 0 ? `${gap.toFixed(1)} ${cfg.unit} below trigger` : '<span style="color:var(--red);font-weight:700;">THRESHOLD EXCEEDED</span>'}
      </div>
      <div style="font-size:9px;color:var(--muted);margin-top:6px;line-height:1.4;font-style:italic;">
        Simulation will override the current ${current} ${cfg.unit} → ${cfg.sim} ${cfg.unit} to demonstrate the zero-touch claim pipeline.
      </div>
    `;
  } catch(e) {
    panel.innerHTML = `<p class="text-muted text-sm">Could not fetch current reading. Simulation will still work.</p>`;
  }
}

let _simulatedBypassConfirmed = false;

async function simulateTrigger() {
  const trigType = document.getElementById('sim-trigger-type').value;
  const city     = document.getElementById('sim-city').value;
  const btn = document.getElementById('btn-simulate-trigger');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Simulating threshold crossing...';
  const requestStartedAt = Date.now();
  try {
    const res = await api('/triggers/simulate', {
      method: 'POST',
      body: JSON.stringify({ trigger_type: trigType, city, bypass_gate: _simulatedBypassConfirmed })
    });
    
    _simulatedBypassConfirmed = false; // Reset after successful bypass

    // Show what-if comparison from the response
    if (res.current_reading) {
      const cfg = SIM_VALUES[trigType] || {};
      toast(`🚨 ${trigType} simulated in ${city}! Current: ${res.current_reading.value} → Simulated: ${res.measured_value} ${res.unit}`, 'info');
    } else {
      toast(`🚨 ${trigType} simulated in ${city}! Processing claims...`, 'info');
    }
    loadTriggerEvents();

    // Poll for the new claim with retry — background task may take 1–5s
    (async () => {
      let payoutAmt = 0;
      let claimNum = 'CLM-AUTO-' + Math.random().toString(36).substr(2,6).toUpperCase();
      let matchedClaim = null;
      const delays = [1500, 2000, 3000]; // retry at 1.5s, 3.5s, 6.5s
      for (const delay of delays) {
        await new Promise(r => setTimeout(r, delay));
        if (!document.getElementById('page-triggers').classList.contains('active')) return;
        try {
          const claims = await api('/claims/');
          matchedClaim = claims.find(c =>
            c.trigger_type === trigType &&
            c.trigger_city === city &&
            new Date(c.created_at).getTime() >= requestStartedAt - 3000
          );
          if (matchedClaim) {
            payoutAmt = matchedClaim.paid_at ? matchedClaim.payout_amount : 0;
            claimNum = matchedClaim.claim_number;
            break;
          }
        } catch(_) {}
      }
      if (!document.getElementById('page-triggers').classList.contains('active')) return;
      if (matchedClaim) {
        showWAModal(trigType, city, payoutAmt, claimNum);
      } else {
        const reason = res.requester_eligibility_reason
          || `No claim was created for your account because your current location or platform does not match ${city}.`;
        toast(reason, res.requester_eligible === false ? 'info' : 'error');
      }
    })();

  } catch(e) {
    if (e.message && e.message.includes('|bypass_required')) {
      const msg = e.message.split('|')[0];
      toast(`🚨 ${msg}`, 'error');
      
      let overrideBtn = document.getElementById('btn-demo-override');
      if (!overrideBtn) {
        overrideBtn = document.createElement('button');
        overrideBtn.id = 'btn-demo-override';
        overrideBtn.className = 'btn btn-full';
        overrideBtn.style = 'background:#1d1f27;color:var(--yellow);margin-top:8px;border:1px dashed var(--yellow);';
        overrideBtn.innerHTML = '⚠️ DEMO PRIVILEGE: Force Override & Simulate';
        overrideBtn.onclick = function() {
            _simulatedBypassConfirmed = true;
            this.style.display = 'none';
            simulateTrigger();
        };
        const simBtn = document.getElementById('btn-simulate-trigger');
        simBtn.parentNode.insertBefore(overrideBtn, simBtn.nextSibling);
      }
      overrideBtn.style.display = 'block';
    } else {
      toast(e.message, 'error');
    }
  } finally {
    btn.disabled = false; btn.innerHTML = '⚡ Simulate Threshold Crossing';
  }
}

// ════════════════════════════════════════════════════════════════
// CLAIMS PAGE
// ════════════════════════════════════════════════════════════════
let _allClaims = [];

async function loadClaimsPage() {
  try {
    const [claims, stats] = await Promise.all([
      api('/claims/'),
      api('/claims/stats'),
    ]);
    _allClaims = claims;

    document.getElementById('cs-total').textContent    = stats.total_claims;
    document.getElementById('cs-approved').textContent = stats.auto_approved;
    document.getElementById('cs-payout').textContent   = fmt(stats.total_payout_inr);
    document.getElementById('cs-score').textContent    = fmtScore(stats.avg_authenticity_score);

    // Reset filter to "all"
    document.querySelectorAll('#claims-filter-chips .chip').forEach(c => c.classList.remove('active'));
    const allChip = document.querySelector('#claims-filter-chips .chip');
    if (allChip) allChip.classList.add('active');

    const el = document.getElementById('claims-list');
    if (!claims.length) {
      el.innerHTML = `
        <div class="card text-center" style="padding:32px 16px;">
          <div class="clay-icon" style="margin:0 auto 16px;width:72px;height:72px;border-radius:20px;background:var(--card3);">
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="22 12 16 12 14 15 10 15 8 12 2 12"></polyline>
              <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path>
            </svg>
          </div>
          <p style="font-weight:600;margin-bottom:6px;font-size:16px;">No claims yet</p>
          <p class="text-muted text-sm" style="line-height:1.5;">When a parametric trigger fires in your city,<br>a claim is auto-generated and paid instantly.</p>
          <button class="btn btn-ghost btn-sm mt-3" style="display:inline-flex;align-items:center;gap:6px;" onclick="navTo('triggers')">
            <i data-lucide="zap" style="width:14px;height:14px;"></i> Simulate a Trigger
          </button>
        </div>`;
      setTimeout(refreshIcons, 50);
      return;
    }
    el.innerHTML = claims.map(c => renderClaimCard(c)).join('');
  } catch(e) {
    toast('Error loading claims: ' + e.message, 'error');
  }
}

function filterClaims(status, chipEl) {
  document.querySelectorAll('#claims-filter-chips .chip').forEach(c => c.classList.remove('active'));
  if (chipEl) chipEl.classList.add('active');
  const filtered = status === 'all' ? _allClaims : _allClaims.filter(c => c.status === status);
  const el = document.getElementById('claims-list');
  if (!filtered.length) {
    el.innerHTML = '<p class="text-muted text-sm" style="padding:16px 0 8px;">No claims with this status.</p>';
    return;
  }
  el.innerHTML = filtered.map(c => renderClaimCard(c)).join('');
}

function renderClaimCard(c, isAdmin = false) {
  const statusClass = {
    'auto_approved': 'status-auto-approved', 'paid': 'status-paid',
    'pending_review': 'status-pending', 'manual_review': 'status-manual',
    'rejected': 'status-rejected',
  }[c.status] || 'status-pending';

  const sc = scoreColor(c.authenticity_score);
  const reviewable = isAdmin && (c.status === 'pending_review' || c.status === 'manual_review');

  // Risk tier badge
  const riskColors = { LOW: '#22c55e', MEDIUM: '#f59e0b', HIGH: '#f97316', CRITICAL: '#ef4444' };
  const riskTier = c.risk_tier || (c.authenticity_score >= 75 ? 'LOW' : c.authenticity_score >= 45 ? 'MEDIUM' : c.authenticity_score >= 20 ? 'HIGH' : 'CRITICAL');
  const riskColor = riskColors[riskTier] || '#999';

  // Fraud validation checks (6 modules)
  const checks = [
    { label: 'GPS',      valid: c.gps_valid !== false },
    { label: 'Shift',    valid: c.shift_valid !== false },
    { label: 'Weather',  valid: c.weather_cross_valid !== false },
    { label: 'Velocity', valid: c.velocity_valid !== false },
    { label: 'Pattern',  valid: c.activity_valid !== false },
    { label: 'Dedup',    valid: c.cross_source_valid !== false },
  ];

  // GPS distance display
  const gpsDistText = c.gps_distance_km != null
    ? `${c.gps_distance_km.toFixed(1)}km from zone`
    : '';

  // ML fraud probability
  const mlText = c.ml_fraud_probability != null
    ? `ML: ${(c.ml_fraud_probability * 100).toFixed(0)}% risk`
    : '';
  const confidence = Number.isFinite(Number(c.trigger_confidence_score)) ? Number(c.trigger_confidence_score) : null;
  const confidenceColor = confidence == null ? 'var(--muted)' : confidence >= 80 ? '#22c55e' : confidence >= 50 ? '#f59e0b' : '#ef4444';
  const measuredText = c.trigger_measured_value != null
    ? `${c.trigger_measured_value} ${c.trigger_unit || ''}`.trim()
    : '—';
  const thresholdText = c.trigger_threshold_value != null
    ? `${c.trigger_threshold_value} ${c.trigger_unit || ''}`.trim()
    : '—';
  const triggerWindow = c.trigger_detected_at
    ? `${fmtDateTime(c.trigger_detected_at)}${c.trigger_expires_at ? ' to ' + fmtDateTime(c.trigger_expires_at) : ''}`
    : '—';
  const zoneText = c.gps_valid === false
    ? 'Mismatch'
    : gpsDistText
      ? `Matched (${gpsDistText})`
      : 'Matched';
  const sourceLog = c.source_log
    || [c.trigger_source_primary ? `Primary: ${c.trigger_source_primary}` : '', c.trigger_source_secondary ? `Secondary: ${c.trigger_source_secondary}` : '', c.trigger_is_validated === true ? 'Cross-source validation: PASSED' : c.trigger_is_validated === false ? 'Cross-source validation: PENDING' : ''].filter(Boolean).join('\n')
    || 'Source hierarchy was not captured for this claim.';
  const recentActivityLabel = c.recent_activity_valid === false ? 'Blocked' : 'Eligible';
  const recentActivityColor = c.recent_activity_valid === false ? '#ef4444' : '#22c55e';
  const recentActivityText = c.recent_activity_reason
    || (c.recent_activity_age_hours != null
      ? `Last active ${Number(c.recent_activity_age_hours).toFixed(1)}h before payout review.`
      : 'Recent activity check pending.');
  const appealActive = !!(c.appeal_status && c.appeal_status !== 'none');
  const appealWindowOpen = !isAdmin && !appealActive && ((Date.now() - new Date(c.created_at).getTime()) <= (48 * 60 * 60 * 1000));
  const payoutRule = c.policy_tier
    ? `${fmt(c.payout_amount)} under ${c.policy_tier}`
    : fmt(c.payout_amount);

  return `
    <div class="claim-card ${c.paid_at ? 'payout-success' : ''}" id="claim-card-${c.id}">
      <div class="flex items-center gap-2" style="margin-bottom:8px;">
        <div class="list-avatar" style="width:34px;height:34px;font-size:15px;background:var(--card2);">
          ${triggerIcon(c.trigger_type || '⚡')}
        </div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;font-weight:600;">${esc(c.trigger_type || 'Disruption')}</div>
          <div class="text-xs text-muted">#${esc(c.claim_number)} · ${timeAgo(c.created_at)}</div>
        </div>
        ${c.is_simulated ? '<span style="font-size:9px;padding:2px 7px;border-radius:50px;background:rgba(249,115,22,0.15);color:#f97316;font-weight:700;letter-spacing:.3px;">SIMULATED</span>' : ''}
        <span class="claim-status-badge ${statusClass}">${c.status.replace(/_/g,' ').toUpperCase()}</span>
      </div>

      <div class="claim-info-grid">
        <div>Payout <b class="text-green">${fmt(c.payout_amount)}</b></div>
        <div>City <b>${esc(c.trigger_city || '—')}</b></div>
      </div>

      <div style="margin-top:6px;">
        <div class="flex justify-between text-xs" style="margin-bottom:3px;">
          <span class="text-muted">Auth Score</span>
          <span style="color:${sc};font-weight:700;">${fmtScore(c.authenticity_score)}</span>
        </div>
        <div class="score-bar">
          <div class="score-fill" style="width:${c.authenticity_score}%;background:${sc}"></div>
        </div>
        <div class="flex justify-between text-xs" style="margin-top:3px;">
          <span style="color:${riskColor};font-weight:600;font-size:10px;letter-spacing:0.5px;">${riskTier} RISK</span>
          <span class="text-muted" style="font-size:10px;">${mlText}</span>
        </div>
      </div>

      <!-- Fraud Validation Checks (6 modules) -->
      <div style="display:flex;gap:4px;margin-top:8px;flex-wrap:wrap;">
        ${checks.map(ch => `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${ch.valid ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.15)'};color:${ch.valid ? '#22c55e' : '#ef4444'};font-weight:500;">${ch.valid ? '✓' : '✗'} ${ch.label}</span>`).join('')}
      </div>

      ${gpsDistText ? `<div class="text-xs text-muted" style="margin-top:4px;">📍 ${gpsDistText}</div>` : ''}

      <!-- Explainability & Trust Module -->
      <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:6px; padding:10px; margin-top:10px; margin-bottom:6px;">
        <div style="font-size:11px; font-weight:700; color:var(--text); margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;">
           <span>Payout Explainability</span>
           <span style="font-size:10px; padding:2px 6px; border-radius:4px; background:rgba(255,255,255,0.1); color:${confidenceColor}">${confidence == null ? 'Unscored' : confidence.toFixed(0) + '% Trigger Confidence'}</span>
        </div>

        <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;font-size:10px;margin-bottom:8px;">
          <div><div style="color:var(--muted);text-transform:uppercase;letter-spacing:.4px;">Measured</div><div style="font-weight:600;">${esc(measuredText)}</div></div>
          <div><div style="color:var(--muted);text-transform:uppercase;letter-spacing:.4px;">Threshold</div><div style="font-weight:600;">${esc(thresholdText)}</div></div>
          <div><div style="color:var(--muted);text-transform:uppercase;letter-spacing:.4px;">Zone Match</div><div style="font-weight:600;color:${c.gps_valid === false ? '#ef4444' : '#22c55e'};">${esc(zoneText)}</div></div>
          <div><div style="color:var(--muted);text-transform:uppercase;letter-spacing:.4px;">Payout Rule</div><div style="font-weight:600;">${esc(payoutRule)}</div></div>
        </div>

        <div style="font-size:10px;color:var(--muted);margin-bottom:8px;line-height:1.45;">
          <b style="color:var(--text);">Event Window:</b> ${esc(triggerWindow)}<br>
          <b style="color:var(--text);">Sources:</b> ${esc(c.trigger_source_primary || 'Unknown')}${c.trigger_source_secondary ? ' + ' + esc(c.trigger_source_secondary) : ''}${c.trigger_is_validated === true ? ' · validated' : c.trigger_is_validated === false ? ' · pending validation' : ''}
        </div>

        <div style="font-size:10px;color:var(--muted);margin-bottom:8px;line-height:1.45;">
          <b style="color:var(--text);">Recent Activity Gate:</b>
          <span style="color:${recentActivityColor};font-weight:700;">${esc(recentActivityLabel)}</span>
          Â· ${esc(recentActivityText)}
        </div>
        <div style="font-family:monospace; font-size:10px; color:var(--muted); line-height:1.5; white-space:pre-wrap; background:#000; padding:6px; border-radius:4px;">${esc(sourceLog)}</div>

        ${c.appeal_reason ? `<div style="margin-top:8px;font-size:10px;line-height:1.45;color:var(--muted);"><b style="color:var(--text);">Appeal Note:</b> ${esc(c.appeal_reason)}</div>` : ''}
        ${appealWindowOpen ? 
          `<button style="width:100%; margin-top:8px; background:rgba(239,68,68,0.1); color:#ef4444; border:1px solid rgba(239,68,68,0.2); padding:6px; border-radius:4px; font-size:10px; font-weight:600; cursor:pointer;" onclick="initiateAppeal(${c.id})">Request Data Review (48h Window)</button>` 
          : ''}
        ${appealActive ? 
          `<div style="margin-top:8px; font-size:10px; color:#f59e0b; font-weight:600; text-transform:uppercase; display:flex; align-items:center; gap:4px;">
             <span class="spinner" style="width:10px;height:10px;border-width:1px;"></span> Appeal Status: ${c.appeal_status.replace('_', ' ')}
           </div>` 
          : ''}
      </div>

      ${c.paid_at ? `<div class="text-xs text-green" style="margin-top:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
        <span>${c.payout_gateway === 'razorpay' ? '🟢' : c.payout_status === 'settled' ? '🟢' : c.payout_status === 'failed' ? '🔴' : '🟡'}</span>
        <span>Paid ${timeAgo(c.paid_at)}</span>
        <span style="padding:1px 6px;border-radius:4px;font-size:9px;font-weight:700;letter-spacing:0.3px;${c.payout_gateway === 'razorpay' ? 'background:rgba(56,132,244,0.15);color:#3884f4;' : 'background:rgba(255,255,255,0.08);color:var(--muted);'}">${c.payout_gateway === 'razorpay' ? 'RAZORPAY TEST' : 'MOCK'}</span>
        ${c.payout_reference ? `<span style="font-family:monospace;font-size:10px;color:var(--muted);">${esc(c.payout_reference_label || 'Reference')}: ${esc(c.payout_reference)}</span>` : `<span style="font-size:10px;color:var(--muted);">${esc(c.payment_ref || '')}</span>`}
      </div>` : ''}

      ${c.paid_at ? signalBadgeRow({
        confidence,
        sourcePrimary: c.trigger_source_primary,
        sourceSecondary: c.trigger_source_secondary,
        sourceLog,
        confidenceLabel: 'Payout Evidence',
      }) : ''}
      ${c.payout_note ? `<div class="text-xs text-muted" style="margin-top:4px;line-height:1.45;">${esc(c.payout_note)}</div>` : ''}

      ${c.fraud_flags ? `<div class="claim-flags">${esc(c.fraud_flags)}</div>` : ''}

      ${c.is_simulated
        ? `<div style="margin-top:8px;padding:6px 10px;border-radius:8px;background:rgba(249,115,22,0.08);border:1px solid rgba(249,115,22,0.15);font-size:10px;line-height:1.5;color:#f97316;font-style:italic;">
            ⚠️ This claim was generated from a <b>simulated trigger</b>. In production, real weather/AQI data triggers payouts autonomously every 15 min.
           </div>`
        : (c.trigger_type ? `<div style="margin-top:8px;padding:6px 10px;border-radius:8px;background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.12);font-size:10px;line-height:1.5;color:#22c55e;font-style:italic;">
            ✅ Generated from a <b>live disruption</b> detected via parametric monitoring. Claim disbursement references may still be test/demo references unless a real payout rail is enabled.
           </div>` : '')}

      ${reviewable ? `
        <div class="flex gap-2" style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px;">
          <button class="btn btn-success btn-sm" style="flex:1;padding:7px 12px;font-size:12px;" onclick="adminApproveClaim(${c.id})">Approve</button>
          <button class="btn btn-danger btn-sm" style="flex:1;padding:7px 12px;font-size:12px;" onclick="adminRejectClaim(${c.id})">Deny</button>
        </div>` : ''}
    </div>
  `;
}

function filterAdminClaims(mode, chipEl) {
  // Update chip active states
  document.querySelectorAll('#admin-claim-filter-chips .chip').forEach(c => c.classList.remove('active'));
  if (chipEl) chipEl.classList.add('active');
  else {
    const firstChip = document.querySelector('#admin-claim-filter-chips .chip');
    if (firstChip) firstChip.classList.add('active');
  }

  const all = window._allAdminClaims || [];
  const filtered = mode === 'simulated' ? all.filter(c => c.is_simulated)
                 : mode === 'organic'   ? all.filter(c => !c.is_simulated)
                 : all;

  const el = document.getElementById('admin-claims-list');
  if (!filtered.length) {
    const msg = mode === 'simulated' ? 'No simulated claims yet. Fire a What-If Scenario to generate one.'
              : mode === 'organic'   ? 'No organic (live) claims yet. These appear when real conditions exceed thresholds.'
              : 'No claims recorded yet.';
    el.innerHTML = `<p class="text-muted text-sm" style="padding:8px 0;">${msg}</p>`;
    return;
  }
  el.innerHTML = filtered.map(c => renderClaimCard(c, true)).join('');
}

async function adminApproveClaim(claimId) {
  try {
    await api(`/claims/${claimId}/status`, { method: 'POST', body: JSON.stringify({status: 'paid'}) });
    toast('Claim approved & payout initiated', 'success');
    loadAdminPage();
  } catch (e) {
    toast('Approve failed: ' + e.message, 'error');
  }
}

async function adminRejectClaim(claimId) {
  try {
    await api(`/claims/${claimId}/status`, { method: 'POST', body: JSON.stringify({status: 'rejected'}) });
    toast('Claim rejected successfully', 'info');
    loadAdminPage();
  } catch (e) {
    toast('Reject failed: ' + e.message, 'error');
  }
}

// User Appeal Workflow
async function initiateAppeal(claimId) {
  if (!confirm('Are you sure you want to appeal this claim computation? Data appeals must be backed by accurate localized evidence within 48 hours.')) return;

  const reason = prompt('Please provide a short reason for appealing (for example: "Rain impact was local to my zone but the threshold source appears delayed").');
  if (!reason) return;

  try {
    toast('Submitting appeal request...', 'info');
    const updated = await api(`/claims/${claimId}/appeal`, { method: 'POST', body: JSON.stringify({ reason }) });
    const idx = window._allClaims.findIndex(x => x.id === claimId);
    if (idx >= 0) window._allClaims[idx] = updated;
    toast('Appeal queued successfully. The claim now shows a persisted review status.', 'success');
    loadClaimsPage();
  } catch (e) {
    toast('Appeal failed: ' + e.message, 'error');
  }
}


async function refreshAdminStats() {
  try {
    const stats = await api('/claims/admin/stats');
    document.getElementById('admin-stats-grid').innerHTML = `
      <div class="admin-stat"><div class="val text-blue">${stats.total_workers}</div><div class="lbl">Workers</div></div>
      <div class="admin-stat"><div class="val text-green">${stats.active_policies}</div><div class="lbl">Active Policies</div></div>
      <div class="admin-stat"><div class="val text-orange">${fmt(stats.weekly_premium_collection_inr)}</div><div class="lbl">Premium/week</div></div>
      <div class="admin-stat"><div class="val">${stats.total_claims}</div><div class="lbl">Total Claims</div></div>
      <div class="admin-stat"><div class="val text-green">${stats.auto_approved_claims}</div><div class="lbl">Auto-Approved</div></div>
      <div class="admin-stat"><div class="val text-red">${fmt(stats.total_payout_inr)}</div><div class="lbl">Total Payouts</div></div>
    `;
    const lrEl = document.getElementById('loss-ratio-val');
    lrEl.textContent = stats.loss_ratio_pct.toFixed(1) + '%';
    lrEl.style.color = stats.loss_ratio_pct < 65 ? 'var(--green)' : stats.loss_ratio_pct < 80 ? 'var(--yellow)' : 'var(--red)';
  } catch(_) {}
}

// ════════════════════════════════════════════════════════════════
// ADMIN PAGE
// ════════════════════════════════════════════════════════════════
async function loadAdminPage() {
  try {
    const [stats, allClaims, workers, cityStats, mlInfo, forecast, transactions] = await Promise.all([
      api('/claims/admin/stats'),
      api('/claims/admin/all?limit=20'),
      api('/claims/admin/workers').catch(() => []),
      api('/analytics/cities').catch(() => []),
      api('/policies/ml-model-info').catch(() => null),
      api('/analytics/forecast').catch(() => null),
      api('/claims/admin/transactions?limit=30').catch(() => []),
    ]);

    // Stats grid
    document.getElementById('admin-stats-grid').innerHTML = `
      <div class="admin-stat"><div class="val text-blue">${stats.total_workers}</div><div class="lbl">Workers</div></div>
      <div class="admin-stat"><div class="val text-green">${stats.active_policies}</div><div class="lbl">Active Policies</div></div>
      <div class="admin-stat"><div class="val text-orange">${fmt(stats.weekly_premium_collection_inr)}</div><div class="lbl">Premium/week</div></div>
      <div class="admin-stat"><div class="val">${stats.total_claims}</div><div class="lbl">Total Claims</div></div>
      <div class="admin-stat"><div class="val text-green">${stats.auto_approved_claims}</div><div class="lbl">Auto-Approved</div></div>
      <div class="admin-stat"><div class="val text-red">${fmt(stats.total_payout_inr)}</div><div class="lbl">Total Payouts</div></div>
    `;

    // Loss ratio
    const lrEl = document.getElementById('loss-ratio-val');
    lrEl.textContent = stats.loss_ratio_pct.toFixed(1) + '%';
    lrEl.style.color = stats.loss_ratio_pct < 65 ? 'var(--green)' : stats.loss_ratio_pct < 80 ? 'var(--yellow)' : 'var(--red)';

    // Trigger breakdown
    const tb = stats.claims_by_trigger;
    document.getElementById('admin-trigger-breakdown').innerHTML = Object.keys(tb).length ?
      Object.entries(tb).map(([k,v]) => `
        <div class="list-item">
          <div class="list-avatar" style="width:38px;height:38px;font-size:18px;">${triggerIcon(k)}</div>
          <div class="list-body"><div class="list-title">${k}</div></div>
          <div class="list-right"><div class="list-val">${v}</div><div class="list-val-sub">claims</div></div>
        </div>
      `).join('') :
      '<p class="text-muted text-sm" style="padding:8px 0;">No claims yet. Simulate a trigger.</p>';

    // Workers — mobile list
    const wrapEl = document.getElementById('admin-workers-tbody-wrap');
    if (workers.length) {
      wrapEl.innerHTML = workers.map(w => {
        const tierColor = w.active_tier === 'Pro Armor'      ? 'var(--purple)' :
                          w.active_tier === 'Standard Guard'  ? 'var(--blue)'   :
                          w.active_tier                        ? 'var(--green)'  : 'var(--muted)';
        const riskCol   = w.zone_risk_score > 0.7 ? 'var(--red)' :
                          w.zone_risk_score > 0.45 ? 'var(--yellow)' : 'var(--green)';
        const initials  = w.full_name.split(' ').slice(0,2).map(n=>n[0]).join('');
        return `
          <div class="worker-row">
            <div class="worker-avatar" style="background:${tierColor}20;color:${tierColor};">${esc(initials)}</div>
            <div style="flex:1;min-width:0;">
              <div style="font-weight:600;font-size:14px;">${esc(w.full_name)}</div>
              <div style="font-size:12px;color:var(--muted);">${esc(w.city)} · ${esc(w.platform)}</div>
              ${w.active_tier ? `<span style="font-size:11px;color:${tierColor};font-weight:700;">${esc(w.active_tier)}</span>` : ''}
            </div>
            <div style="text-align:right;">
              <div style="font-weight:700;font-size:14px;">${w.active_tier ? fmt(w.weekly_premium) : '—'}</div>
              <div style="font-size:11px;color:${riskCol};">${(w.zone_risk_score*100).toFixed(0)}% risk</div>
              <div style="font-size:11px;color:var(--muted);">${w.claim_history_count} claims</div>
            </div>
          </div>
        `;
      }).join('');

      // Also populate legacy hidden tbody for compat
      document.getElementById('admin-workers-tbody').innerHTML = workers.map(w => `<tr><td>${esc(w.full_name)}</td></tr>`).join('');
    } else {
      wrapEl.innerHTML = '<p class="text-muted text-sm" style="padding:8px 0;">No workers yet.</p>';
    }

    // City performance analytics
    const cityEl = document.getElementById('admin-city-analytics');
    if (cityStats && cityStats.length) {
      cityEl.innerHTML = cityStats.map(c => {
        const lrColor = c.loss_ratio < 65 ? 'var(--green)' : c.loss_ratio < 80 ? 'var(--yellow)' : 'var(--red)';
        const avgScore = c.avg_authenticity_score != null ? `${c.avg_authenticity_score.toFixed(0)}` : '—';
        return `
          <div class="list-item">
            <div class="clay-icon clay-sm clay-blue">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4"/><path d="M10 10h4"/><path d="M10 14h4"/><path d="M10 18h4"/></svg>
            </div>
            <div class="list-body">
              <div class="list-title">${esc(c.city)}</div>
              <div class="list-sub">${c.policies_issued} policies · ${c.claims_total} claims · Auth ${avgScore}</div>
            </div>
            <div class="list-right">
              <div class="list-val" style="color:${lrColor}">${c.loss_ratio.toFixed(1)}%</div>
              <div class="list-val-sub">loss ratio</div>
            </div>
          </div>`;
      }).join('');
    } else {
      cityEl.innerHTML = '<p class="text-muted text-sm" style="padding:8px 0;">No city data yet. Simulate a trigger to generate claims.</p>';
    }

    // ML Fraud Model transparency
    const mlEl = document.getElementById('admin-ml-info');
    if (mlInfo) {
      const topFeatures = (mlInfo.feature_importances || []).slice(0, 6);
      const maxImp = topFeatures.length ? Math.max(...topFeatures.map(f => f.importance)) : 1;
      mlEl.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
          <div class="clay-icon clay-lg clay-purple">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/><path d="M17.599 6.5a3 3 0 0 0 .399-1.375"/><path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/><path d="M3.477 10.896a4 4 0 0 1 .585-.396"/><path d="M19.938 10.5a4 4 0 0 1 .585.396"/><path d="M6 18a4 4 0 0 1-1.967-.516"/><path d="M19.967 17.484A4 4 0 0 1 18 18"/></svg>
          </div>
          <div style="flex:1;">
            <div style="font-weight:700;font-size:14px;">${mlInfo.model_type}</div>
            <div style="font-size:12px;color:var(--muted);">${mlInfo.n_estimators} trees · ${mlInfo.training_samples.toLocaleString()} training samples</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:24px;font-weight:800;color:var(--green);">${(mlInfo.validation_accuracy*100).toFixed(1)}%</div>
            <div style="font-size:11px;color:var(--muted);">val accuracy</div>
          </div>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;">Feature Importances</div>
        ${topFeatures.map(f => `
          <div style="margin-bottom:9px;">
            <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px;">
              <span style="color:var(--text);">${f.feature}</span>
              <span style="color:var(--muted);">${(f.importance*100).toFixed(1)}%</span>
            </div>
            <div style="height:4px;background:var(--card3);border-radius:4px;overflow:hidden;">
              <div style="height:4px;background:linear-gradient(90deg,var(--orange),var(--purple));border-radius:4px;width:${(f.importance/maxImp*100).toFixed(1)}%;"></div>
            </div>
          </div>`).join('')}
        <div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:6px;">
          <span style="font-size:11px;background:var(--green-d);color:var(--green);padding:3px 10px;border-radius:50px;">≥75 → Auto-Approved</span>
          <span style="font-size:11px;background:var(--yellow-d);color:var(--yellow);padding:3px 10px;border-radius:50px;">45–74 → Pending</span>
          <span style="font-size:11px;background:var(--red-d);color:var(--red);padding:3px 10px;border-radius:50px;">&lt;45 → Manual Review</span>
        </div>`;
    } else {
      mlEl.innerHTML = '<p class="text-muted text-sm">ML model info unavailable.</p>';
    }

    // Fraud Detection Analytics (Phase 3)
    if (allClaims.length > 0) {
      const gpsFlagged = allClaims.filter(c => c.gps_valid === false).length;
      const shiftFlagged = allClaims.filter(c => c.shift_valid === false).length;
      const weatherFlagged = allClaims.filter(c => c.weather_cross_valid === false).length;
      const velocityFlagged = allClaims.filter(c => c.velocity_valid === false).length;
      const patternFlagged = allClaims.filter(c => c.activity_valid === false).length;
      const totalFlagged = allClaims.filter(c => c.authenticity_score < 75).length;
      const avgScore = (allClaims.reduce((s,c) => s + (c.authenticity_score||0), 0) / allClaims.length).toFixed(1);
      const fraudEl = document.getElementById('admin-fraud-analytics');
      if (fraudEl) {
        fraudEl.innerHTML = `
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px;">
            <div style="text-align:center;padding:8px;background:var(--card2);border-radius:8px;">
              <div style="font-size:20px;font-weight:800;color:var(--green);">${avgScore}</div>
              <div style="font-size:10px;color:var(--muted);">Avg Auth Score</div>
            </div>
            <div style="text-align:center;padding:8px;background:var(--card2);border-radius:8px;">
              <div style="font-size:20px;font-weight:800;color:var(--orange);">${totalFlagged}</div>
              <div style="font-size:10px;color:var(--muted);">Flagged Claims</div>
            </div>
            <div style="text-align:center;padding:8px;background:var(--card2);border-radius:8px;">
              <div style="font-size:20px;font-weight:800;">${allClaims.length}</div>
              <div style="font-size:10px;color:var(--muted);">Total Analyzed</div>
            </div>
          </div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:8px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;">Detection Breakdown</div>
          ${[
            {label:'GPS Spoofing', count:gpsFlagged, color:'#ef4444'},
            {label:'Shift Mismatch', count:shiftFlagged, color:'#f97316'},
            {label:'Weather Anomaly', count:weatherFlagged, color:'#eab308'},
            {label:'Velocity Anomaly', count:velocityFlagged, color:'#8b5cf6'},
            {label:'Pattern Anomaly', count:patternFlagged, color:'#3b82f6'},
          ].map(d => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:12px;">
              <span style="color:var(--text);">${d.label}</span>
              <span style="font-weight:700;color:${d.count > 0 ? d.color : 'var(--muted)'};">${d.count} flagged</span>
            </div>`).join('')}`;
      }
    }

    // Predictive Analytics Forecast (Phase 3)
    if (forecast) {
      const lrPct = (forecast.predicted_loss_ratio * 100).toFixed(1);
      const lrColor = forecast.predicted_loss_ratio < 0.65 ? 'var(--green)' : forecast.predicted_loss_ratio < 0.80 ? 'var(--yellow)' : 'var(--red)';
      const fcLR = document.getElementById('fc-loss-ratio');
      if (fcLR) { fcLR.textContent = lrPct + '%'; fcLR.style.color = lrColor; }
      const fcCl = document.getElementById('fc-claims');
      if (fcCl) fcCl.textContent = forecast.predicted_claims;
      const fcPy = document.getElementById('fc-payouts');
      if (fcPy) fcPy.textContent = fmt(forecast.predicted_payouts_inr);

      const ci = forecast.confidence_interval || [0, 0];
      const fcConf = document.getElementById('fc-confidence');
      if (fcConf) fcConf.textContent = `Week ${forecast.forecast_week} | CI: ${(ci[0]*100).toFixed(0)}%-${(ci[1]*100).toFixed(0)}% | Seasonal: ${forecast.seasonal_factor}x | ${forecast.data_points_used} weeks analyzed | ${forecast.method}`;

      // SVG sparkline
      const fcChart = document.getElementById('fc-chart');
      if (fcChart && forecast.historical_trend) {
        const pts = forecast.historical_trend.map(h => h.loss_ratio);
        pts.push(forecast.predicted_loss_ratio);
        const cw = fcChart.offsetWidth || 280;
        const ch = 80;
        const maxY = Math.max(...pts, 0.01) * 1.3;
        const stepX = pts.length > 1 ? cw / (pts.length - 1) : cw;
        const lastI = pts.length - 1;
        const histPath = pts.slice(0,-1).map((v,i) => {
          const x = i * stepX, y = ch - (v/maxY)*ch;
          return (i===0?'M':'L')+x.toFixed(1)+','+y.toFixed(1);
        }).join(' ');
        const prevX = (lastI-1)*stepX, prevY = ch-(pts[lastI-1]/maxY)*ch;
        const fx = lastI*stepX, fy = ch-(forecast.predicted_loss_ratio/maxY)*ch;
        const dots = pts.slice(0,-1).map((v,i) => `<circle cx="${(i*stepX).toFixed(1)}" cy="${(ch-(v/maxY)*ch).toFixed(1)}" r="2.5" fill="var(--blue)"/>`).join('');
        fcChart.innerHTML = `<svg width="100%" height="${ch}" viewBox="0 0 ${cw} ${ch}">
          <path d="${histPath}" fill="none" stroke="var(--blue)" stroke-width="2" opacity="0.7"/>
          <line x1="${prevX}" y1="${prevY}" x2="${fx}" y2="${fy}" stroke="var(--orange)" stroke-width="2" stroke-dasharray="5,3"/>
          <circle cx="${fx}" cy="${fy}" r="5" fill="var(--orange)"/>
          ${dots}
          <text x="${fx-4}" y="${Math.max(12, fy-8)}" font-size="9" fill="var(--orange)" text-anchor="end" font-weight="700">Forecast</text>
        </svg>`;
      }

      // Trigger risk forecast
      const trEl = document.getElementById('fc-trigger-risks');
      if (trEl) {
        const entries = Object.entries(forecast.trigger_risk_forecast || {});
        trEl.innerHTML = entries.length ? entries.map(([type, count]) => {
          const risk = count > 3 ? 'HIGH' : count > 1 ? 'MEDIUM' : 'LOW';
          const color = risk === 'HIGH' ? 'var(--red)' : risk === 'MEDIUM' ? 'var(--yellow)' : 'var(--green)';
          return `<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:12px;">
            <span>${triggerIcon(type)} ${type}</span>
            <span style="color:${color};font-weight:600;">${count} expected</span>
          </div>`;
        }).join('') : '<span class="text-muted text-sm">No trigger history to forecast from</span>';
      }

      // City risk pills
      const crEl = document.getElementById('fc-city-risks');
      if (crEl) {
        crEl.innerHTML = (forecast.city_risk_forecast || []).map(c => {
          const bg = c.risk_level === 'HIGH' ? 'rgba(239,68,68,0.15)' : c.risk_level === 'MEDIUM' ? 'rgba(245,158,11,0.15)' : 'rgba(34,197,94,0.12)';
          const fg = c.risk_level === 'HIGH' ? '#ef4444' : c.risk_level === 'MEDIUM' ? '#f59e0b' : '#22c55e';
          return `<span style="font-size:11px;padding:3px 8px;border-radius:6px;background:${bg};color:${fg};font-weight:600;">${c.city} ${c.seasonal_factor}x</span>`;
        }).join('');
      }
    }

    // Transaction Log (Phase 3: payment + disbursement audit trail)
    const txnEl = document.getElementById('admin-txn-log');
    if (txnEl) {
      if (!transactions.length) {
        txnEl.innerHTML = '<p class="text-muted text-sm" style="padding:8px 0;">No transactions yet.</p>';
      } else {
        txnEl.innerHTML = transactions.map(t => {
          const isPremium = t.transaction_type === 'premium_payment';
          const typeColor = isPremium ? '#22c55e' : '#3884f4';
          const typeBg = isPremium ? 'rgba(34,197,94,0.12)' : 'rgba(56,132,244,0.12)';
          const typeLabel = isPremium ? 'PREMIUM IN' : 'PAYOUT OUT';
          const evidenceSummary = sourceSummary(t.evidence_source_primary, t.evidence_source_secondary);
          const statusColor = t.status === 'settled' ? '#22c55e' : t.status === 'failed' ? '#ef4444' : '#f59e0b';
          const gatewayBadge = t.gateway === 'razorpay' ?
            `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(56,132,244,0.15);color:#3884f4;font-weight:700;">${isPremium ? 'RAZORPAY' : 'RZP TEST'}</span>` :
            '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(255,255,255,0.08);color:var(--muted);font-weight:700;">MOCK</span>';
          const displayRef = t.display_reference || t.razorpay_payment_id || t.upi_ref || '—';
          const when = t.settled_at || t.initiated_at;
          const whenStr = when ? new Date(when).toLocaleString('en-IN', {day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit'}) : '—';
          return `
            <div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);">
              <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                  <span style="font-size:10px;padding:2px 7px;border-radius:4px;background:${typeBg};color:${typeColor};font-weight:700;letter-spacing:0.3px;">${typeLabel}</span>
                  ${gatewayBadge}
                  <span style="font-size:10px;color:${statusColor};font-weight:700;text-transform:uppercase;">${t.status}</span>
                </div>
                <div style="font-size:10px;color:var(--muted);margin-top:3px;">${esc(t.reference_label || 'Reference')}</div>
                <div style="font-family:monospace;font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(displayRef)}</div>
                <div style="font-size:10px;color:var(--muted);margin-top:2px;line-height:1.4;">${esc(t.flow_note || '')}</div>
                ${signalBadgeRow({
                  confidence: t.evidence_confidence_score,
                  sourcePrimary: t.evidence_source_primary,
                  sourceSecondary: t.evidence_source_secondary,
                  sourceLog: t.evidence_source_log,
                  sourceTier: t.evidence_source_tier,
                  confidenceLabel: isPremium ? 'Checkout Proof' : 'Payout Evidence',
                })}
                <div style="font-size:10px;color:var(--muted);margin-top:2px;line-height:1.4;">
                  ${esc(isPremium ? evidenceSummary : `${t.trigger_type || 'Claim'}${t.trigger_city ? ` · ${t.trigger_city}` : ''}${t.claim_number ? ` · #${t.claim_number}` : ''}`)}
                </div>
                <div style="font-size:10px;color:var(--muted);margin-top:1px;">${whenStr}</div>
              </div>
              <div style="text-align:right;">
                <div style="font-weight:800;font-size:14px;color:${isPremium ? 'var(--green)' : 'var(--red)'};">${isPremium ? '+' : '-'}${fmt(t.amount)}</div>
                <div style="font-size:10px;color:var(--muted);">Worker #${t.worker_id}</div>
              </div>
            </div>`;
        }).join('');
      }
    }

    // All claims — isAdmin=true enables approve/reject buttons on reviewable claims
    window._allAdminClaims = allClaims;
    filterAdminClaims('all');

  } catch(e) {
    toast('Error loading admin data: ' + e.message, 'error');
  }
}

// ════════════════════════════════════════════════════════════════
// WHATSAPP NOTIFICATION + CONFETTI
// ════════════════════════════════════════════════════════════════
function showWAModal(triggerType, city, payoutAmt, claimNum) {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit' });
  const timeLabel = `Today, ${timeStr}`;

  const TRIGGER_MSGS = {
    'Heavy Rainfall':          `🌧️ <b>Heavy Rainfall Alert!</b><br>${city} receiving heavy rain.<br>Delivery disruption expected.`,
    'Extreme Rain / Flooding': `🌊 <b>Extreme Rain / Flooding!</b><br>${city}: Severe flooding reported.<br>Roads impassable in several zones.`,
    'Severe Heatwave':         `🔥 <b>Severe Heatwave Warning!</b><br>${city} temp exceeding 42°C.<br>NDMA advisory issued.`,
    'Hazardous AQI':           `🏭 <b>Hazardous AQI Alert!</b><br>${city} AQI > 400 (Severe).<br>Health emergency declared.`,
    'Platform Outage':         `☁️ <b>Platform Outage Detected!</b><br>Delivery platform systems down.<br>Orders paused city-wide.`,
    'Civil Disruption':        `🚨 <b>Civil Disruption Alert!</b><br>${city}: Bandh / protest reported.<br>Movement restricted in key areas.`,
  };

  document.getElementById('wa-trigger-msg').innerHTML =
    TRIGGER_MSGS[triggerType] || `⚡ <b>${triggerType}</b> detected in ${city}.`;
  document.getElementById('wa-payout-amt').textContent =
    payoutAmt > 0 ? `${fmt(payoutAmt)} Demo Payout Recorded` : 'Claim Under Review';
  document.getElementById('wa-claim-num').textContent = claimNum || 'CLM-AUTO-XXXXXX';
  document.getElementById('wa-time').textContent  = timeLabel;
  document.getElementById('wa-time2').textContent = timeLabel;

  document.getElementById('wa-modal-overlay').classList.remove('hidden');

  if (payoutAmt > 0) launchConfetti();
}

function closeWAModal() {
  document.getElementById('wa-modal-overlay').classList.add('hidden');
  document.querySelectorAll('.confetti-piece').forEach(c => c.remove());
  navTo('claims');
}

function launchConfetti() {
  const colors = ['#25d366','#FF6B35','#3B82F6','#F59E0B','#8B5CF6','#00C853'];
  for (let i = 0; i < 60; i++) {
    setTimeout(() => {
      const c = document.createElement('div');
      c.className = 'confetti-piece';
      c.style.cssText = `
        left:${Math.random()*100}vw; top:-10px;
        background:${colors[Math.floor(Math.random()*colors.length)]};
        width:${6+Math.random()*10}px; height:${6+Math.random()*10}px;
        animation-duration:${1.5+Math.random()*1.5}s;
        animation-delay:${Math.random()*0.5}s;
      `;
      document.body.appendChild(c);
      setTimeout(() => c.remove(), 3500);
    }, i * 30);
  }
}

// ════════════════════════════════════════════════════════════════
// BOOT
// ════════════════════════════════════════════════════════════════
window.addEventListener('load', () => {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }
  // Initialize Lucide SVG icons on first load
  refreshIcons();
  if (authToken && currentUser) {
    showApp();
    const hash = location.hash.replace('#', '');
    const validPages = ['dashboard','policy','triggers','claims','admin'];
    if (hash && validPages.includes(hash)) navTo(hash);
  }
});

// ESC key closes modals
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const waModal = document.getElementById('wa-modal-overlay');
    if (waModal && !waModal.classList.contains('hidden')) closeWAModal();
    const payModal = document.getElementById('pay-modal-overlay');
    if (payModal && !payModal.classList.contains('hidden')) closePaymentModal();
    const recoveryModal = document.getElementById('pay-recovery-overlay');
    if (recoveryModal && !recoveryModal.classList.contains('hidden')) closeRecoveryModal();
  }
});

// Offline / online detection
window.addEventListener('offline', () => toast('You are offline. Some features may not work.', 'error'));
window.addEventListener('online', () => toast('Back online!', 'success'));

// Back/forward button support
window.addEventListener('popstate', e => {
  if (!authToken || !currentUser) return;
  const page = e.state?.page || 'dashboard';
  const pageEl = document.getElementById(`page-${page}`);
  if (!pageEl) return;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link, .bottom-nav-item').forEach(l => l.classList.remove('active'));
  pageEl.classList.add('active');
  document.querySelectorAll(`[onclick="navTo('${page}')"]`).forEach(l => l.classList.add('active'));
  if (page === 'dashboard') loadDashboard();
  if (page === 'policy')    loadPolicyPage();
  if (page === 'triggers')  { syncTriggerPageContext(); loadLiveConditions(); loadTriggerTypes(); loadTriggerLiveStatus(); loadTriggerEvents(); }
  if (page === 'claims')    loadClaimsPage();
  if (page === 'admin')     loadAdminPage();
});
