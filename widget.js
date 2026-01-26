// BTC Power Law Widget for Scriptable
// Add to Home Screen as a widget for always-on display

const GENESIS = new Date('2009-01-03T18:15:05Z');
const PL_A = -17.01;
const PL_B = 5.82;
const SUPPORT_MULT = 0.35;
const RESIST_MULT = 3.5;

function daysSinceGenesis() {
  return (Date.now() - GENESIS.getTime()) / (1000 * 60 * 60 * 24);
}

function powerLawPrice(days) {
  return Math.pow(10, PL_A + PL_B * Math.log10(days));
}

function formatPrice(price) {
  if (price >= 1000000) return '$' + (price / 1000000).toFixed(2) + 'M';
  if (price >= 1000) return '$' + Math.round(price).toLocaleString();
  return '$' + price.toFixed(2);
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

function getStatus(price, fairValue, support, resist) {
  if (price < support) return { text: 'Deeply Undervalued', emoji: '🟢', color: new Color('#00d395') };
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
  const { price, change24h } = await fetchBTCPrice();
  const days = daysSinceGenesis();
  const fairValue = powerLawPrice(days);
  const support = fairValue * SUPPORT_MULT;
  const resist = fairValue * RESIST_MULT;
  const deviation = ((price - fairValue) / fairValue) * 100;
  const status = getStatus(price, fairValue, support, resist);
  const gaugePos = getGaugePosition(price, support, resist);
  
  const widget = new ListWidget();
  widget.backgroundColor = new Color('#0d0d0d');
  widget.setPadding(12, 14, 12, 14);
  
  // Header
  const headerStack = widget.addStack();
  headerStack.centerAlignContent();
  
  const logo = headerStack.addText('₿');
  logo.font = Font.boldSystemFont(18);
  logo.textColor = new Color('#f7931a');
  
  headerStack.addSpacer(6);
  
  const title = headerStack.addText('Power Law');
  title.font = Font.mediumSystemFont(14);
  title.textColor = new Color('#ffffff');
  
  headerStack.addSpacer();
  
  const statusBadge = headerStack.addText(status.emoji);
  statusBadge.font = Font.systemFont(14);
  
  widget.addSpacer(8);
  
  // Price
  const priceText = widget.addText(formatPrice(price));
  priceText.font = Font.boldSystemFont(28);
  priceText.textColor = new Color('#ffffff');
  
  // 24h change
  if (change24h !== null) {
    const changeText = widget.addText((change24h >= 0 ? '+' : '') + change24h.toFixed(1) + '% (24h)');
    changeText.font = Font.systemFont(12);
    changeText.textColor = change24h >= 0 ? new Color('#00d395') : new Color('#ff6b6b');
  }
  
  widget.addSpacer(8);
  
  // Gauge bar
  const gaugeStack = widget.addStack();
  gaugeStack.layoutHorizontally();
  gaugeStack.centerAlignContent();
  gaugeStack.size = new Size(0, 8);
  
  // Draw gauge using colored stacks
  const gaugeWidth = 140;
  const markerPos = Math.round(gaugePos * gaugeWidth);
  
  const gaugeContainer = gaugeStack.addStack();
  gaugeContainer.layoutHorizontally();
  gaugeContainer.cornerRadius = 4;
  gaugeContainer.size = new Size(gaugeWidth, 6);
  
  // Green portion
  const greenWidth = Math.round(gaugeWidth * 0.33);
  const green = gaugeContainer.addStack();
  green.backgroundColor = new Color('#00d395');
  green.size = new Size(greenWidth, 6);
  
  // Blue portion  
  const blueWidth = Math.round(gaugeWidth * 0.34);
  const blue = gaugeContainer.addStack();
  blue.backgroundColor = new Color('#4da6ff');
  blue.size = new Size(blueWidth, 6);
  
  // Red portion
  const red = gaugeContainer.addStack();
  red.backgroundColor = new Color('#ff6b6b');
  red.size = new Size(gaugeWidth - greenWidth - blueWidth, 6);
  
  widget.addSpacer(6);
  
  // Fair value and deviation
  const infoStack = widget.addStack();
  infoStack.layoutHorizontally();
  
  const fairLabel = infoStack.addText('Fair: ' + formatPrice(fairValue));
  fairLabel.font = Font.systemFont(11);
  fairLabel.textColor = new Color('#4da6ff');
  
  infoStack.addSpacer();
  
  const devLabel = infoStack.addText((deviation >= 0 ? '+' : '') + deviation.toFixed(0) + '%');
  devLabel.font = Font.boldSystemFont(11);
  devLabel.textColor = deviation >= 0 ? new Color('#ff6b6b') : new Color('#00d395');
  
  widget.addSpacer(4);
  
  // Status text
  const statusText = widget.addText(status.text);
  statusText.font = Font.mediumSystemFont(12);
  statusText.textColor = status.color;
  
  // Tap to open web app
  widget.url = 'https://loknlod.github.io/Bitcoin-Power-Law/';
  
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
