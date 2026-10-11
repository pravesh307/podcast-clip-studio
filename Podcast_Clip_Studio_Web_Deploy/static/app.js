/* Interface uses same-origin API endpoints; private server or local machine. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let running = false;
  let pollTimer = null;
  let activeJob = null;
  const escapes = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  const safe = val => String(val == null ? '' : val).replace(/[&<>"']/g, c => escapes[c]);
  const fileBytes = n => n >= 1024 ** 3 ? `${(n/1024**3).toFixed(2)} GB` : `${(n/1024**2).toFixed(1)} MB`;
  const timeLabel = s => {let i = Math.max(0,Math.round(s || 0));return `${String(Math.floor(i/60)).padStart(2,'0')}:${String(i%60).padStart(2,'0')}`};
  const error = msg => { $('formError').hidden = false; $('formError').textContent = msg; };
  const clearError = () => { $('formError').hidden = true; };

  async function episodeLookup() {
    const url = $('episodeUrl').value.trim();
    if (!url) return error('Paste a Spotify episode URL first.');
    $('lookupBtn').disabled = true;
    $('lookupBtn').textContent = 'Checking...';
    clearError();
    try {
      const res = await fetch(`/api/episode?url=${encodeURIComponent(url)}`);
      const meta = await res.json();
      if (!res.ok) throw new Error(meta.detail || 'Unable to identify this episode');
      $('episodeTitle').textContent = meta.title;
      $('episodeNote').textContent = meta.note || 'Spotify episode link validated';
      const artwork = $('episodeArtwork');
      artwork.style.backgroundImage = /^https:\/\//.test(meta.thumbnail || '') ? `url(${JSON.stringify(meta.thumbnail).slice(1,-1)})` : '';
      artwork.textContent = meta.thumbnail ? '' : '♫';
      $('episodeCard').hidden = false;
    } catch (e) {
      $('episodeCard').hidden = true;
      error(e.message || 'Unable to look up episode');
    } finally {
      $('lookupBtn').textContent = 'Look up ↗';
      $('lookupBtn').disabled = false;
    }
  }
  $('lookupBtn').addEventListener('click', episodeLookup);
  $('episodeUrl').addEventListener('change', () => {$('episodeCard').hidden = true;});
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
  $('mode').addEventListener('change', () => {
    $('modeExplanation').textContent = $('mode').value === 'smart'
      ? 'Smart AI reviews the entire transcript in overlapping windows, selects self-contained stories, and names each clip. Requires an OpenAI API key configured on the server.'
      : 'Draft cuts work without an OpenAI API key. They divide the spoken video into rough 3–4 minute sections and need editorial review; they are not AI-curated highlights.';
  });
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
      return `<article class="clip-card"><img class="clip-thumb" loading="lazy" src="${thumb}" alt="Thumbnail for clip ${i+1}" /><div class="clip-body"><h3>${safe(clip.title)}</h3><div class="clip-meta">${timeLabel(clip.start)} → ${timeLabel(clip.end)} · ${timeLabel(clip.duration)} · ${clip.source==='ai'?'AI-selected':'Draft cut'}</div><div class="clip-actions"><button type="button" class="preview-clip" data-video="${media}" data-title="${safe(clip.title)}">▶ Preview</button><a href="${media}" download>↓ MP4</a><a href="${thumb}" download>↓ JPG</a><button type="button" class="copy-title" data-title="${safe(clip.title)}">Copy title</button></div></div></article>`;
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
    if (!fileInput.files.length) return error('Please attach a video file you are permitted to process. A Spotify link alone does not contain accessible video.');
    if (!$('rightsCheck').checked) return error('Please confirm that you have rights to process this source.');
    const form = new FormData();
    form.append('spotify_url',$('episodeUrl').value.trim());
    form.append('video',fileInput.files[0]);
    form.append('mode',$('mode').value);
    form.append('whisper',$('whisper').value);
    $('results').hidden = true;
    setRunning(true);
    setProgress(0,`Uploading video: ${fileBytes(fileInput.files[0].size)}...`,'Preparing your project');
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
  episodeLookup();
  loadHistory();
})();
