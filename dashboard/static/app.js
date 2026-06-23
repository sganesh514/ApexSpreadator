/**
 * ApexSpreadator — Dashboard Frontend
 * WebSocket client + UI rendering logic
 */

// State
let ws = null;
let connected = false;
let paused = false;
let closeTargetId = null;

// WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        connected = true;
        updateConnectionStatus(true);
        console.log('WebSocket connected');
        fetchAllData();
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleMessage(msg);
        } catch (e) {
            console.error('Failed to parse message:', e);
        }
    };

    ws.onclose = () => {
        connected = false;
        updateConnectionStatus(false);
        console.log('WebSocket disconnected. Reconnecting in 3s...');
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}

function handleMessage(msg) {
    switch (msg.type) {
        case 'full_update':
            updateAccount(msg.data.account);
            updatePositions(msg.data.positions);
            updateMarketStructure(msg.data.market_structure);
            updateStats(msg.data.stats);
            updateRisk(msg.data.risk);
            break;
        case 'account_update':
            updateAccount(msg.data);
            break;
        case 'position_update':
            updatePositions(msg.data);
            break;
        case 'market_structure_update':
            updateMarketStructure(msg.data);
            break;
        case 'trade_closed':
            fetchAllData();
            break;
        case 'journal_entry':
            addInsight(msg.data);
            break;
        case 'pong':
            break;
        default:
            console.log('Unknown message type:', msg.type);
    }
}

// REST API Calls
async function fetchAllData() {
    try {
        const [account, positions, history, ms_data, stats, journal, risk] = await Promise.all([
            fetch('/api/account').then(r => r.json()),
            fetch('/api/positions').then(r => r.json()),
            fetch('/api/history').then(r => r.json()),
            fetch('/api/market_structure').then(r => r.json()),
            fetch('/api/stats').then(r => r.json()),
            fetch('/api/journal').then(r => r.json()),
            fetch('/api/risk').then(r => r.json()),
        ]);

        updateAccount(account);
        updatePositions(positions);
        updateHistory(history);
        updateMarketStructure(ms_data);
        updateStats(stats);
        updateJournal(journal);
        updateRisk(risk);
    } catch (e) {
        console.error('Failed to fetch data:', e);
    }
}

// UI Update Functions
function updateConnectionStatus(isConnected) {
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('connectionText');

    if (isConnected) {
        dot.className = 'status-dot';
        text.textContent = 'Connected';
    } else {
        dot.className = 'status-dot disconnected';
        text.textContent = 'Disconnected';
    }
}

function updateAccount(data) {
    if (!data || data.error) return;

    animateValue('balance', formatCurrency(data.balance));
    
    const dailyPnl = data.daily_pnl || 0;
    const dailyEl = document.getElementById('dailyPnl');
    dailyEl.textContent = `${formatPnl(dailyPnl)} today`;
    dailyEl.className = `balance-change ${dailyPnl >= 0 ? 'positive' : 'negative'}`;

    document.getElementById('equity').textContent = formatCurrency(data.equity);
    document.getElementById('buyingPower').textContent = formatCurrency(data.buying_power);
    
    const totalPnlEl = document.getElementById('totalPnl');
    const totalPnl = data.total_pnl || 0;
    totalPnlEl.textContent = formatPnl(totalPnl);
    totalPnlEl.style.color = totalPnl >= 0 ? 'var(--profit)' : 'var(--loss)';
}

function updatePositions(data) {
    const list = document.getElementById('positionList');
    const countEl = document.getElementById('positionCount');

    if (!data || !Array.isArray(data) || data.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                No open positions. Agent is monitoring structure and active zones...
            </div>`;
        countEl.textContent = '0 / 4';
        return;
    }

    countEl.textContent = `${data.length} / 4`;
    list.innerHTML = data.map(pos => {
        const pnl = pos.unrealized_pnl || 0;
        const pnlPct = pos.unrealized_pnl_pct || 0;
        const isProfit = pnl >= 0;
        const pnlClass = isProfit ? 'positive' : 'negative';
        const rowClass = isProfit ? 'profit' : 'loss';
        const exp = pos.expiration ? formatExpiration(pos.expiration) : '?';
        const typeStr = pos.right === 'C' ? 'Bull Call' : 'Bear Put';

        return `
            <div class="position-row ${rowClass}" id="pos-${pos.id}">
                <div>
                    <div class="position-symbol">${pos.symbol} ${pos.long_strike}/${pos.short_strike} ${typeStr}</div>
                    <div class="position-spread">Exp: ${exp} | Qty: ${pos.quantity}</div>
                </div>
                <div class="position-greeks">
                    <span>Entry: ${formatCurrency(pos.entry_price)}</span>
                    <span>Mid: ${formatCurrency(pos.current_value)}</span>
                    <span>TP: ${formatCurrency(pos.take_profit_price)}</span>
                    <span>SL: ${formatCurrency(pos.invalidation_price)}</span>
                </div>
                <div class="position-pnl ${pnlClass}">${formatPnl(pnl)}</div>
                <div class="position-pnl-pct ${pnlClass}">${(pnlPct * 100).toFixed(1)}%</div>
                <div class="position-dte">${pos.front_dte || '?'} DTE</div>
                <div>
                    <button class="btn btn-danger btn-close-position" onclick="requestClose('${pos.id}', '${pos.symbol} ${pos.long_strike}/${pos.short_strike}')">
                        ✕ Close
                    </button>
                </div>
            </div>`;
    }).join('');
}

function updateHistory(data) {
    const body = document.getElementById('historyBody');
    const countEl = document.getElementById('historyCount');

    if (!data || !Array.isArray(data) || data.length === 0) {
        body.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 30px;">
                    No completed trades yet
                </td>
            </tr>`;
        countEl.textContent = '0 trades';
        return;
    }

    countEl.textContent = `${data.length} trades`;

    const sorted = [...data].reverse();
    body.innerHTML = sorted.map(trade => {
        const pnl = trade.realized_pnl || 0;
        const pnlPct = trade.realized_pnl_pct || 0;
        const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        const date = trade.exit_time ? new Date(trade.exit_time).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '--';
        const exp = formatExpiration(trade.expiration || '');
        const typeStr = trade.right === 'C' ? 'Bull Call' : 'Bear Put';

        return `
            <tr>
                <td>${date}</td>
                <td style="font-weight: 600;">${trade.symbol}</td>
                <td>${trade.long_strike}/${trade.short_strike} ${typeStr} (${exp})</td>
                <td>${formatCurrency(trade.entry_price)}</td>
                <td>${formatCurrency(trade.exit_price)}</td>
                <td class="${pnlClass}">${formatPnl(pnl)}</td>
                <td class="${pnlClass}">${(pnlPct * 100).toFixed(1)}%</td>
                <td style="color: var(--text-secondary); font-size: 0.75rem;">${formatExitReason(trade.exit_reason)}</td>
            </tr>`;
    }).join('');
}

function updateMarketStructure(data) {
    if (!data) return;

    // Update bias indicators
    const bias = data.bias || 'NEUTRAL';
    const biasEl = document.getElementById('currentBias');
    const biasBadge = document.getElementById('marketBiasBadge');
    
    biasEl.textContent = bias;
    biasBadge.textContent = bias;
    
    // Bias color styling
    if (bias === 'BULLISH') {
        biasEl.style.color = 'var(--profit)';
        biasBadge.style.background = 'var(--profit-dim)';
        biasBadge.style.color = 'var(--profit)';
    } else if (bias === 'BEARISH') {
        biasEl.style.color = 'var(--loss)';
        biasBadge.style.background = 'var(--loss-dim)';
        biasBadge.style.color = 'var(--loss)';
    } else {
        biasEl.style.color = 'var(--text-secondary)';
        biasBadge.style.background = 'var(--bg-glass)';
        biasBadge.style.color = 'var(--text-muted)';
    }

    // Update proximity
    const prox = data.proximity_pct !== undefined ? `${(data.proximity_pct * 100).toFixed(2)}%` : 'No active zones';
    const closest = data.closest_zone ? ` (${data.closest_zone.type} zone at ${data.closest_zone.high.toFixed(2)})` : '';
    document.getElementById('zoneProximity').textContent = `${prox}${closest}`;

    // Update active zones table
    const zonesBody = document.getElementById('activeZonesBody');
    const zones = data.active_zones || [];
    if (zones.length === 0) {
        zonesBody.innerHTML = `
            <tr>
                <td colspan="4" style="text-align: center; color: var(--text-muted);">No active zones mapped</td>
            </tr>`;
    } else {
        zonesBody.innerHTML = zones.map(z => {
            const range = `${z.low.toFixed(2)} - ${z.high.toFixed(2)}`;
            const typeClass = z.type === 'DEMAND' ? 'pnl-positive' : 'pnl-negative';
            return `
                <tr>
                    <td>${z.id}</td>
                    <td class="${typeClass}" style="font-weight:600;">${z.type}</td>
                    <td>${range}</td>
                    <td style="font-size:0.75rem; color:var(--text-secondary);">${z.origin_candle_time}</td>
                </tr>`;
        }).join('');
    }

    // Update risk filter logs
    const riskLogsBody = document.getElementById('riskLogBody');
    const logs = data.risk_filter_logs || [];
    if (logs.length === 0) {
        riskLogsBody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align: center; color: var(--text-muted);">No risk filter checks executed yet</td>
            </tr>`;
    } else {
        const sortedLogs = [...logs].reverse();
        riskLogsBody.innerHTML = sortedLogs.map(l => {
            const statusClass = l.status === 'APPROVED' ? 'pnl-positive' : 'pnl-negative';
            const exp = formatExpiration(l.date);
            const spreadType = l.direction === 'BULLISH' ? 'Bull Call' : 'Bear Put';
            return `
                <tr>
                    <td style="font-size:0.75rem;">${exp}</td>
                    <td>${l.symbol}</td>
                    <td>${l.long_strike}/${l.short_strike} ${spreadType}</td>
                    <td>${formatCurrency(l.net_debit)}</td>
                    <td>${l.rr_ratio ? l.rr_ratio.toFixed(2) : '--'}</td>
                    <td class="${statusClass}" style="font-weight:600; font-size:0.75rem;">${l.status}</td>
                </tr>`;
        }).join('');
    }
}

function updateStats(data) {
    if (!data) return;

    document.getElementById('totalTrades').textContent = `${data.total_trades || 0} trades`;
    document.getElementById('winRate').textContent = data.win_rate ? `${(data.win_rate * 100).toFixed(1)}%` : '--';
    document.getElementById('avgWin').textContent = data.avg_win ? formatCurrency(data.avg_win) : '--';
    document.getElementById('avgLoss').textContent = data.avg_loss ? formatCurrency(data.avg_loss) : '--';
    document.getElementById('profitFactor').textContent = 
        (data.profit_factor === 'Infinity' || data.profit_factor === Infinity)
        ? '∞'
        : (data.profit_factor && data.profit_factor < 100 ? data.profit_factor.toFixed(2) : '--');
}

function updateJournal(data) {
    if (!data || !Array.isArray(data) || data.length === 0) return;

    const list = document.getElementById('insightList');
    const recent = data.slice(-8).reverse();

    list.innerHTML = recent.map(entry => {
        let itemClass = '';
        if (entry.entry_type === 'entry') itemClass = 'entry';
        else if (entry.entry_type === 'exit') {
            const pnl = entry.data?.realized_pnl || 0;
            itemClass = pnl >= 0 ? 'exit-win' : 'exit-loss';
        } else if (entry.entry_type === 'lesson') itemClass = 'lesson';

        const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : '';

        return `
            <div class="insight-item ${itemClass}">
                <span class="insight-time">${time}</span>
                <span class="insight-content">${escapeHtml(entry.content || '')}</span>
            </div>`;
    }).join('');
}

function updateRisk(data) {
    if (!data) return;

    const banner = document.getElementById('circuitBreakerBanner');
    if (data.circuit_breaker_active) {
        banner.classList.add('active');
        document.getElementById('circuitBreakerReason').textContent = data.circuit_breaker_reason || 'Unknown';
    } else {
        banner.classList.remove('active');
    }
}

// Actions
function requestClose(positionId, description) {
    closeTargetId = positionId;
    document.getElementById('closeModalBody').textContent = 
        `Are you sure you want to close the ${description} vertical spread? This will place a market close order.`;
    document.getElementById('closeModal').classList.add('active');
}

function cancelClose() {
    closeTargetId = null;
    document.getElementById('closeModal').classList.remove('active');
}

async function confirmClose() {
    if (!closeTargetId) return;

    try {
        const resp = await fetch(`/api/positions/${closeTargetId}/close`, { method: 'POST' });
        const result = await resp.json();
        console.log('Close result:', result);
    } catch (e) {
        console.error('Failed to close position:', e);
    }

    cancelClose();
    setTimeout(fetchAllData, 2000);
}

async function togglePause() {
    try {
        const resp = await fetch('/api/agent/pause', { method: 'POST' });
        const result = await resp.json();
        paused = result.paused || false;

        const btn = document.getElementById('btnPause');
        if (paused) {
            btn.innerHTML = '▶ Resume Agent';
            btn.className = 'btn btn-success';
        } else {
            btn.innerHTML = '⏸ Pause Agent';
            btn.className = 'btn btn-primary';
        }
    } catch (e) {
        console.error('Failed to toggle pause:', e);
    }
}

async function resetCircuitBreaker() {
    try {
        await fetch('/api/risk/reset-circuit-breaker', { method: 'POST' });
        document.getElementById('circuitBreakerBanner').classList.remove('active');
    } catch (e) {
        console.error('Failed to reset circuit breaker:', e);
    }
}

// Helpers
function formatCurrency(amount) {
    if (amount === null || amount === undefined) return '--';
    const num = parseFloat(amount);
    if (isNaN(num)) return '--';
    return num >= 0 
        ? `$${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : `-$${Math.abs(num).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPnl(amount) {
    if (amount === null || amount === undefined) return '--';
    const num = parseFloat(amount);
    if (isNaN(num)) return '--';
    return num >= 0
        ? `+$${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : `-$${Math.abs(num).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatExpiration(exp) {
    if (!exp) return '?';
    // If it contains dashes, parse as YYYY-MM-DD
    if (exp.includes('-')) {
        const parts = exp.split('-');
        if (parts.length >= 3) {
            const year = parseInt(parts[0]);
            const month = parseInt(parts[1]) - 1;
            const day = parseInt(parts[2]);
            if (!isNaN(year) && !isNaN(month) && !isNaN(day)) {
                const d = new Date(year, month, day);
                if (!isNaN(d.getTime())) {
                    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                }
            }
        }
    }
    // If it is 8 chars long and starts with digits (YYYYMMDD)
    if (exp.length >= 8 && /^\d+$/.test(exp.substring(0, 8))) {
        try {
            const d = new Date(exp.substring(0, 4), parseInt(exp.substring(4, 6)) - 1, exp.substring(6, 8));
            if (!isNaN(d.getTime())) {
                return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            }
        } catch {}
    }
    return exp;
}

function formatExitReason(reason) {
    const map = {
        'profit_target': '🎯 Profit',
        'stop_loss': '🛑 Stop',
        'time_exit': '⏰ Time',
        'manual_close': '✋ Manual',
        'circuit_breaker': '🚨 Circuit',
    };
    return map[reason] || reason || '--';
}

function timeAgo(isoString) {
    if (!isoString) return '--';
    try {
        const then = new Date(isoString);
        const now = new Date();
        const diff = Math.floor((now - then) / 1000);
        if (diff < 60) return `${diff}s ago`;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    } catch {
        return '--';
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function animateValue(elementId, newValue) {
    const el = document.getElementById(elementId);
    if (!el) return;

    const oldValue = el.textContent;
    el.textContent = newValue;

    if (oldValue !== newValue && oldValue !== '--') {
        const oldNum = parseFloat(oldValue.replace(/[^0-9.-]/g, ''));
        const newNum = parseFloat(newValue.replace(/[^0-9.-]/g, ''));
        if (!isNaN(oldNum) && !isNaN(newNum)) {
            el.classList.add(newNum > oldNum ? 'flash-profit' : 'flash-loss');
            setTimeout(() => el.classList.remove('flash-profit', 'flash-loss'), 600);
        }
    }
}

function addInsight(entry) {
    const list = document.getElementById('insightList');
    const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

    const div = document.createElement('div');
    div.className = `insight-item ${entry.entry_type || ''}`;
    div.innerHTML = `
        <span class="insight-time">${time}</span>
        <span class="insight-content">${escapeHtml(entry.content || '')}</span>`;

    list.prepend(div);

    while (list.children.length > 10) {
        list.removeChild(list.lastChild);
    }
}

function updateTime() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-US', { 
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        timeZone: 'America/New_York'
    });
    const el = document.getElementById('timeDisplay');
    if (el) el.textContent = `ET ${timeStr}`;
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
    updateTime();
    setInterval(updateTime, 1000);

    setInterval(() => {
        if (connected) fetchAllData();
    }, 30000);
});
