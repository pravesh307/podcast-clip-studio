/* Interface uses same-origin API endpoints; private server or local machine. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let running = false;
  let pollTimer = null;
  let activeJob = null;
  let selectedYoutube = "";
  let aiConfigured = false;
  let lookupVersion = 0;
  const escapes = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  const safe = val => String(val == null ? '' : val).replace(/[&<>"']/g, c => escapes[c]);
  const fileBytes = n => n >= 1024 ** 3 ? `${(n/1024**3).toFixed(2)} GB` : `${(n/1024**2).toFixed(1)} MB`;
  const timeLabel = s => {let i = Math.max(0,Math.round(s || 0));return `${String(Math.floor(i/60)).padStart(2,'0')}:${String(i%60).padStart(2,'0')}`};
  const error = msg => { $('formError').hidden = false; $('formError').textContent = msg; };
  const clearError = () => { $('formError').hidden = true; };

  async function episodeLookup() {
    const url = $('episodeUrl').value.trim();
    if (!url) return error('Paste a YouTube video or Spotify episode link first.');
    const version = ++lookupVersion;
    selectedYoutube = '';
    $('sourceCandidates').hidden = true;
    $('lookupBtn').disabled = true;
    $('lookupBtn').textContent = 'Checking...';
    clearError();
    try {
      const res = await fetch(`/api/episode?url=${encodeURIComponent(url)}`);
      const meta = await res.json();
      if (!res.ok) throw new Error(meta.detail || 'Unable to identify this episode');
      if (version !== lookupVersion || $('episodeUrl').value.trim() !== url) return;
      $('episodeTitle').textContent = meta.title;
      $('episodeNote').textContent = meta.note || (meta.platform === 'youtube' ? 'YouTube video · ready for link import' : 'Spotify episode · choose its YouTube version below');
      const artwork = $('episodeArtwork');
      artwork.style.backgroundImage = /^https:\/\//.test(meta.thumbnail || '') ? `url(${JSON.stringify(meta.thumbnail).slice(1,-1)})` : '';
      artwork.textContent = meta.thumbnail ? '' : '♫';
      $('episodeCard').hidden = false;
      if (meta.platform === 'spotify' && !fileInput.files.length) {
        $('sourceCandidates').hidden = false;
        $('sourceCandidates').textContent = 'Finding possible YouTube versions...';
        const found = await fetch(`/api/source-candidates?url=${encodeURIComponent(url)}`);
        const data = await found.json();
        if (version !== lookupVersion || $('episodeUrl').value.trim() !== url) return;
        if (!found.ok) throw new Error(data.detail || 'Could not search YouTube. Paste its YouTube link instead.');
        if (!data.candidates.length) {
          $('sourceCandidates').textContent = 'No YouTube version found. Paste a YouTube link or use the optional file upload.';
        } else {
          $('sourceCandidates').innerHTML = '<p>Choose the matching full episode. Search results may include other recordings.</p>' + data.candidates.map((c,i) => `<button type="button" class="candidate" data-index="${i}"><strong>${safe(c.title)}</strong><small>${safe(c.channel)} · ${c.duration ? timeLabel(c.duration) : 'Length unavailable'}</small></button>`).join('');
          $('sourceCandidates').querySelectorAll('button').forEach(btn => btn.addEventListener('click', () => {
            selectedYoutube = data.candidates[Number(btn.dataset.index)].url;
            $('sourceCandidates').querySelectorAll('button').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            $('episodeNote').textContent = 'YouTube version selected. Ready to create clips.';
            clearError();
          }));
        }
      }
    } catch (e) {
      $('episodeCard').hidden = true;
      error(e.message || 'Unable to look up episode');
    } finally {
      $('lookupBtn').textContent = 'Look up ↗';
      $('lookupBtn').disabled = false;
    }
  }
  $('lookupBtn').addEventListener('click', episodeLookup);
  $('episodeUrl').addEventListener('input', () => {lookupVersion++;selectedYoutube='';$('episodeCard').hidden=true;$('sourceCandidates').hidden=true;});
  const drop = $('dropzone'), fileInput = $('videoFile');
  function chooseFile() {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    $('fileHeadline').textContent = file.name;
    $('fileSubhead').textContent = `${fileBytes(file.size)} · Ready for processing`;
    drop.classList.add('dragging');
    clearError();
  }
  fileInput.addEventListener('change', chooseFile);
  $('clearFile').addEventListener('click', () => {fileInput.value='';drop.classList.remove('dragging');$('fileHeadline').textContent='Drop a video here';$('fileSubhead').textContent='or choose a file · MP4, MOV, MKV, WebM, M4V';});
  drop.addEventListener('keydown', e => {if(e.key==='Enter' || e.key===' '){e.preventDefault();fileInput.click()}});
  for (const ev of ['dragenter','dragover']) drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('dragging'); });
  for (const ev of ['dragleave','dragend']) drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('dragging'); });
  drop.addEventListener('drop', e => {
    e.preventDefault(); drop.classList.remove('dragging');
    if (e.dataTransfer && e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      chooseFile();
    }
  });
  function updateMode() {
    $('aiSetup').hidden = aiConfigured || $('mode').value !== 'smart';
    $('modeExplanation').textContent = $('mode').value === 'smart'
      ? 'Smart AI reviews the entire transcript in overlapping windows, selects self-contained stories, and names each clip. Uses your OpenAI API key.'
      : 'Draft cuts work without an OpenAI API key. They divide the spoken video into rough sections at your selected length and need editorial review; they are not AI-curated highlights.';
  }
  $('mode').addEventListener('change', updateMode);
  fetch('/api/config').then(r=>r.json()).then(c=>{aiConfigured=!!c.ai_configured;updateMode();}).catch(updateMode);
  updateMode();
  function setProgress(percent, message, title='Processing your podcast') {
    let p = Math.min(100,Math.max(0,Number(percent || 0)));
    $('progressSection').hidden = false;
    $('progressPercent').textContent = `${p}%`;
    $('progressBar').style.width = `${p}%`;
    $('progressMessage').textContent = message;
    $('progressTitle').textContent = title;
  }
  function setRunning(status) {
    running = status;
    $('submitBtn').disabled = status;
    $('submitBtn').innerHTML = status ? '◷ &nbsp; Processing episode...' : '<span>✦</span> Generate all qualifying clips <span class="button-arrow">↗</span>';
  }
  async function watchJob() {
    if (!activeJob) return;
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(activeJob)}`);
      if (!response.ok) throw new Error('Processing job no longer available. Check your server.');
      const job = await response.json();
      if (job.status === 'failed') {
        setProgress(job.progress, job.message, 'Processing failed');
        error(job.message);
        setRunning(false);
        try{sessionStorage.removeItem('clipstudio-activeJob')}catch(_){}
        clearInterval(pollTimer);
      } else if (job.status === 'complete') {
        clearInterval(pollTimer);
        setProgress(100, `Exported ${job.manifest.clip_count} videos with titles and thumbnails.`, 'Your clips are ready');
        renderResults(job.manifest);
        loadHistory();
        try{sessionStorage.removeItem('clipstudio-activeJob')}catch(_){}
        setRunning(false);
      } else {
        setProgress(job.progress, job.message, job.status === 'queued' ? 'Your project is queued' : 'Processing your podcast');
      }
    } catch(e) {
      clearInterval(pollTimer); setRunning(false); error(e.message);
    }
  }
  async function loadHistory() {
    try {
      const response = await fetch('/api/jobs');
      if (!response.ok) return;
      const jobs = await response.json();
      if (!jobs.length) return;
      $('history').hidden = false;
      $('historyJobs').innerHTML = jobs.map(job => `<div class="history-item"><div><strong>${safe(job.episode?.title || 'Podcast episode')}</strong><small>${job.clip_count} clips · ${safe(job.job_id)}</small></div><button type="button" data-job="${safe(job.job_id)}">View exported clips ↗</button></div>`).join('');
      $('historyJobs').querySelectorAll('button').forEach(button => button.addEventListener('click',async () => {
        try {
          const id = button.dataset.job;
          const response = await fetch(`/api/jobs/${encodeURIComponent(id)}`);
          if (!response.ok) throw new Error('Could not load the saved project.');
          const job = await response.json();
          activeJob = id;
          renderResults(job.manifest);
        } catch (e) { error(e.message); }
      }));
    } catch(_) { /* Server connection warnings are handled in the submit form */ }
  }
  function renderResults(manifest) {
    const n = manifest.clip_count;
    $('results').hidden = false;
    $('resultCount').textContent = `${n} clip${n===1?'':'s'} created`;
    $('downloadAll').href = `/api/jobs/${activeJob}/download`;
    $('clipGrid').innerHTML = (manifest.clips || []).map((clip,i) => {
      const media = `/api/jobs/${activeJob}/files/${clip.video.split('/').map(encodeURIComponent).join('/')}`;
      const thumb = `/api/jobs/${activeJob}/files/${clip.thumbnail.split('/').map(encodeURIComponent).join('/')}`;
      return `<article class="clip-card"><img class="clip-thumb" loading="lazy" src="${thumb}" alt="Thumbnail for clip ${i+1}" /><div class="clip-body"><h3>${safe(clip.title)}</h3><div class="clip-meta">${timeLabel(clip.start)} → ${timeLabel(clip.end)} · ${timeLabel(clip.duration)} · ${clip.source==='ai'?'AI-selected':'Draft cut'}</div><p class="clip-reason">${safe(clip.reason || '')}</p>${(clip.title_variants || []).length ? `<details class="alternate-titles"><summary>Alternate titles</summary>${clip.title_variants.map(t=>`<p>${safe(t)}</p>`).join('')}</details>` : ''}<div class="clip-actions"><button type="button" class="preview-clip" data-video="${media}" data-title="${safe(clip.title)}">▶ Preview</button><a href="${media}" download>↓ MP4</a><a href="${thumb}" download>↓ JPG</a><button type="button" class="copy-title" data-title="${safe(clip.title)}">Copy title</button></div></div></article>`;
    }).join('');
    document.querySelectorAll('.copy-title').forEach(btn => btn.addEventListener('click',async()=>{
      try{await navigator.clipboard.writeText(btn.dataset.title);btn.textContent='Copied ✓';setTimeout(()=>btn.textContent='Copy title',1800);}catch(e){window.prompt('Copy this title:',btn.dataset.title)}
    }));
    document.querySelectorAll('.preview-clip').forEach(btn => btn.addEventListener('click', () => {
      $('videoModalTitle').textContent = btn.dataset.title;
      const player = $('clipPlayer'); player.src = btn.dataset.video;
      if ($('videoDialog').showModal) $('videoDialog').showModal();
      else window.open(btn.dataset.video, '_blank', 'noopener');
    }));
    $('results').scrollIntoView({behavior:'smooth',block:'start'});
  }
  $('jobForm').addEventListener('submit', e => {
    e.preventDefault();
    if (running) return;
    clearError();
    const source = $('episodeUrl').value.trim();
    if (!source && !fileInput.files.length) return error('Paste a YouTube or Spotify link. No file upload is needed for accessible YouTube videos.');
    if (/open\.spotify\.com/i.test(source) && !selectedYoutube && !fileInput.files.length) {episodeLookup();return error('Select the matching YouTube episode from the results below the link.');}
    if ($('mode').value==='smart' && !aiConfigured && !$('apiKey').value.trim()) return error('Enter an OpenAI API key to use Smart AI, or choose Draft cuts.');
    if (!$('rightsCheck').checked) return error('Please confirm that you have rights to process this source.');
    const form = new FormData();
    form.append('source_url',source);
    form.append('youtube_url',selectedYoutube);
    if (fileInput.files.length) form.append('video',fileInput.files[0]);
    form.append('clip_minutes',$('clipMinutes').value);
    form.append('style',$('titleStyle').value);
    form.append('rights_confirmed','true');
    if ($('apiKey').value.trim()) form.append('api_key',$('apiKey').value.trim());
    form.append('mode',$('mode').value);
    form.append('whisper',$('whisper').value);
    $('results').hidden = true;
    setRunning(true);
    setProgress(0,fileInput.files.length ? `Uploading video: ${fileBytes(fileInput.files[0].size)}...` : 'Queuing your link for import...','Preparing your project');
    const xhr = new XMLHttpRequest();
    xhr.open('POST','/api/jobs');
    xhr.upload.onprogress = e => {if(e.lengthComputable)setProgress(0,`Uploading source video... ${Math.round(e.loaded/e.total*100)}%`,'Preparing your project')};
    xhr.onload = () => {
      let payload = {};
      try{payload=JSON.parse(xhr.responseText)}catch(_){/* error payload not JSON */}
      if (xhr.status !== 202) {
        const detail = typeof payload.detail === 'string' ? payload.detail : `Server returned ${xhr.status}`;
        error(detail);setRunning(false);setProgress(0,detail,'Could not start project');return;
      }
      $('apiKey').value = '';
      activeJob = payload.job_id;
      try{sessionStorage.setItem('clipstudio-activeJob',activeJob)}catch(_){}
      setProgress(1,'Project received. Waiting for processing...');
      watchJob();
      pollTimer = setInterval(watchJob,2000);
    };
    xhr.onerror = () => {error('Unable to reach the processing server. Check the connection.');setRunning(false);};
    xhr.send(form);
  });
  $('closeVideoModal').addEventListener('click', () => $('videoDialog').close());
  $('videoDialog').addEventListener('close', () => { $('clipPlayer').pause();$('clipPlayer').removeAttribute('src');$('clipPlayer').load(); });
  $('videoDialog').addEventListener('click', e => { if(e.target === $('videoDialog'))$('videoDialog').close(); });
  // Recover the running processing job after a browser refresh.
  try {
    const old = sessionStorage.getItem('clipstudio-activeJob');
    if (old && /^[a-f0-9]{14}$/.test(old)) {
      activeJob = old;
      setRunning(true);
      watchJob();
      pollTimer = setInterval(watchJob, 2000);
    }
  } catch(_) {}
  // Spotify lookup does not scrape or download video.
  // Link lookup runs only when requested.
  loadHistory();
})();
