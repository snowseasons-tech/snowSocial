(() => {
  const dialog = document.querySelector('#composer');
  const form = document.querySelector('#postForm');
  const caption = document.querySelector('#caption');
  const mediaUrlInput = document.querySelector('#mediaUrl');
  const dateInput = document.querySelector('#publishDate');
  const timeInput = document.querySelector('#publishTime');
  const toast = document.querySelector('#toast');
  const postsTable = document.querySelector('#postsTable');
  const agendaItems = document.querySelector('#agendaItems');
  const apiMeta = document.querySelector('meta[name="snowsocial-api-base"]');
  const API_BASE = (apiMeta?.content.trim() || (
    window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : window.location.origin
  )).replace(/\/+$/, '');
  const CHANNEL_LABELS = {
    facebook: 'Facebook',
    instagram: 'Instagram',
    google_business: 'Google Business',
    linkedin: 'LinkedIn',
    x: 'X'
  };
  const CHANNEL_VALUES = Object.fromEntries(
    Object.entries(CHANNEL_LABELS).map(([value, label]) => [label, value])
  );
  const STATUS_LABELS = {
    draft: 'Draft',
    approved: 'Approved',
    scheduled: 'Scheduled',
    queued: 'Queued',
    publishing: 'Publishing',
    published: 'Published',
    retry_wait: 'Retry scheduled',
    needs_review: 'Needs review',
    failed: 'Failed',
    cancelled: 'Cancelled'
  };
  let posts = [];
  let monthlySummary = null;
  let monthlyPosts = [];
  let toastTimer;
  let showLoginScreen = null;

  async function apiRequest(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        credentials: 'include',
        ...options,
        headers: {
          ...(options.body ? { 'Content-Type': 'application/json' } : {}),
          ...options.headers
        }
      });
    } catch (error) {
      throw new Error('Could not reach SnowSocial API. Check the API address and CORS settings.');
    }
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') message = body.detail;
      } catch (error) {
        console.warn('API returned a non-JSON error response:', error);
      }
      const error = new Error(message);
      error.status = response.status;
      if (response.status === 401 && path !== '/api/auth/login' && showLoginScreen) showLoginScreen();
      throw error;
    }
    if (response.status === 204) return null;
    return response.json();
  }

  async function initializeAuth() {
    const screen = document.querySelector('#authScreen'), form = document.querySelector('#loginForm'), errorBox = document.querySelector('#loginError');
    const show = (message = '') => { document.body.classList.remove('auth-pending'); screen.style.display = 'flex'; errorBox.textContent = message; };
    const hide = () => { screen.style.display = 'none'; };
    try { await apiRequest('/api/auth/me'); document.body.classList.remove('auth-pending'); hide(); }
    catch (error) { if (error.status === 401) show(); else { show(error.message); return false; } }
    showLoginScreen = () => show('Your session expired. Sign in again to continue.');
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); const button = form.querySelector('button[type="submit"]'); button.disabled = true; errorBox.textContent = '';
      try { const values = new FormData(form); await apiRequest('/api/auth/login', {method:'POST', body:JSON.stringify({username:values.get('username'),password:values.get('password')})}); form.reset(); hide(); document.body.classList.remove('auth-pending'); await refreshWorkspace(); }
      catch (error) { errorBox.textContent = error.message; } finally { button.disabled = false; }
    });
    document.querySelector('#logoutButton').addEventListener('click', async () => { try { await apiRequest('/api/auth/logout',{method:'POST'}); } finally { show('You have signed out.'); } });
    return true;
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 3500);
  }

  function formatNumber(value) {
    return value === null || value === undefined ? '—' : new Intl.NumberFormat().format(value);
  }

  function formatChange(value) {
    if (value === null || value === undefined) return '—';
    const sign = value > 0 ? '+' : '';
    return `${sign}${value}%`;
  }

  function setChange(element, value) {
    element.textContent = formatChange(value);
    element.classList.toggle('positive', typeof value === 'number' && value >= 0);
    element.classList.toggle('negative', typeof value === 'number' && value < 0);
    element.classList.toggle('neutral', value === null || value === undefined);
  }

  function showConnectionState(label, connected = false) {
    const state = document.querySelector('.connection-state');
    state.lastChild.textContent = ` ${label}`;
    state.classList.toggle('is-connected', connected);
    state.classList.toggle('is-error', !connected && label === 'API unavailable');
  }

  function openComposer() {
    const now = new Date();
    const localDate = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    dateInput.min = localDate;
    dateInput.value = localDate;
    dialog.showModal();
    caption.focus();
  }

  function channelLabel(value) {
    return CHANNEL_LABELS[value] || value;
  }

  function statusLabel(value) {
    return STATUS_LABELS[value] || value;
  }

  function appendCell(row, content, className = '') {
    const cell = document.createElement('td');
    if (className) cell.className = className;
    cell.textContent = content;
    row.append(cell);
    return cell;
  }

  function renderPost(post) {
    const row = document.createElement('tr');
    const postCell = document.createElement('td');
    const preview = document.createElement('div');
    preview.className = 'table-post';
    const thumb = document.createElement('span');
    thumb.className = 'post-thumb thumb-3';
    thumb.textContent = '🌿';
    const title = document.createElement('b');
    title.textContent = post.caption;
    preview.append(thumb, title);
    postCell.append(preview);
    row.append(postCell);

    const deliveryStatuses = new Map((post.deliveries || []).map((delivery) => [delivery.channel, statusLabel(delivery.status)]));
    appendCell(row, (post.channels || []).map((channel) => {
      const label = channelLabel(channel);
      return deliveryStatuses.has(channel) ? `${label}: ${deliveryStatuses.get(channel)}` : label;
    }).join(' · '));
    const statusCell = document.createElement('td');
    const pill = document.createElement('span');
    pill.className = `status-pill ${post.status === 'published' ? 'published' : post.status === 'failed' ? 'failed' : 'scheduled'}`;
    pill.textContent = statusLabel(post.status);
    statusCell.append(pill);
    row.append(statusCell);

    const dateValue = post.scheduled_at || post.created_at;
    const dateText = dateValue
      ? new Date(dateValue).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
      : 'No date';
    appendCell(row, dateText);
    appendCell(row, '—');
    row.append(document.createElement('td'));
    postsTable.append(row);
  }

  function renderUpcoming() {
    agendaItems.replaceChildren();
    const upcoming = posts
      .filter((post) => post.status === 'scheduled' && post.scheduled_at && new Date(post.scheduled_at) >= new Date())
      .sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at))
      .slice(0, 3);

    const label = document.createElement('div');
    label.className = 'agenda-label';
    label.textContent = upcoming.length ? 'UP NEXT' : 'NO UPCOMING POSTS';
    agendaItems.append(label);

    for (const post of upcoming) {
      const item = document.createElement('div');
      item.className = 'agenda-item';
      const when = new Date(post.scheduled_at);
      const time = document.createElement('time');
      time.textContent = when.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
      const thumb = document.createElement('div');
      thumb.className = 'post-thumb thumb-1';
      thumb.textContent = '🌲';
      const copy = document.createElement('div');
      copy.className = 'post-copy';
      const title = document.createElement('b');
      title.textContent = post.caption;
      const channels = document.createElement('small');
      channels.textContent = (post.channels || []).map(channelLabel).join(' · ');
      copy.append(title, channels);
      const pill = document.createElement('span');
      pill.className = 'status-pill scheduled';
      pill.textContent = 'Scheduled';
      item.append(time, thumb, copy, pill);
      agendaItems.append(item);
    }
  }

  function updateCounts() {
    const now = new Date();
    const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
    const monthEnd = new Date(now.getFullYear(), now.getMonth() + 1, 1);
    const publishedThisMonth = posts.filter((post) =>
      (post.deliveries || []).some((delivery) => {
        if (delivery.status !== 'published' || !delivery.published_at) return false;
        const publishedAt = new Date(delivery.published_at);
        return publishedAt >= monthStart && publishedAt < monthEnd;
      })
    ).length;
    const scheduled = posts.filter((post) => post.status === 'scheduled').length;
    document.querySelector('#publishedMetric').textContent = String(publishedThisMonth);
    document.querySelector('#scheduledMetric').textContent = String(scheduled);
    const next = posts
      .filter((post) => post.status === 'scheduled' && post.scheduled_at && new Date(post.scheduled_at) >= now)
      .sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at))[0];
    document.querySelector('#nextScheduled').textContent = next
      ? `Next: ${new Date(next.scheduled_at).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`
      : 'No posts scheduled';
  }

  function updateConnections(connections) {
    const connectedChannels = new Set(connections.map((connection) => connection.channel));
    document.querySelectorAll('.channel-row[data-channel]').forEach((row) => {
      const connected = connectedChannels.has(row.dataset.channel);
      const dot = row.querySelector('.connected-dot');
      dot.classList.toggle('is-disconnected', !connected);
      dot.title = connected ? 'Connected' : 'Not connected';
      const connectButton = row.querySelector('.connect-small');
      if (connectButton) connectButton.hidden = connected;
      row.setAttribute('aria-label', `${channelLabel(row.dataset.channel)}: ${connected ? 'connected' : 'not connected'}`);
    });
    const count = connectedChannels.size;
    showConnectionState(count ? `API connected · ${count} channel${count === 1 ? '' : 's'}` : 'API connected', true);
  }

  function populateReportMonths() {
    const input = document.querySelector('#analyticsMonth');
    const now = new Date();
    input.replaceChildren();
    for (let offset = 0; offset < 12; offset += 1) {
      const monthDate = new Date(now.getFullYear(), now.getMonth() - offset, 1);
      const value = `${monthDate.getFullYear()}-${String(monthDate.getMonth() + 1).padStart(2, '0')}-01`;
      const option = document.createElement('option');
      option.value = value;
      option.textContent = monthDate.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
      input.append(option);
    }
  }

  function updateAnalytics(summary) {
    const totals = summary.totals;
    document.querySelector('#publishedMetric').textContent = formatNumber(totals.published_posts);
    document.querySelector('#reachMetric').textContent = formatNumber(totals.reach);
    setChange(document.querySelector('#reachChange'), summary.change_percent.reach);
    const engagementChannels = summary.by_channel
      .map((item) => item.metrics)
      .filter((metrics) => metrics.reach !== null
        && metrics.likes !== null
        && metrics.comments !== null
        && metrics.shares !== null);
    const engagementReach = engagementChannels.reduce((sum, metrics) => sum + metrics.reach, 0);
    const engagementActions = engagementChannels.reduce(
      (sum, metrics) => sum + metrics.likes + metrics.comments + metrics.shares,
      0
    );
    const engagement = engagementReach > 0 ? engagementActions * 100 / engagementReach : null;
    document.querySelector('#engagementMetric').textContent = engagement === null ? '—' : engagement.toFixed(1);
    setChange(document.querySelector('#engagementChange'), null);

    const monthLabel = new Date(summary.month).toLocaleDateString(undefined, {
      month: 'long', year: 'numeric', timeZone: 'UTC'
    });
    document.querySelector('#reportMonthLabel').textContent = monthLabel;
    document.querySelector('#chartEmpty').textContent =
      `${formatNumber(totals.published_posts)} posts published · ${formatNumber(totals.impressions)} impressions · ${formatNumber(totals.link_clicks)} clicks`;

    const body = document.querySelector('#channelMetricsBody');
    body.replaceChildren();
    if (!summary.by_channel.length) {
      const row = document.createElement('tr');
      const cell = appendCell(row, 'No channel metrics are available for this month.', 'empty-row');
      cell.colSpan = 10;
      body.append(row);
      return;
    }
    summary.by_channel.forEach((item) => {
      const metrics = item.metrics;
      const row = document.createElement('tr');
      appendCell(row, channelLabel(item.channel), 'channel-name');
      appendCell(row, formatNumber(metrics.published_posts));
      appendCell(row, formatNumber(metrics.impressions));
      appendCell(row, formatNumber(metrics.reach));
      appendCell(row, formatNumber(metrics.link_clicks));
      appendCell(row, formatNumber(metrics.likes));
      appendCell(row, formatNumber(metrics.comments));
      appendCell(row, formatNumber(metrics.shares));
      appendCell(row, formatNumber(metrics.calls));
      appendCell(row, formatNumber(metrics.direction_requests));
      body.append(row);
    });
  }

  function csvCell(value) {
    let text = value === null || value === undefined ? '' : String(value);
    if (typeof value === 'string' && /^[\t\r\n ]*[=+@-]/u.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  }

  function downloadMonthlyCsv() {
    if (!monthlySummary) {
      showToast('Load a monthly report before exporting.');
      return;
    }
    const fields = [
      ['published_posts', 'Published Posts'],
      ['impressions', 'Impressions'],
      ['reach', 'Reach'],
      ['link_clicks', 'Clicks'],
      ['likes', 'Likes'],
      ['comments', 'Comments'],
      ['shares', 'Shares'],
      ['saves', 'Saves'],
      ['calls', 'Calls'],
      ['direction_requests', 'Directions']
    ];
    const rows = [[
      'Month',
      'Channel',
      'Post',
      'Published At',
      ...fields.map(([, label]) => label)
    ]];
    const addMetricsRow = (month, channel, metrics) => {
      rows.push([month, channel, '', '', ...fields.map(([field]) => metrics[field])]);
    };
    for (const channel of monthlySummary.by_channel) {
      addMetricsRow(monthlySummary.month, channelLabel(channel.channel), channel.metrics);
    }
    addMetricsRow(monthlySummary.month, 'Total', monthlySummary.totals);
    addMetricsRow(monthlySummary.previous_month, 'Previous month total', monthlySummary.previous_totals);
    rows.push([
      monthlySummary.month,
      'Change (%)',
      '',
      '',
      '',
      ...fields.slice(1).map(([field]) => monthlySummary.change_percent[field])
    ]);
    for (const post of monthlyPosts) {
      rows.push([
        monthlySummary.month,
        channelLabel(post.channel),
        post.caption,
        post.published_at,
        ...fields.map(([field]) => post.metrics[field])
      ]);
    }
    const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}`;
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `snowsocial-monthly-${monthlySummary.month.slice(0, 7)}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function updateMonthlyPostPerformance(items) {
    const body = document.querySelector('#monthlyPostsBody');
    body.replaceChildren();
    if (!items.length) {
      const row = document.createElement('tr');
      appendCell(row, 'No posts were published in this month.', 'empty-row').colSpan = 9;
      body.append(row);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement('tr');
      appendCell(row, channelLabel(item.channel), 'channel-name');
      const captionCell = appendCell(row, item.caption, 'post-caption-cell');
      captionCell.title = item.caption;
      appendCell(row, new Date(item.published_at).toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'
      }));
      appendCell(row, formatNumber(item.metrics.impressions));
      appendCell(row, formatNumber(item.metrics.reach));
      appendCell(row, formatNumber(item.metrics.link_clicks));
      appendCell(row, formatNumber(item.metrics.likes));
      appendCell(row, formatNumber(item.metrics.comments));
      appendCell(row, formatNumber(item.metrics.shares));
      body.append(row);
    });
  }

  async function loadMonthlyAnalytics(month) {
    const chart = document.querySelector('#chartEmpty');
    chart.textContent = 'Loading monthly metrics…';
    try {
      const [summary, postMetrics] = await Promise.all([
        apiRequest(`/api/analytics/monthly?month=${encodeURIComponent(month)}`),
        apiRequest(`/api/analytics/monthly/posts?month=${encodeURIComponent(month)}&limit=500`)
      ]);
      monthlySummary = summary;
      monthlyPosts = postMetrics;
      document.querySelector('#exportMonthlyCsv').disabled = false;
      updateAnalytics(summary);
      updateMonthlyPostPerformance(postMetrics);
    } catch (error) {
      console.warn('Monthly analytics are not available:', error);
      monthlySummary = null;
      monthlyPosts = [];
      document.querySelector('#exportMonthlyCsv').disabled = true;
      chart.textContent = 'Monthly metrics are not available for this period.';
      document.querySelector('#channelMetricsBody').replaceChildren();
      const row = document.createElement('tr');
      const cell = appendCell(row, 'Check the API connection and try again.', 'empty-row');
      cell.colSpan = 10;
      document.querySelector('#channelMetricsBody').append(row);
      const postsBody = document.querySelector('#monthlyPostsBody');
      postsBody.replaceChildren();
      const postsRow = document.createElement('tr');
      appendCell(postsRow, 'Post metrics are not available for this month.', 'empty-row').colSpan = 9;
      postsBody.append(postsRow);
    }
  }

  async function refreshWorkspace() {
    showConnectionState('Connecting to API');
    try {
      const [loadedPosts, connections] = await Promise.all([
        apiRequest('/api/posts?limit=100'),
        apiRequest('/api/connections')
      ]);
      posts = loadedPosts;
      postsTable.replaceChildren();
      posts.forEach(renderPost);
      renderUpcoming();
      updateCounts();
      updateConnections(connections);
      await loadMonthlyAnalytics(document.querySelector('#analyticsMonth').value);
    } catch (error) {
      console.error('Could not load SnowSocial workspace:', error);
      showConnectionState('API unavailable');
      showToast(error.message);
    }
  }

  async function savePost(shouldSchedule) {
    const channels = [...form.querySelectorAll('input[type="checkbox"]:checked')]
      .map((input) => CHANNEL_VALUES[input.value])
      .filter(Boolean);
    if (!caption.value.trim()) {
      caption.focus();
      showToast('Add a caption before saving this post.');
      return;
    }
    if (!channels.length) {
      showToast('Choose at least one channel.');
      return;
    }
    if (channels.includes('instagram') && caption.value.trim().length > 2200) {
      showToast('Instagram captions cannot exceed 2,200 characters.');
      return;
    }
    const mediaUrl = mediaUrlInput.value.trim();
    if (shouldSchedule && channels.includes('instagram') && !mediaUrl) {
      mediaUrlInput.focus();
      showToast('Add a public image URL before scheduling to Instagram.');
      return;
    }
    const scheduledAt = new Date(`${dateInput.value}T${timeInput.value}`);
    if (shouldSchedule && (!dateInput.value || !timeInput.value || scheduledAt <= new Date())) {
      showToast('Choose a future date and time for this post.');
      return;
    }

    const buttons = form.querySelectorAll('button');
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const created = await apiRequest('/api/posts', {
        method: 'POST',
        body: JSON.stringify({ caption: caption.value.trim(), channels, media_url: mediaUrl || null })
      });
      if (shouldSchedule) {
        await apiRequest(`/api/posts/${created.id}/approve`, { method: 'POST' });
        await apiRequest(`/api/posts/${created.id}/schedule`, {
          method: 'POST',
          body: JSON.stringify({ scheduled_at: scheduledAt.toISOString() })
        });
      }
      form.reset();
      document.querySelector('#charCount').textContent = '0';
      dialog.close();
      showToast(shouldSchedule ? 'Post added to your schedule.' : 'Draft saved to SnowSocial.');
      await refreshWorkspace();
    } catch (error) {
      console.error('Could not save post:', error);
      showToast(error.message);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  document.querySelector('#exportMonthlyCsv').addEventListener('click', downloadMonthlyCsv);
  document.querySelector('#analyticsMonth').addEventListener('change', (event) => {
    void loadMonthlyAnalytics(event.target.value);
  });
  document.querySelector('#newPost').addEventListener('click', openComposer);
  document.querySelector('#addToCalendar').addEventListener('click', openComposer);
  document.querySelector('#viewCalendar').addEventListener('click', () => {
    document.querySelector('#planner').scrollIntoView({ behavior: 'smooth' });
  });
  document.querySelector('#viewAllPosts').addEventListener('click', () => {
    document.querySelector('#posts').scrollIntoView({ behavior: 'smooth' });
  });
  document.querySelector('#connectChannel').addEventListener('click', () => {
    window.location.assign(`${API_BASE}/api/connections/meta/authorize`);
  });
  document.querySelector('#connectGoogle').addEventListener('click', () => {
    window.location.assign(`${API_BASE}/api/connections/google/authorize`);
  });
  document.querySelector('#connectLinkedIn').addEventListener('click', () => {
    window.location.assign(`${API_BASE}/api/connections/linkedin/authorize`);
  });
  document.querySelector('#connectX').addEventListener('click', () => {
    window.location.assign(`${API_BASE}/api/connections/x/authorize`);
  });
  caption.addEventListener('input', () => {
    document.querySelector('#charCount').textContent = String(caption.value.length);
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void savePost(true);
  });
  document.querySelector('#saveDraft').addEventListener('click', () => { void savePost(false); });

  document.querySelector('#todayLabel').textContent = new Date().toLocaleDateString(undefined, {
    weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'
  }).toUpperCase();
  const connectionResult = new URLSearchParams(window.location.search).get('connection');
  if (connectionResult === 'meta') showToast('Meta channels connected.');
  if (connectionResult === 'google') showToast('Google Business Profile connected.');
  if (connectionResult === 'google_error') showToast('Google authorization was not completed.');
  if (connectionResult === 'linkedin') showToast('LinkedIn organization connected.');
  if (connectionResult === 'linkedin_error') showToast('LinkedIn authorization was not completed.');
  if (connectionResult === 'x') showToast('X account connected.');
  if (connectionResult === 'x_error') showToast('X authorization was not completed.');
  if (connectionResult) window.history.replaceState({}, document.title, window.location.pathname);
  populateReportMonths();
  void initializeAuth().then((authenticated) => { if (authenticated) return refreshWorkspace(); });
})();
