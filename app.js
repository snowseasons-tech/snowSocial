(() => {
  const dialog = document.querySelector('#composer');
  const form = document.querySelector('#postForm');
  const caption = document.querySelector('#caption');
  const dateInput = document.querySelector('#publishDate');
  const timeInput = document.querySelector('#publishTime');
  const toast = document.querySelector('#toast');
  const storageKey = 'snowsocial.posts.v1';
  let posts = loadPosts();
  let toastTimer;

  function loadPosts() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
      return Array.isArray(saved) ? saved : [];
    } catch (error) {
      console.error('Could not read saved posts:', error);
      return [];
    }
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2800);
  }

  function openComposer() {
    const now = new Date();
    const localDate = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    dateInput.value = localDate;
    dialog.showModal();
    caption.focus();
  }

  function savePost(status) {
    const channels = [...form.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value);
    if (!caption.value.trim()) {
      caption.focus();
      showToast('Add a caption before saving this post.');
      return;
    }
    if (channels.length === 0) {
      showToast('Choose at least one channel.');
      return;
    }
    const post = {
      id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      caption: caption.value.trim(),
      channels,
      date: dateInput.value,
      time: timeInput.value,
      status,
      createdAt: new Date().toISOString()
    };
    posts = [post, ...posts];
    try {
      localStorage.setItem(storageKey, JSON.stringify(posts));
    } catch (error) {
      console.error('Could not save post:', error);
      showToast('Could not save this post in your browser.');
      return;
    }
    renderPost(post);
    updateCounts();
    form.reset();
    dialog.close();
    showToast(status === 'Draft' ? 'Draft saved in this browser.' : 'Post added to your local schedule.');
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
    const channelsCell = document.createElement('td');
    channelsCell.textContent = post.channels.join(', ');
    const statusCell = document.createElement('td');
    const pill = document.createElement('span');
    pill.className = `status-pill ${post.status === 'Draft' ? 'scheduled' : 'published'}`;
    pill.textContent = post.status;
    statusCell.append(pill);
    const dateCell = document.createElement('td');
    dateCell.textContent = post.date ? new Date(`${post.date}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : 'No date';
    const reachCell = document.createElement('td');
    reachCell.textContent = '—';
    const menuCell = document.createElement('td');
    row.append(postCell, channelsCell, statusCell, dateCell, reachCell, menuCell);
    document.querySelector('#postsTable').prepend(row);
  }

  function updateCounts() {
    document.querySelector('#scheduledMetric').textContent = String(5 + posts.filter((post) => post.status === 'Scheduled').length);
  }

  document.querySelector('#newPost').addEventListener('click', openComposer);
  document.querySelector('#addToCalendar').addEventListener('click', openComposer);
  document.querySelector('#viewCalendar').addEventListener('click', () => showToast('Full month calendar is next in the build plan.'));
  document.querySelector('#viewAllPosts').addEventListener('click', () => showToast('Post filtering is next in the build plan.'));
  caption.addEventListener('input', () => { document.querySelector('#charCount').textContent = String(caption.value.length); });
  form.addEventListener('submit', (event) => { event.preventDefault(); savePost('Scheduled'); });
  document.querySelector('#saveDraft').addEventListener('click', () => savePost('Draft'));

  posts.slice(0, 5).forEach(renderPost);
  updateCounts();
})();
