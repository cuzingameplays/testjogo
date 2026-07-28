'use strict';

const $ = (selector) => document.querySelector(selector);
const state = {
  room: null,
  playerId: null,
  token: null,
  inviteUrl: null,
  socket: null,
  pollTimer: null,
  pingTimer: null,
  mediaStream: null,
  recording: false,
  lastVideoUrl: null,
};

const PHASES = {
  lobby: 'Sala de espera',
  media_ready: 'Mídia pronta',
  recording: 'Gravações abertas',
  ranking: 'Resultados',
};

function show(element, visible = true) {
  if (!element) return;
  element.classList.toggle('hidden', !visible);
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  show(element, true);
  clearTimeout(element._timer);
  element._timer = setTimeout(() => show(element, false), 2800);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(payload.detail || `Erro HTTP ${response.status}`);
  return payload;
}

function sessionKey(code) { return `dubshow:${code}`; }
function saveSession() {
  if (!state.room) return;
  sessionStorage.setItem(sessionKey(state.room.code), JSON.stringify({
    playerId: state.playerId, token: state.token, inviteUrl: state.inviteUrl,
  }));
}
function loadSession(code) {
  try { return JSON.parse(sessionStorage.getItem(sessionKey(code)) || 'null'); }
  catch (_) { return null; }
}

function setWelcomeMode(mode) {
  const creating = mode === 'create';
  $('#createTab').classList.toggle('active', creating);
  $('#joinTab').classList.toggle('active', !creating);
  show($('#createForm'), creating);
  show($('#joinForm'), !creating);
  show($('#setupPanel'), true);
}

async function enterRoom(payload) {
  state.room = payload.room;
  state.playerId = payload.player_id;
  state.token = payload.token;
  state.inviteUrl = payload.invite_url || `${location.origin}/?room=${payload.room.code}`;
  saveSession();
  show($('#welcomeScreen'), false);
  show($('#roomScreen'), true);
  show($('#connectionBadge'), true);
  connectSocket();
  startPolling();
  renderRoom();
}

async function reconnectExisting(code, saved) {
  try {
    const room = await api(`/api/rooms/${encodeURIComponent(code)}`);
    const playerExists = room.players.some((p) => p.id === saved.playerId);
    if (!playerExists) return false;
    state.room = room;
    state.playerId = saved.playerId;
    state.token = saved.token;
    state.inviteUrl = saved.inviteUrl || `${location.origin}/?room=${code}`;
    show($('#welcomeScreen'), false);
    show($('#roomScreen'), true);
    show($('#connectionBadge'), true);
    connectSocket();
    startPolling();
    renderRoom();
    return true;
  } catch (_) {
    sessionStorage.removeItem(sessionKey(code));
    return false;
  }
}

function connectSocket() {
  if (!state.room) return;
  if (state.socket) state.socket.close();
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${scheme}://${location.host}/ws/${state.room.code}/${state.playerId}?token=${encodeURIComponent(state.token)}`;
  const ws = new WebSocket(url);
  state.socket = ws;
  $('#connectionBadge').textContent = 'Conectando…';
  $('#connectionBadge').className = 'status-badge warn';
  ws.addEventListener('open', () => {
    $('#connectionBadge').textContent = 'Online';
    $('#connectionBadge').className = 'status-badge live';
    clearInterval(state.pingTimer);
    state.pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
    }, 25000);
  });
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'state') {
      state.room = message.room;
      renderRoom();
    }
    if (message.type === 'play_original') synchronizedPlay(message.delay_ms || 700);
  });
  ws.addEventListener('close', () => {
    $('#connectionBadge').textContent = 'Reconectando…';
    $('#connectionBadge').className = 'status-badge warn';
    setTimeout(() => {
      if (state.room && state.socket === ws) connectSocket();
    }, 1800);
  });
}

function startPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (!state.room) return;
    try {
      state.room = await api(`/api/rooms/${state.room.code}`);
      renderRoom();
    } catch (error) {
      $('#globalError').textContent = error.message;
      show($('#globalError'), true);
    }
  }, 2000);
}

function selfPlayer() {
  return state.room?.players.find((p) => p.id === state.playerId) || null;
}

function renderRoom() {
  const room = state.room;
  if (!room) return;
  const self = selfPlayer();
  const isHost = Boolean(self?.is_host);
  const singleplayer = room.max_players === 1;
  $('#roomCode').textContent = room.code;
  $('#roundText').textContent = singleplayer ? `Modo singleplayer · Rodada ${room.round_number}` : `Rodada ${room.round_number}`;
  $('#roundCounterSide').textContent = String(room.round_number);
  $('#phaseBadge').textContent = PHASES[room.phase] || room.phase;
  $('#playerCount').textContent = singleplayer ? 'Singleplayer' : `${room.players.length}/${room.max_players}`;
  $('#copyLinkButton').textContent = singleplayer ? 'Singleplayer' : `Copiar convite · ${room.code}`;
  $('#copyLinkButton').disabled = singleplayer;
  $('#readyToggle').checked = Boolean(self?.ready);

  const globalError = room.error_message || (room.media?.status === 'error' ? room.media.message : '');
  $('#globalError').textContent = globalError;
  show($('#globalError'), Boolean(globalError));

  $('#playersList').innerHTML = room.players.map((player) => {
    const status = player.recording_status === 'done' ? 'Dublagem enviada' :
      player.recording_status === 'analyzing' ? 'Analisando áudio…' :
      player.recording_status === 'rendering' ? 'Gerando vídeo…' :
      player.ready ? 'Pronto' : 'Na sala';
    return `
      <div class="player-item">
        <div class="avatar">${escapeHtml(player.name.slice(0, 1).toUpperCase())}</div>
        <div class="player-copy">
          <strong>${escapeHtml(player.name)}${player.is_host ? ' · Host' : ''}</strong>
          <span>${status}</span>
        </div>
        <span class="online-dot ${player.connected ? '' : 'offline'}"></span>
      </div>`;
  }).join('');

  show($('#hostPanel'), isHost);
  const media = room.media;
  const mediaReady = media?.status === 'ready';
  $('#mediaStatus').textContent = media?.status === 'processing' || media?.status === 'uploading'
    ? 'Processando…' : mediaReady ? 'Pronto' : media?.status === 'error' ? 'Erro' : 'Sem mídia';
  $('#mediaStatus').className = `status-badge ${mediaReady ? 'live' : (media?.status === 'processing' || media?.status === 'uploading') ? 'warn' : ''}`;
  $('#mediaMessage').textContent = media?.message || '';

  show($('#videoPanel'), mediaReady);
  show($('#hostRoundControls'), isHost && mediaReady);
  if (mediaReady) {
    $('#mediaTitle').textContent = media.title || 'Vídeo da rodada';
    $('#clipDurationBadge').textContent = `${Number(media.clip_duration).toFixed(1)} s`;
    const videoUrl = media.video_url;
    if (videoUrl && state.lastVideoUrl !== videoUrl) {
      state.lastVideoUrl = videoUrl;
      $('#referenceVideo').src = videoUrl;
      $('#referenceVideo').load();
    }
  }

  show($('#recordingPanel'), mediaReady && ['recording', 'ranking'].includes(room.phase));
  $('#recordButton').disabled = state.recording || self?.recording_status === 'analyzing' || self?.recording_status === 'rendering';
  const recordStatus = self?.recording_status || 'waiting';
  $('#recordingStatusBadge').textContent = {
    waiting: 'Aguardando', analyzing: 'Analisando…', done: 'Enviado', rendering: 'Gerando vídeo…', error: 'Erro',
  }[recordStatus] || recordStatus;
  $('#recordingStatusBadge').className = `status-badge ${recordStatus === 'done' ? 'live' : ['analyzing', 'rendering'].includes(recordStatus) ? 'warn' : ''}`;
  if (!state.recording) {
    $('#recordingMessage').textContent = recordStatus === 'done'
      ? 'Sua gravação foi analisada. Você pode gravar novamente para substituir a tentativa.'
      : recordStatus === 'analyzing' ? 'Comparando frequência, timbre, ritmo e pitch…'
      : recordStatus === 'rendering' ? 'Alinhando sua voz e mixando com a trilha…' : 'Assista ao clipe e grave sua voz.';
  }

  const scoredPlayers = room.players.filter((p) => p.score).sort((a, b) => b.score.total - a.score.total);
  show($('#rankingPanel'), scoredPlayers.length > 0 || room.phase === 'ranking');
  $('#showRankingButton').disabled = scoredPlayers.length === 0;
  $('#rankingList').innerHTML = scoredPlayers.length ? scoredPlayers.map((player, index) => rankingHtml(player, index, isHost)).join('') : '<p class="helper">Ainda não há notas nesta rodada.</p>';
  bindRankingButtons();

  $('#openRecordingButton').disabled = !mediaReady;
  $('#newRoundButton').disabled = room.phase === 'lobby' && !mediaReady;
}

function rankingHtml(player, index, isHost) {
  const canRender = player.id === state.playerId || isHost;
  const score = player.score;
  const metrics = [
    ['Pitch', score.pitch], ['Timbre', score.timbre], ['Ritmo', score.rhythm],
    ['Espectro', score.spectral], ['Frequências', score.chroma], ['Duração', score.duration],
  ];
  return `
    <div class="ranking-item">
      <div class="ranking-head">
        <div class="rank-number">${index + 1}º</div>
        <div class="ranking-person">
          <strong>${escapeHtml(player.name)}</strong>
          <span>${escapeHtml(score.verdict)}</span>
        </div>
        <div class="score-value">${Number(score.total).toFixed(1)}</div>
      </div>
      <div class="score-grid">
        ${metrics.map(([label, value]) => `
          <div class="score-metric">${label} · ${Number(value).toFixed(0)}
            <div class="mini-track"><span style="width:${Math.max(0, Math.min(100, value))}%"></span></div>
          </div>`).join('')}
      </div>
      <div class="result-actions">
        ${player.has_result ? `<button class="secondary-button watch-result" data-player="${player.id}" type="button">▶ Ver vídeo dublado</button>` : ''}
        ${canRender && !player.has_result ? `<button class="primary-button render-result" data-player="${player.id}" type="button">Gerar resultado dublado</button>` : ''}
      </div>
    </div>`;
}

function bindRankingButtons() {
  document.querySelectorAll('.render-result').forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await api(`/api/rooms/${state.room.code}/render`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ player_id: state.playerId, token: state.token, target_player_id: button.dataset.player }),
        });
        toast('Gerando o vídeo dublado…');
      } catch (error) {
        toast(error.message); button.disabled = false;
      }
    });
  });
  document.querySelectorAll('.watch-result').forEach((button) => {
    button.addEventListener('click', () => openResult(button.dataset.player));
  });
}

function openResult(playerId) {
  const player = state.room.players.find((p) => p.id === playerId);
  if (!player?.result_url) return;
  $('#resultTitle').textContent = `Dublagem de ${player.name}`;
  $('#resultVideo').src = player.result_url;
  $('#resultDialog').showModal();
  $('#resultVideo').play().catch(() => {});
}

async function roomAction(action) {
  try {
    await api(`/api/rooms/${state.room.code}/action`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_id: state.playerId, token: state.token, action }),
    });
  } catch (error) { toast(error.message); }
}

function synchronizedPlay(delayMs) {
  const video = $('#referenceVideo');
  video.muted = false;
  video.currentTime = 0;
  setTimeout(() => video.play().catch(() => toast('Clique no vídeo para permitir a reprodução.')), delayMs);
}

async function recordTake() {
  if (state.recording) return;
  const duration = Number(state.room.media.clip_duration);
  try {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      throw new Error('Este navegador não oferece gravação de microfone. Use Chrome, Edge ou Firefox atualizado.');
    }
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false }, video: false,
    });
    state.recording = true;
    $('#recordButton').disabled = true;
    $('#recordingMessage').textContent = 'Prepare-se…';
    await countdown();

    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
    const mimeType = candidates.find((type) => MediaRecorder.isTypeSupported(type)) || '';
    const chunks = [];
    const recorder = new MediaRecorder(state.mediaStream, mimeType ? { mimeType } : undefined);
    recorder.addEventListener('dataavailable', (event) => { if (event.data.size) chunks.push(event.data); });
    const stopped = new Promise((resolve) => recorder.addEventListener('stop', resolve, { once: true }));

    const video = $('#referenceVideo');
    video.pause();
    video.currentTime = 0;
    video.muted = true;
    await video.play();
    recorder.start(250);
    $('#recordingMessage').textContent = 'Gravando — acompanhe o personagem!';
    const started = performance.now();
    const progressTimer = setInterval(() => {
      const elapsed = (performance.now() - started) / 1000;
      $('#recordProgressBar').style.width = `${Math.min(100, elapsed / duration * 100)}%`;
    }, 80);
    await wait(duration * 1000);
    if (recorder.state !== 'inactive') recorder.stop();
    video.pause();
    clearInterval(progressTimer);
    $('#recordProgressBar').style.width = '100%';
    await stopped;

    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    if (blob.size < 1000) throw new Error('A gravação ficou vazia. Verifique o microfone.');
    $('#recordingMessage').textContent = 'Enviando e analisando…';
    const form = new FormData();
    form.append('player_id', state.playerId);
    form.append('token', state.token);
    form.append('file', blob, 'dublagem.webm');
    await api(`/api/rooms/${state.room.code}/takes`, { method: 'POST', body: form });
    toast('Gravação enviada!');
  } catch (error) {
    $('#recordingMessage').textContent = error.message;
  } finally {
    state.recording = false;
    $('#recordButton').disabled = false;
    state.mediaStream?.getTracks().forEach((track) => track.stop());
    state.mediaStream = null;
    setTimeout(() => { $('#recordProgressBar').style.width = '0%'; }, 900);
  }
}

async function countdown() {
  const overlay = $('#countdownOverlay');
  show(overlay, true);
  for (const number of [3, 2, 1]) {
    overlay.textContent = number;
    await wait(700);
  }
  overlay.textContent = 'DUBLE!';
  await wait(450);
  show(overlay, false);
}

function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char]));
}

$('#playOnlineButton').addEventListener('click', () => setWelcomeMode('create'));
$('#singlePlayerButton').addEventListener('click', async () => {
  const defaultName = $('#createName').value.trim() || 'Jogador Solo';
  $('#welcomeError').textContent = '';
  try {
    const payload = await api('/api/rooms', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: defaultName, max_players: 1 }),
    });
    await enterRoom(payload);
    toast('Modo singleplayer criado!');
  } catch (error) { $('#welcomeError').textContent = error.message; show($('#setupPanel'), true); }
});
$('#createTab').addEventListener('click', () => setWelcomeMode('create'));
$('#joinTab').addEventListener('click', () => setWelcomeMode('join'));

$('#createForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#welcomeError').textContent = '';
  try {
    const payload = await api('/api/rooms', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('#createName').value, max_players: Number($('#maxPlayers').value) }),
    });
    await enterRoom(payload);
  } catch (error) { $('#welcomeError').textContent = error.message; }
});

$('#joinForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#welcomeError').textContent = '';
  const code = $('#joinCode').value.trim().toUpperCase();
  try {
    const payload = await api(`/api/rooms/${encodeURIComponent(code)}/join`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('#joinName').value }),
    });
    await enterRoom(payload);
  } catch (error) { $('#welcomeError').textContent = error.message; }
});

$('#youtubeMediaForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api(`/api/rooms/${state.room.code}/media/youtube`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player_id: state.playerId, token: state.token, url: $('#youtubeUrl').value,
        clip_start: Number($('#youtubeStart').value), clip_duration: Number($('#youtubeDuration').value),
      }),
    });
    toast('Baixando e preparando o trecho…');
  } catch (error) { toast(error.message); }
});

$('#copyLinkButton').addEventListener('click', async () => {
  if (state.room?.max_players === 1) return;
  const link = state.inviteUrl || `${location.origin}/?room=${state.room.code}`;
  try { await navigator.clipboard.writeText(link); toast('Link copiado!'); }
  catch (_) { window.prompt('Copie o link da sala:', link); }
});

$('#leaveButton').addEventListener('click', () => {
  if (state.room) sessionStorage.removeItem(sessionKey(state.room.code));
  location.href = '/';
});

$('#readyToggle').addEventListener('change', () => {
  if (state.socket?.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify({ type: 'ready', ready: $('#readyToggle').checked }));
  }
});

$('#playForAllButton').addEventListener('click', () => roomAction('play_original'));
$('#openRecordingButton').addEventListener('click', () => roomAction('open_recording'));
$('#showRankingButton').addEventListener('click', () => roomAction('show_ranking'));
$('#newRoundButton').addEventListener('click', () => {
  if (confirm('Iniciar uma nova rodada e apagar os arquivos desta rodada?')) roomAction('new_round');
});
$('#recordButton').addEventListener('click', recordTake);
$('#listenOriginalButton').addEventListener('click', () => {
  const video = $('#referenceVideo');
  video.muted = false; video.currentTime = 0; video.play().catch(() => {});
});
$('#closeDialogButton').addEventListener('click', () => {
  $('#resultVideo').pause(); $('#resultDialog').close();
});
$('#resultDialog').addEventListener('close', () => { $('#resultVideo').pause(); $('#resultVideo').removeAttribute('src'); });

(async function init() {
  const code = new URLSearchParams(location.search).get('room')?.trim().toUpperCase();
  if (code) {
    $('#joinCode').value = code;
    setWelcomeMode('join');
    const saved = loadSession(code);
    if (saved && await reconnectExisting(code, saved)) return;
    show($('#setupPanel'), true);
    return;
  }
  show($('#setupPanel'), false);
})();
