// WebRTC Direct Peer-to-Peer file transfer engine for transfer_room
(function() {
  const CHUNK_SIZE = 64 * 1024; // 64 KB
  const ICE_SERVERS = [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' }
  ];

  let pc = null;
  let dc = null;
  let ws = null;
  let isInitiator = false;
  let peerReady = false;

  // Receiving state
  let incomingInfo = null;
  let receivedBuffers = [];
  let receivedBytes = 0;

  window.initWebRTC = function(websocketInstance, isHost) {
    ws = websocketInstance;
    isInitiator = !!isHost;
    createPeerConnection();

    // Notify room of presence
    setTimeout(() => {
      sendSignal({ type: 'webrtc_join', initiator: isInitiator });
      if (isInitiator) {
        initiateOffer();
      }
    }, 500);
  };

  window.handleWebRTCSignal = async function(msg) {
    if (!pc) createPeerConnection();
    try {
      if (msg.type === 'webrtc_join') {
        if (isInitiator && !peerReady) {
          initiateOffer();
        }
      } else if (msg.type === 'webrtc_offer') {
        await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        sendSignal({ type: 'webrtc_answer', sdp: answer });
      } else if (msg.type === 'webrtc_answer') {
        await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
      } else if (msg.type === 'webrtc_candidate') {
        if (msg.candidate) {
          await pc.addIceCandidate(new RTCIceCandidate(msg.candidate));
        }
      }
    } catch (err) {
      console.log('[WebRTC Error]', err);
    }
  };

  function sendSignal(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify(obj)); } catch (e) {}
    }
  }

  function createPeerConnection() {
    if (pc) return;
    try {
      pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
      pc.onicecandidate = (e) => {
        if (e.candidate) {
          sendSignal({ type: 'webrtc_candidate', candidate: e.candidate });
        }
      };

      pc.onconnectionstatechange = () => {
        updateP2PStatus(pc.connectionState);
      };

      if (isInitiator) {
        dc = pc.createDataChannel('fileTransfer');
        setupDataChannel(dc);
      } else {
        pc.ondatachannel = (e) => {
          dc = e.channel;
          setupDataChannel(dc);
        };
      }
    } catch (e) {
      console.log('PeerConnection creation failed:', e);
    }
  }

  async function initiateOffer() {
    if (!pc) return;
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      sendSignal({ type: 'webrtc_offer', sdp: offer });
    } catch (e) {
      console.log('Offer creation error:', e);
    }
  }

  function setupDataChannel(channel) {
    channel.binaryType = 'arraybuffer';
    channel.onopen = () => {
      peerReady = true;
      updateP2PStatus('connected');
    };
    channel.onclose = () => {
      peerReady = false;
      updateP2PStatus('disconnected');
    };
    channel.onmessage = (e) => {
      handleChannelMessage(e.data);
    };
  }

  function updateP2PStatus(state) {
    const badge = document.getElementById('p2pStatusBadge');
    if (!badge) return;
    if (state === 'connected') {
      badge.className = 'text-[11px] px-2.5 py-0.5 rounded-full font-bold bg-green-100 text-green-800 flex items-center gap-1';
      badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span> P2P ডিরেক্ট কানেক্টেড (০ সার্ভার ডাটা)';
    } else {
      badge.className = 'text-[11px] px-2.5 py-0.5 rounded-full font-bold bg-yellow-100 text-yellow-800 flex items-center gap-1';
      badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-yellow-500"></span> ক্লাউড সিঙ্ক মোড';
    }
  }

  window.sendP2PFile = function(file, onProgress) {
    if (!dc || dc.readyState !== 'open') return false;

    // Send metadata header
    dc.send(JSON.stringify({
      meta: true,
      name: file.name,
      size: file.size,
      type: file.type
    }));

    const reader = new FileReader();
    let offset = 0;

    function readSlice() {
      const slice = file.slice(offset, offset + CHUNK_SIZE);
      reader.readAsArrayBuffer(slice);
    }

    reader.onload = (e) => {
      if (dc.bufferedAmount > 8 * 1024 * 1024) {
        setTimeout(() => reader.onload(e), 50);
        return;
      }
      dc.send(e.target.result);
      offset += e.target.result.byteLength;
      if (onProgress) onProgress(Math.min(100, Math.round(offset / file.size * 100)));

      if (offset < file.size) {
        readSlice();
      } else {
        if (onProgress) onProgress(100);
      }
    };

    readSlice();
    return true;
  };

  function handleChannelMessage(data) {
    if (typeof data === 'string') {
      try {
        const msg = JSON.parse(data);
        if (msg.meta) {
          incomingInfo = msg;
          receivedBuffers = [];
          receivedBytes = 0;
          showP2PDownloadProgress(msg.name, msg.size, 0);
        }
      } catch (e) {}
    } else {
      // Binary chunk
      receivedBuffers.push(data);
      receivedBytes += data.byteLength;
      if (incomingInfo) {
        const pct = Math.min(100, Math.round(receivedBytes / incomingInfo.size * 100));
        showP2PDownloadProgress(incomingInfo.name, incomingInfo.size, pct);

        if (receivedBytes >= incomingInfo.size) {
          // File complete!
          const blob = new Blob(receivedBuffers, { type: incomingInfo.type || 'application/octet-stream' });
          triggerBlobDownload(blob, incomingInfo.name);
          addP2PFileToList(incomingInfo.name, incomingInfo.size, blob);
          incomingInfo = null;
          receivedBuffers = [];
        }
      }
    }
  }

  function showP2PDownloadProgress(name, size, pct) {
    let p = document.getElementById('p2pProgressBox');
    if (!p) {
      p = document.createElement('div');
      p.id = 'p2pProgressBox';
      p.className = 'p-3 bg-pink-50 border border-pink-200 rounded-xl space-y-1 mb-2 text-xs';
      const container = document.getElementById('fileList');
      if (container) container.parentNode.insertBefore(p, container);
    }
    p.classList.remove('hidden');
    p.innerHTML = `
      <div class="flex justify-between font-bold text-gray-800">
        <span>⚡ ডিরেক্ট আসছে: ${esc(name)}</span>
        <span>${pct}%</span>
      </div>
      <div class="w-full bg-gray-200 h-2 rounded-full overflow-hidden">
        <div class="bg-gradient-to-r from-pink-600 to-orange-500 h-full transition-all" style="width: ${pct}%"></div>
      </div>
    `;
    if (pct >= 100) {
      setTimeout(() => { p.classList.add('hidden'); }, 3000);
    }
  }

  function triggerBlobDownload(blob, filename) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => a.remove(), 1000);
  }

  function addP2PFileToList(name, size, blob) {
    const list = document.getElementById('fileList');
    if (!list) return;
    const url = URL.createObjectURL(blob);
    const item = document.createElement('div');
    item.className = 'border-2 border-green-300 bg-green-50/50 rounded-xl p-3 flex items-center justify-between gap-2 animate-pulse';
    item.innerHTML = `
      <div class="min-w-0">
        <p class="font-bold text-sm text-gray-900 truncate">⚡ ${esc(name)}</p>
        <p class="text-[11px] text-green-700">ডিরেক্ট ডিভাইস-টু-ডিভাইস (WebRTC) • ${fmtSize(size)}</p>
      </div>
      <a href="${url}" download="${esc(name)}" class="px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-bold">⬇️ ডাউনলোড</a>
    `;
    list.prepend(item);
  }

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function fmtSize(b) {
    return b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(0) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
  }
})();
