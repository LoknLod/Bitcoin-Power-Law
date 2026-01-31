// BTC Power Law Widget for Scriptable
// Add to Home Screen as a widget for always-on display
// v2.0 - Now includes BTC/Gold ratio

const GENESIS = new Date('2009-01-03T18:15:05Z');

// BTC/USD Power Law
const USD_A = -17.01, USD_B = 5.82;
// BTC/Gold Power Law  
const GOLD_A = -19.150, GOLD_B = 5.525;

const SUPPORT_MULT = 0.35;
const RESIST_MULT = 3.5;

function daysSinceGenesis() {
  return (Date.now() - GENESIS.getTime()) / (1000 * 60 * 60 * 24);
}

function powerLawUSD(days) {
  return Math.pow(10, USD_A + USD_B * Math.log10(days));
}

function powerLawGold(days) {
  return Math.pow(10, GOLD_A + GOLD_B * Math.log10(days));
}

function formatPrice(price) {
  if (price >= 1000000) return '$' + (price / 1000000).toFixed(2) + 'M';
  if (price >= 1000) return '$' + Math.round(price).toLocaleString();
  return '$' + price.toFixed(2);
}

function formatRatio(r) {
  return r >= 100 ? r.toFixed(0) : r >= 10 ? r.toFixed(1) : r.toFixed(2);
}

async function fetchBTCPrice() {
  try {
    const req = new Request('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true');
    const data = await req.loadJSON();
    return {
      price: data.bitcoin.usd,
      change24h: data.bitcoin.usd_24h_change
    };
  } catch (e) {
    const req = new Request('https://api.coinbase.com/v2/prices/BTC-USD/spot');
    const data = await req.loadJSON();
    return {
      price: parseFloat(data.data.amount),
      change24h: null
    };
  }
}

async function fetchGoldPrice() {
  try {
    const req = new Request('https://data-asg.goldprice.org/dbXRates/USD');
    const data = await req.loadJSON();
    return data.items[0].xauPrice;
  } catch (e) {
    return 2800; // Fallback
  }
}

function getStatus(price, fairValue, support, resist) {
  if (price < support) return { text: 'Deep Value', emoji: '🟢', color: new Color('#00d395') };
  if (price < fairValue * 0.7) return { text: 'Undervalued', emoji: '🟢', color: new Color('#00d395') };
  if (price < fairValue * 1.3) return { text: 'Fair Value', emoji: '🔵', color: new Color('#4da6ff') };
  if (price < resist) return { text: 'Above Fair', emoji: '🟠', color: new Color('#ff9500') };
  return { text: 'Overvalued', emoji: '🔴', color: new Color('#ff6b6b') };
}

function getGaugePosition(price, support, resist) {
  const logPrice = Math.log10(price);
  const logSupport = Math.log10(support);
  const logResist = Math.log10(resist);
  return Math.max(0, Math.min(1, (logPrice - logSupport) / (logResist - logSupport)));
}

async function createWidget() {
  const [{ price, change24h }, goldPrice] = await Promise.all([
    fetchBTCPrice(),
    fetchGoldPrice()
  ]);
  
  const days = daysSinceGenesis();
  
  // USD calculations
  const usdFair = powerLawUSD(days);
  const usdSupport = usdFair * SUPPORT_MULT;
  const usdResist = usdFair * RESIST_MULT;
  const usdDev = ((price - usdFair) / usdFair) * 100;
  
  // Gold calculations
  const btcGold = price / goldPrice;
  const goldFair = powerLawGold(days);
  const goldDev = ((btcGold - goldFair) / goldFair) * 100;
  
  const status = getStatus(price, usdFair, usdSupport, usdResist);
  const gaugePos = getGaugePosition(price, usdSupport, usdResist);
  
  const widget = new ListWidget();
  widget.backgroundColor = new Color('#0a0a0a');
  widget.setPadding(12, 14, 12, 14);
  
  // Header
  const headerStack = widget.addStack();
  headerStack.centerAlignContent();
  
  const logo = headerStack.addText('₿');
  logo.font = Font.boldSystemFont(16);
  logo.textColor = new Color('#f7931a');
  
  headerStack.addSpacer(6);
  
  const title = headerStack.addText('Power Law');
  title.font = Font.mediumSystemFont(13);
  title.textColor = new Color('#ffffff');
  
  headerStack.addSpacer();
  
  const statusBadge = headerStack.addText(status.emoji);
  statusBadge.font = Font.systemFont(14);
  
  widget.addSpacer(6);
  
  // Price
  const priceText = widget.addText(formatPrice(price));
  priceText.font = Font.boldSystemFont(26);
  priceText.textColor = new Color('#ffffff');
  
  // 24h change
  if (change24h !== null) {
    const changeText = widget.addText((change24h >= 0 ? '+' : '') + change24h.toFixed(1) + '%');
    changeText.font = Font.systemFont(11);
    changeText.textColor = change24h >= 0 ? new Color('#00d395') : new Color('#ff6b6b');
  }
  
  widget.addSpacer(8);
  
  // Deviations row
  const devStack = widget.addStack();
  devStack.layoutHorizontally();
  
  // USD deviation
  const usdStack = devStack.addStack();
  usdStack.layoutVertically();
  
  const usdLabel = usdStack.addText('vs USD');
  usdLabel.font = Font.systemFont(9);
  usdLabel.textColor = new Color('#666');
  
  const usdDevText = usdStack.addText((usdDev >= 0 ? '+' : '') + usdDev.toFixed(0) + '%');
  usdDevText.font = Font.boldSystemFont(14);
  usdDevText.textColor = usdDev >= 0 ? new Color('#ff6b6b') : new Color('#00d395');
  
  devStack.addSpacer();
  
  // Gold deviation
  const goldStack = devStack.addStack();
  goldStack.layoutVertically();
  
  const goldLabel = goldStack.addText('vs Gold');
  goldLabel.font = Font.systemFont(9);
  goldLabel.textColor = new Color('#666');
  
  const goldDevText = goldStack.addText((goldDev >= 0 ? '+' : '') + goldDev.toFixed(0) + '%');
  goldDevText.font = Font.boldSystemFont(14);
  goldDevText.textColor = goldDev >= 0 ? new Color('#ff6b6b') : new Color('#00d395');
  
  widget.addSpacer(6);
  
  // Status text
  const statusText = widget.addText(status.text);
  statusText.font = Font.mediumSystemFont(11);
  statusText.textColor = status.color;
  
  // Tap to open web app
  widget.url = 'https://loknlod.github.io/Bitcoin-Power-Law/quick.html';
  
  return widget;
}

// Run
const widget = await createWidget();

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  widget.presentSmall();
}

Script.complete();
