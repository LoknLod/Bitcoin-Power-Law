// BTC Power Law Widget for Scriptable
// iPhone home screen widget — shows BTC price (7d change) + hash rate (7d change)
// v4.0 - 7d changes for both price and hashrate

const GENESIS = new Date('2009-01-03T18:15:05Z');

function daysSinceGenesis() {
  return (Date.now() - GENESIS.getTime()) / (1000 * 60 * 60 * 24);
}

function formatPrice(price) {
  if (price >= 1000000) return '$' + (price / 1000000).toFixed(2) + 'M';
  if (price >= 1000) return '$' + Math.round(price).toLocaleString();
  return '$' + price.toFixed(2);
}

async function fetchBTCPriceAndChange() {
  try {
    // Get current price + 7d history
    const req = new Request('https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=7&interval=daily');
    const data = await req.loadJSON();
    const prices = data.prices || [];
    if (prices.length >= 2) {
      const currentPrice = prices[prices.length - 1][1];
      const pastPrice = prices[0][1];
      const change7d = ((currentPrice - pastPrice) / pastPrice) * 100;
      return { price: currentPrice, change7d: change7d };
    }
  } catch (e) {}
  
  // Fallback: try simple price
  try {
    const req = new Request('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd');
    const data = await req.loadJSON();
    return { price: data.bitcoin.usd, change7d: null };
  } catch (e) {}
  
  // Final fallback to Coinbase
  try {
    const req = new Request('https://api.coinbase.com/v2/prices/BTC-USD/spot');
    const data = await req.loadJSON();
    return { price: parseFloat(data.data.amount), change7d: null };
  } catch (e2) {
    return { price: null, change7d: null };
  }
}

async function fetchHashRate() {
  try {
    const req = new Request('https://mempool.space/api/v1/mining/hashrate/7d');
    const data = await req.loadJSON();
    const current = data.currentHashrate / 1e18;
    
    // Find hashrate ~7 days ago
    const now = Date.now() / 1000;
    const sevenDaysAgo = now - (7 * 24 * 60 * 60);
    const hashrates = data.hashrates || [];
    let past = current;
    for (const h of hashrates) {
      if (h.timestamp <= sevenDaysAgo) {
        past = h.avgHashrate / 1e18;
        break;
      }
    }
    
    const change = ((current - past) / past) * 100;
    return { ehs: current.toFixed(0), change: change.toFixed(1) };
  } catch (e) {
    return null;
  }
}

async function createWidget() {
  const [{ price, change7d }, hashData] = await Promise.all([
    fetchBTCPriceAndChange(),
    fetchHashRate()
  ]);
  const hashRate = hashData ? hashData.ehs : null;
  const hashChange = hashData ? hashData.change : null;

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

  // 7d change (right-aligned next to price)
  if (change7d !== null && change7d !== undefined) {
    const priceStack = widget.addStack();
    priceStack.layoutHorizontally();
    priceStack.addSpacer();
    const sign = change7d >= 0 ? '+' : '';
    const pChange = priceStack.addText(sign + change7d.toFixed(1) + '% (7d)');
    pChange.font = Font.systemFont(12);
    pChange.textColor = change7d >= 0 ? new Color('#00d395') : new Color('#ff6b6b');
  }

  widget.addSpacer(12);

  // Divider
  const divider = widget.addStack();
  divider.backgroundColor = new Color('#222222');
  divider.size = new Size(0, 1);

  widget.addSpacer(10);

  // Hash Rate row
  const hashRow = widget.addStack();
  hashRow.layoutHorizontally();
  hashRow.centerAlignContent();

  const hashIcon = hashRow.addText('⛏');
  hashIcon.font = Font.systemFont(12);

  hashRow.addSpacer(4);

  const hashLabel = hashRow.addText('Hash Rate');
  hashLabel.font = Font.systemFont(11);
  hashLabel.textColor = new Color('#666666');

  hashRow.addSpacer(6);

  const hashValue = hashRow.addText(hashRate ? hashRate + ' EH/s' : '—');
  hashValue.font = Font.boldSystemFont(13);
  hashValue.textColor = new Color('#f7931a');

  // 7d change for hash rate
  if (hashChange !== null) {
    const hc = hashRow.addText((hashChange >= 0 ? '+' : '') + hashChange + '% (7d)');
    hc.font = Font.systemFont(11);
    hc.textColor = hashChange >= 0 ? new Color('#00d395') : new Color('#ff6b6b');
  }

  widget.addSpacer(8);

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
