(() => {
  const dialog = document.querySelector('#composer');
  const form = document.querySelector('#postForm');
  const caption = document.querySelector('#caption');
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
    linkedin: 'LinkedIn'
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
  let toastTimer;

  async function apiRequest(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
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
      throw new Error(message);
    }
    if (response.status === 204) return null;
    return response.json();
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
      row.setAttribute('aria-label', `${channelLabel(row.dataset.channel)}: ${connected ? 'connected' : 'not connected'}`);
    });
    const count = connectedChannels.size;
    showConnectionState(count ? `API connected · ${count} channel${count === 1 ? '' : 's'}` : 'API connected', true);
  }

  function updateAnalytics(summary) {
    const totals = summary.totals;
    document.querySelector('#reachMetric').textContent = formatNumber(totals.reach);
    setChange(document.querySelector('#reachChange'), summary.change_percent.reach);
    const engagement = totals.reach && totals.likes !== null && totals.comments !== null && totals.shares !== null
      ? ((totals.likes + totals.comments + totals.shares) * 100 / totals.reach)
      : null;
    document.querySelector('#engagementMetric').textContent = engagement === null ? '—' : engagement.toFixed(1);
    setChange(document.querySelector('#engagementChange'), null);
    document.querySelector('#chartEmpty').textContent = totals.reach === null
      ? 'Monthly metrics will appear after a metrics import.'
      : `${formatNumber(totals.reach)} people reached in ${new Date(summary.month).toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' })}. Daily trend data is not available yet.`;
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
      const now = new Date();
      const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
      try {
        updateAnalytics(await apiRequest(`/api/analytics/monthly?month=${month}`));
      } catch (error) {
        console.warn('Monthly analytics are not available:', error);
        document.querySelector('#chartEmpty').textContent = 'Monthly metrics are not available yet.';
      }
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
  if (new URLSearchParams(window.location.search).get('connection') === 'meta') {
    showToast('Meta channels connected.');
    window.history.replaceState({}, document.title, window.location.pathname);
  }
  void refreshWorkspace();
})();
