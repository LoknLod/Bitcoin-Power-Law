// BTC Power Law Widget for Scriptable
// iPhone home screen widget — shows BTC price, 24h change, hash rate
// v3.0 - Price + Hash Rate display

const GENESIS = new Date('2009-01-03T18:15:05Z');

function daysSinceGenesis() {
  return (Date.now() - GENESIS.getTime()) / (1000 * 60 * 60 * 24);
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
    try {
      const req = new Request('https://api.coinbase.com/v2/prices/BTC-USD/spot');
      const data = await req.loadJSON();
      return { price: parseFloat(data.data.amount), change24h: null };
    } catch (e2) {
      return { price: null, change24h: null };
    }
  }
}

async function fetchHashRate() {
  try {
    const req = new Request('https://mempool.space/api/v1/mining/hashrate/3d');
    const data = await req.loadJSON();
    // currentHashrate is in H/s — convert to EH/s
    const ehs = data.currentHashrate / 1e18;
    return ehs.toFixed(0);
  } catch (e) {
    return null;
  }
}

async function createWidget() {
  const [{ price, change24h }, hashRate] = await Promise.all([
    fetchBTCPrice(),
    fetchHashRate()
  ]);

  const widget = new ListWidget();
  widget.backgroundColor = new Color('#0a0a0a');
  widget.setPadding(14, 16, 14, 16);

  // Header row: ₿ Bitcoin
  const headerStack = widget.addStack();
  headerStack.centerAlignContent();

  const logo = headerStack.addText('₿');
  logo.font = Font.boldSystemFont(15);
  logo.textColor = new Color('#f7931a');

  headerStack.addSpacer(5);

  const title = headerStack.addText('Bitcoin');
  title.font = Font.mediumSystemFont(13);
  title.textColor = new Color('#aaaaaa');

  widget.addSpacer(8);

  // BTC Price — big
  const priceText = widget.addText(price ? formatPrice(price) : '—');
  priceText.font = Font.boldSystemFont(28);
  priceText.textColor = new Color('#ffffff');

  widget.addSpacer(4);

  // 24h change
  if (change24h !== null && change24h !== undefined) {
    const sign = change24h >= 0 ? '+' : '';
    const changeText = widget.addText(sign + change24h.toFixed(2) + '% (24h)');
    changeText.font = Font.systemFont(12);
    changeText.textColor = change24h >= 0 ? new Color('#00d395') : new Color('#ff6b6b');
  }

  widget.addSpacer(10);

  // Divider
  const divider = widget.addStack();
  divider.backgroundColor = new Color('#222222');
  divider.size = new Size(0, 1);

  widget.addSpacer(10);

  // Hash Rate row
  const hashStack = widget.addStack();
  hashStack.centerAlignContent();

  const hashIcon = hashStack.addText('⛏');
  hashIcon.font = Font.systemFont(12);

  hashStack.addSpacer(5);

  const hashLabel = hashStack.addText('Hash Rate');
  hashLabel.font = Font.systemFont(11);
  hashLabel.textColor = new Color('#666666');

  hashStack.addSpacer();

  const hashValue = hashStack.addText(hashRate ? hashRate + ' EH/s' : '—');
  hashValue.font = Font.boldSystemFont(13);
  hashValue.textColor = new Color('#f7931a');

  widget.addSpacer(4);

  // Last updated
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  const updatedText = widget.addText('Updated ' + timeStr);
  updatedText.font = Font.systemFont(9);
  updatedText.textColor = new Color('#444444');

  // Tap opens the quick view
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
