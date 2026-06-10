// BTC Power Law + AIM Cockpit Widget for Scriptable
// Copy this entire file into Scriptable on iPhone.
// Optional: set the widget parameter to your private cockpit base URL.
// Example parameter format: http://tailnet-ip-or-magicdns-name:8766/
// v6.0 - Power Law, AIM posture, optional private 2030/2035 readiness.

const PUBLIC_DASHBOARD_URL = 'https://loknlod.github.io/Bitcoin-Power-Law/';
const PRIVATE_DASHBOARD_URL = normalizeBaseUrl(args.widgetParameter || '');
const PRIVATE_CACHE_BASE = PRIVATE_DASHBOARD_URL;
const PUBLIC_CACHE_BASE = PUBLIC_DASHBOARD_URL;

function normalizeBaseUrl(value) {
  const trimmed = String(value || '').trim();
  if (!trimmed) return '';
  return trimmed.endsWith('/') ? trimmed : trimmed + '/';
}

const GENESIS = new Date('2009-01-03T18:15:05Z');
const PL_A = -17.01;
const PL_B = 5.82;
const SUPPORT_MULT = 0.35;
const CONSERVATIVE_FAIR_MULT = 0.71;
const RESIST_MULT = 3.5;

function formatPrice(price) {
  if (!Number.isFinite(price) || price <= 0) return '—';
  if (price >= 1000000) return '$' + (price / 1000000).toFixed(2) + 'M';
  if (price >= 1000) return '$' + Math.round(price).toLocaleString();
  return '$' + price.toFixed(2);
}

function formatPct(value) {
  if (!Number.isFinite(value)) return '—';
  return (value >= 0 ? '+' : '') + value.toFixed(1) + '%';
}

function daysSinceGenesis(d = new Date()) {
  return (d - GENESIS) / 864e5;
}

function powerLawPrice(days) {
  return Math.pow(10, PL_A + PL_B * Math.log10(days));
}

function powerLawModel(d = new Date()) {
  const trend = powerLawPrice(daysSinceGenesis(d));
  return {
    support: trend * SUPPORT_MULT,
    conservativeFair: trend * CONSERVATIVE_FAIR_MULT,
    trend,
    resistance: trend * RESIST_MULT
  };
}

function powerLawStatus(price, model) {
  if (!Number.isFinite(price)) return { text: 'Signal pending', color: '#888888' };
  if (price < model.support) return { text: 'Deep value', color: '#00d395' };
  if (price < model.conservativeFair * 0.70) return { text: 'Undervalued', color: '#00d395' };
  if (price < model.conservativeFair * 1.30) return { text: 'Fair zone', color: '#4da6ff' };
  if (price < model.resistance) return { text: 'Above fair', color: '#ffab40' };
  return { text: 'Hot', color: '#ff6b6b' };
}

function colorForPct(value) {
  if (!Number.isFinite(value)) return new Color('#888888');
  return value >= 0 ? new Color('#00d395') : new Color('#ff6b6b');
}

// Display-only staleness labeling: the price cron refreshes every 30 minutes,
// so data older than 3 missed cycles is flagged. This is a UI label, not an alert.
const STALE_AFTER_MINUTES = 90;
// AIM caches refresh weekdays 8am/4pm CT only, so weekend posture data is
// legitimately old; flag it for display only once past 72h.
const AIM_STALE_AFTER_HOURS = 72;

function dataAgeMinutes(observedAt) {
  if (!observedAt) return NaN;
  const t = new Date(observedAt);
  if (Number.isNaN(t.getTime())) return NaN;
  return (Date.now() - t.getTime()) / 60000;
}

function dataAgeLabel(observedAt) {
  const mins = dataAgeMinutes(observedAt);
  if (!Number.isFinite(mins)) return 'age unknown';
  if (mins < 1) return 'now';
  if (mins < 60) return Math.round(mins) + 'm ago';
  const hours = mins / 60;
  if (hours < 48) return (hours < 10 ? hours.toFixed(1) : Math.round(hours)) + 'h ago';
  return Math.round(hours / 24) + 'd ago';
}

function readinessColor(labels) {
  const values = labels.filter(Boolean).map(v => String(v).toLowerCase());
  if (values.includes('red')) return new Color('#ff6b6b');
  if (values.includes('yellow')) return new Color('#ffab40');
  return new Color('#00d395');
}

async function loadJson(url) {
  const req = new Request(url);
  req.timeoutInterval = 8;
  req.headers = { 'Cache-Control': 'no-cache' };
  return await req.loadJSON();
}

async function loadFirst(paths) {
  let lastError = null;
  for (const path of paths) {
    try {
      const data = await loadJson(path.url);
      return { data, source: path.label, base: path.base };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error('No cache source available');
}

async function loadMarketCache() {
  const paths = [];
  if (PRIVATE_CACHE_BASE) paths.push({ url: PRIVATE_CACHE_BASE + 'market-cache.json', label: 'Private', base: PRIVATE_CACHE_BASE });
  paths.push({ url: PUBLIC_CACHE_BASE + 'market-cache.json', label: 'GitHub Pages', base: PUBLIC_CACHE_BASE });
  return await loadFirst(paths);
}

async function loadAimCache() {
  const paths = [];
  if (PRIVATE_CACHE_BASE) paths.push({ url: PRIVATE_CACHE_BASE + 'aim-cache.json', label: 'Private', base: PRIVATE_CACHE_BASE });
  paths.push({ url: PUBLIC_CACHE_BASE + 'aim-cache.json', label: 'GitHub Pages', base: PUBLIC_CACHE_BASE });
  return await loadFirst(paths);
}

async function loadPrivatePortfolioCache() {
  if (!PRIVATE_CACHE_BASE) return null;
  try {
    const data = await loadJson(PRIVATE_CACHE_BASE + 'portfolio-cockpit-cache.json');
    if (
      !data ||
      data.schema_version !== 'shrike_portfolio_cockpit.v0.1' ||
      data.mutation_allowed !== false ||
      !data.privacy_note ||
      !Number.isFinite(Number(data.retirement?.required_annual_growth_to_2030))
    ) {
      throw new Error('Private cockpit cache failed validation');
    }
    return data;
  } catch (error) {
    return null;
  }
}

async function loadWidgetData() {
  const [marketResult, aimResult, portfolio] = await Promise.all([
    loadMarketCache(),
    loadAimCache(),
    loadPrivatePortfolioCache()
  ]);

  const btcAsset = marketResult.data.assets && marketResult.data.assets.btc_usd;
  const price = Number(btcAsset && btcAsset.price);
  const model = powerLawModel();
  const gapToFair = Number.isFinite(price) ? ((price - model.conservativeFair) / model.conservativeFair) * 100 : NaN;
  const status = powerLawStatus(price, model);

  return {
    price,
    fair: model.conservativeFair,
    trend: model.trend,
    gapToFair,
    status,
    marketSource: marketResult.source,
    observedAt: btcAsset?.as_of || marketResult.data.generated_at,
    posture: aimResult.data.posture?.label || 'AIM',
    aimGeneratedAt: aimResult.data.generated_at || null,
    hardMoneyScore: aimResult.data.scores?.hard_money_repricing?.score,
    early2030: portfolio?.retirement?.early_2030_readiness || null,
    baseline2035: portfolio?.retirement?.faa_2035_readiness || null,
    dashboardUrl: portfolio ? PRIVATE_DASHBOARD_URL : PUBLIC_DASHBOARD_URL,
    privateLoaded: Boolean(portfolio)
  };
}

function addLabelValue(row, label, value, valueColor) {
  row.layoutHorizontally();
  row.centerAlignContent();

  const left = row.addText(label);
  left.font = Font.systemFont(10);
  left.textColor = new Color('#777777');

  row.addSpacer();

  const right = row.addText(value);
  right.font = Font.semiboldSystemFont(10);
  right.textColor = valueColor || new Color('#ffffff');
}

function addFooter(widget, data) {
  widget.addSpacer();
  const footer = widget.addStack();
  footer.layoutHorizontally();
  footer.centerAlignContent();

  const dot = footer.addText(data.privateLoaded ? '●' : '○');
  dot.font = Font.systemFont(8);
  dot.textColor = data.privateLoaded ? new Color('#00d395') : new Color('#888888');

  footer.addSpacer(4);

  // Show the age of the market DATA, not the render clock: a widget rendering
  // a stale cache must not look freshly updated.
  const ageMinutes = dataAgeMinutes(data.observedAt);
  const isStale = Number.isFinite(ageMinutes) ? ageMinutes > STALE_AFTER_MINUTES : true;
  const label = (isStale ? 'STALE · ' : '')
    + (data.privateLoaded ? 'Private' : 'Public')
    + ' · ' + dataAgeLabel(data.observedAt);
  const text = footer.addText(label);
  text.font = Font.systemFont(9);
  text.textColor = isStale ? new Color('#ffab40') : new Color('#555555');
}

async function createWidget() {
  let data;
  try {
    data = await loadWidgetData();
  } catch (error) {
    data = {
      price: NaN,
      fair: powerLawModel().conservativeFair,
      gapToFair: NaN,
      status: { text: 'Offline', color: '#ff6b6b' },
      posture: 'AIM',
      aimGeneratedAt: null,
      hardMoneyScore: null,
      early2030: null,
      baseline2035: null,
      observedAt: null,
      dashboardUrl: PRIVATE_DASHBOARD_URL || PUBLIC_DASHBOARD_URL,
      privateLoaded: false
    };
  }

  const widget = new ListWidget();
  widget.backgroundColor = new Color('#0a0a0a');
  widget.setPadding(12, 14, 12, 14);
  widget.url = data.dashboardUrl;

  const header = widget.addStack();
  header.layoutHorizontally();
  header.centerAlignContent();

  const logo = header.addText('₿');
  logo.font = Font.boldSystemFont(14);
  logo.textColor = new Color('#f7931a');

  header.addSpacer(5);

  const title = header.addText('Power Law');
  title.font = Font.mediumSystemFont(12);
  title.textColor = new Color('#aaaaaa');

  header.addSpacer();

  // Display-only AIM cache age: posture/hard-money can be days old over
  // weekends; an old regime read must not look current. No behavior change.
  const aimAgeMinutes = dataAgeMinutes(data.aimGeneratedAt);
  const aimStale = !Number.isFinite(aimAgeMinutes) || aimAgeMinutes > AIM_STALE_AFTER_HOURS * 60;
  const postureLabel = data.aimGeneratedAt && aimStale
    ? data.posture + ' · ' + dataAgeLabel(data.aimGeneratedAt)
    : data.posture;
  const posture = header.addText(postureLabel);
  posture.font = Font.semiboldSystemFont(10);
  posture.textColor = aimStale ? new Color('#ffab40') : new Color('#4da6ff');
  posture.minimumScaleFactor = 0.7;
  posture.lineLimit = 1;

  widget.addSpacer(5);

  const priceText = widget.addText(formatPrice(data.price));
  priceText.font = Font.boldSystemFont(25);
  priceText.textColor = new Color('#ffffff');
  priceText.minimumScaleFactor = 0.75;

  widget.addSpacer(2);

  const statusText = widget.addText(data.status.text + ' · ' + formatPct(data.gapToFair) + ' vs Conservative Fair');
  statusText.font = Font.systemFont(10);
  statusText.textColor = new Color(data.status.color);
  statusText.minimumScaleFactor = 0.75;

  widget.addSpacer(8);

  addLabelValue(widget.addStack(), 'Conservative Fair', formatPrice(data.fair), new Color('#4da6ff'));
  widget.addSpacer(4);
  addLabelValue(widget.addStack(), 'Hard Money', data.hardMoneyScore == null ? '—' : String(data.hardMoneyScore) + '/100', new Color('#f7931a'));

  if (data.early2030 || data.baseline2035) {
    widget.addSpacer(4);
    const line = '2030 ' + (data.early2030 || '—') + ' · 2035 ' + (data.baseline2035 || '—');
    // Color must follow the worst readiness label; "red" rendered in green
    // inverts the signal at a glance.
    addLabelValue(widget.addStack(), 'Retirement', line, readinessColor([data.early2030, data.baseline2035]));
  }

  addFooter(widget, data);
  widget.refreshAfterDate = new Date(Date.now() + 30 * 60 * 1000);
  return widget;
}

const widget = await createWidget();

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  widget.presentSmall();
}

Script.complete();
