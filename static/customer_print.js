/* QR Se Print – customer page logic
   Globals injected by the template: SHOP_ID, SHOP_CODE, BKASH_NUM, NAGAD_NUM, RATES */
(function () {
  const $ = (id) => document.getElementById(id);
  const MAX_BYTES = 50 * 1024 * 1024;
  const ACTIVE_TAB = 'py-2 rounded-xl font-bold text-[11px] sm:text-xs transition bg-pink-600 text-white shadow';
  const IDLE_TAB = 'py-2 rounded-xl font-bold text-[11px] sm:text-xs transition text-gray-600 hover:text-gray-900';
  const bn = (n) => String(n).replace(/\d/g, (d) => '০১২৩৪৫৬৭৮৯'[d]);

  let currentMode = 'document'; // 'document' | 'nid' | 'studio' | 'transfer'
  let uploadedFile = null;
  let nidFrontFile = null;
  let nidBackFile = null;
  let nidRot = { front: 0, back: 0 };

  // ── helpers ──────────────────────────────────────────────────────────────
  function sendForm(url, formData, onProgress) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url, true);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = () => {
        let json = {};
        try { json = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
        resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, json });
      };
      xhr.onerror = () => resolve({ ok: false, status: 0, json: { detail: 'সার্ভারে সংযোগ পাওয়া যায়নি।' } });
      xhr.send(formData);
    });
  }

  function setBar(bar, label, p) {
    bar.style.width = p + '%';
    label.innerText = p + '%';
  }

  function checked(name) {
    const el = document.querySelector(`input[name="${name}"]:checked`);
    return el ? el.value : null;
  }

  function fmtSize(b) {
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(0) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  // ── elements ─────────────────────────────────────────────────────────────
  const tabs = { document: $('tabDocMode'), nid: $('tabNidMode'), studio: $('tabStudioMode'), transfer: $('tabTransferMode') };
  const studioCard = $('studioCard');
  const printFlow = $('printFlow');
  const transferFlow = $('transferFlow');
  const dropzoneNormal = $('dropzoneNormal');
  const dropzoneNid = $('dropzoneNid');
  const step1Title = $('step1Title');
  const paperTypeWrapper = $('paperTypeWrapper');
  const nupWrapper = $('nupWrapper');
  const duplexWrapper = $('duplexWrapper');
  const nidFitWrapper = $('nidFitWrapper');
  const optPhoto = $('optPhoto');
  const settingsCard = $('settingsCard');
  const paymentCard = $('paymentCard');
  const fileInput = $('fileInput');
  const uploadProgress = $('uploadProgress');
  const progressBar = $('progressBar');
  const progressPercent = $('progressPercent');
  const progressLabel = $('progressLabel');
  const fileInfoBadge = $('fileInfoBadge');
  const inputCopies = $('inputCopies');
  const totalPriceText = $('totalPriceText');
  const billSummaryText = $('billSummaryText');
  const btnMergeNid = $('btnMergeNid');

  // ── mode switching ───────────────────────────────────────────────────────
  function setMode(mode) {
    currentMode = mode;
    Object.keys(tabs).forEach((k) => (tabs[k].className = k === mode ? ACTIVE_TAB : IDLE_TAB));
    const isTransfer = mode === 'transfer';
    printFlow.classList.toggle('hidden', isTransfer);
    transferFlow.classList.toggle('hidden', !isTransfer);
    if (isTransfer) return;

    const isNid = mode === 'nid';
    const isStudio = mode === 'studio';
    dropzoneNormal.classList.toggle('hidden', isNid);
    dropzoneNid.classList.toggle('hidden', !isNid);
    step1Title.innerText = isNid ? 'NID / ID কার্ডের ২ পিঠের ছবি দিন'
      : isStudio ? 'যে ছবিটি দিয়ে ফটো বানাবেন সেটি দিন' : 'ডকুমেন্ট বা ছবি সিলেক্ট করুন';
    fileInput.accept = isStudio ? 'image/png,image/jpeg,image/webp' : '.pdf,image/png,image/jpeg,image/webp';
    paperTypeWrapper.classList.add('hidden');            // photo sheets now live in the Photo Studio tab
    nupWrapper.classList.toggle('hidden', isNid || isStudio);
    duplexWrapper.classList.toggle('hidden', isNid || isStudio);
    nidFitWrapper.classList.toggle('hidden', !isNid);
    settingsCard.classList.toggle('hidden', isStudio);
    studioCard.classList.toggle('hidden', !isStudio);
    resetUploads();
  }
  tabs.document.addEventListener('click', () => setMode('document'));
  tabs.nid.addEventListener('click', () => setMode('nid'));
  tabs.studio.addEventListener('click', () => setMode('studio'));
  tabs.transfer.addEventListener('click', () => setMode('transfer'));

  function resetUploads() {
    uploadedFile = null;
    nidFrontFile = null;
    nidBackFile = null;
    nidRot = { front: 0, back: 0 };
    fileInput.value = '';
    $('nidFrontInput').value = '';
    $('nidBackInput').value = '';
    ['Front', 'Back'].forEach((side) => {
      $('nid' + side + 'Label').innerText = 'ছবি তুলুন/বাছুন';
      $('nid' + side + 'Thumb').classList.add('hidden');
      $('nid' + side + 'Icon').classList.remove('hidden');
    });
    btnMergeNid.disabled = true;
    btnMergeNid.innerText = 'দুটো ছবি সিলেক্ট করে NID শিট তৈরি করুন';
    btnMergeNid.className = 'w-full py-2.5 bg-gray-200 text-gray-500 font-bold text-xs rounded-xl transition cursor-not-allowed';
    fileInfoBadge.classList.add('hidden');
    $('nidInlinePreview').classList.add('hidden');
    optPhoto.classList.remove('hidden');
    settingsCard.classList.add('opacity-60', 'pointer-events-none');
    studioCard.classList.add('opacity-60', 'pointer-events-none');
    paymentCard.classList.add('opacity-60', 'pointer-events-none');
    totalPriceText.innerText = '৳ 0';
  }

  // ── NID: choose two photos ───────────────────────────────────────────────
  $('nidFrontBox').addEventListener('click', () => $('nidFrontInput').click());
  $('nidBackBox').addEventListener('click', () => $('nidBackInput').click());

  function onNidPicked(side, file) {
    if (!file) return;
    if (side === 'Front') nidFrontFile = file; else nidBackFile = file;
    $('nid' + side + 'Label').innerText = '✓ ' + file.name.substring(0, 14);
    const thumb = $('nid' + side + 'Thumb');
    thumb.src = URL.createObjectURL(file);
    thumb.classList.remove('hidden');
    $('nid' + side + 'Icon').classList.add('hidden');
    if (nidFrontFile && nidBackFile) {
      btnMergeNid.disabled = false;
      btnMergeNid.className = 'w-full py-2.5 bg-pink-600 hover:bg-pink-700 text-white font-bold text-xs rounded-xl transition shadow cursor-pointer';
      btnMergeNid.innerText = '✨ প্রিন্ট শিট তৈরি করুন ও প্রিভিউ দেখুন';
    }
  }
  $('nidFrontInput').addEventListener('change', (e) => onNidPicked('Front', e.target.files[0]));
  $('nidBackInput').addEventListener('change', (e) => onNidPicked('Back', e.target.files[0]));

  btnMergeNid.addEventListener('click', async () => {
    if (!nidFrontFile || !nidBackFile) return;
    dropzoneNid.classList.add('hidden');
    progressLabel.innerText = 'NID ছবি আপলোড ও প্রসেসিং হচ্ছে...';
    setBar(progressBar, progressPercent, 0);
    uploadProgress.classList.remove('hidden');

    const fd = new FormData();
    fd.append('front', nidFrontFile);
    fd.append('back', nidBackFile);
    const res = await sendForm('/api/upload-nid', fd, (p) => setBar(progressBar, progressPercent, p));
    uploadProgress.classList.add('hidden');
    if (res.ok) {
      uploadedFile = res.json;
      showUploadedFile();
    } else {
      alert(res.json.detail || 'NID ছবি প্রসেসিং ব্যর্থ হয়েছে।');
      dropzoneNid.classList.remove('hidden');
    }
  });

  function nidPreviewUrl(width) {
    return `/api/nid-preview/${uploadedFile.nid_token}?color=${checked('color_mode') || 'bw'}` +
      `&fit=${checked('nid_fit') || 'cover'}&rf=${nidRot.front}&rb=${nidRot.back}&w=${width}&t=${Date.now()}`;
  }
  let nidTimer = null;
  function refreshNidInline() {
    if (!uploadedFile || !uploadedFile.is_nid) return;
    clearTimeout(nidTimer);
    nidTimer = setTimeout(() => { $('nidInlineImg').src = nidPreviewUrl(800); }, 150);
  }
  $('btnRotFront').addEventListener('click', () => { nidRot.front = (nidRot.front + 90) % 360; refreshNidInline(); });
  $('btnRotBack').addEventListener('click', () => { nidRot.back = (nidRot.back + 90) % 360; refreshNidInline(); });

  // ── normal file upload ───────────────────────────────────────────────────
  dropzoneNormal.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => { if (e.target.files.length) handleFileUpload(e.target.files[0]); });
  dropzoneNormal.addEventListener('dragover', (e) => { e.preventDefault(); dropzoneNormal.classList.add('border-pink-500'); });
  dropzoneNormal.addEventListener('dragleave', () => dropzoneNormal.classList.remove('border-pink-500'));
  dropzoneNormal.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzoneNormal.classList.remove('border-pink-500');
    if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files[0]);
  });

  async function handleFileUpload(file) {
    if (currentMode === 'studio' && !/^image\//.test(file.type)) { alert('ফটো স্টুডিওতে শুধু ছবি (JPG/PNG) দিন।'); return; }
    if (file.size > MAX_BYTES) { alert('ফাইলের সাইজ সর্বোচ্চ ৫০ MB হতে পারবে।'); return; }
    dropzoneNormal.classList.add('hidden');
    progressLabel.innerText = 'ফাইল আপলোড ও প্রসেসিং হচ্ছে...';
    setBar(progressBar, progressPercent, 0);
    uploadProgress.classList.remove('hidden');

    const fd = new FormData();
    fd.append('file', file);
    const res = await sendForm('/api/upload', fd, (p) => setBar(progressBar, progressPercent, p));
    uploadProgress.classList.add('hidden');
    if (res.ok) {
      uploadedFile = res.json;
      showUploadedFile();
    } else {
      alert(res.json.detail || 'ফাইল আপলোড ব্যর্থ হয়েছে।');
      dropzoneNormal.classList.remove('hidden');
      fileInput.value = '';
    }
  }

  function showUploadedFile() {
    $('fileNameText').innerText = uploadedFile.original_filename;
    $('fileMetaText').innerText = uploadedFile.is_nid
      ? 'NID কার্ড — A4 পেজে সামনে ও পেছনে (আসল মাপ ৮৫.৬×৫৪ মিমি)'
      : `মোট পেজ: ${bn(uploadedFile.page_count)} টি`;
    fileInfoBadge.classList.remove('hidden');
    const isImg = uploadedFile.file_type === 'image';
    const btnCrop = $('btnOpenCrop');
    const btnCam = $('btnOpenCamScan');
    const btnRbg = $('btnOpenRemoveBg');
    if (btnCrop) btnCrop.classList.toggle('hidden', !isImg);
    if (btnCam) btnCam.classList.toggle('hidden', !isImg);
    if (btnRbg) btnRbg.classList.toggle('hidden', !isImg);

    if (currentMode === 'studio') {
      studioCard.classList.remove('opacity-60', 'pointer-events-none');
      paymentCard.classList.remove('opacity-60', 'pointer-events-none');
      initStudio();
      calculateBill();
      return;
    }

    const isPdf = uploadedFile.file_type === 'pdf';
    // 4x6 passport sheet only makes sense for images; N-up only for PDFs
    optPhoto.classList.toggle('hidden', isPdf);
    if (isPdf) selectRadio('optDocA4');
    nupWrapper.classList.toggle('hidden', !isPdf || uploadedFile.is_nid);

    if (uploadedFile.is_nid) {
      $('nidInlinePreview').classList.remove('hidden');
      $('nidInlineImg').src = nidPreviewUrl(800);
    }

    settingsCard.classList.remove('opacity-60', 'pointer-events-none');
    paymentCard.classList.remove('opacity-60', 'pointer-events-none');
    calculateBill();
  }

  $('btnRemoveFile').addEventListener('click', () => {
    resetUploads();
    (currentMode === 'nid' ? dropzoneNid : dropzoneNormal).classList.remove('hidden');
  });

  // ── option radio groups ──────────────────────────────────────────────────
  const GROUP_IDS = [];
  function setupRadioGroup(groupIds, onChange) {
    GROUP_IDS.push(groupIds);
    groupIds.forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.addEventListener('click', () => {
        selectRadio(id);
        if (onChange) onChange();
      });
    });
  }
  function selectRadio(id) {
    const group = GROUP_IDS.find((g) => g.includes(id));
    if (!group) return;
    group.forEach((otherId) => {
      const o = $(otherId);
      if (o) {
        o.classList.remove('border-pink-600', 'bg-pink-50', 'text-pink-900');
        o.classList.add('border-gray-200', 'text-gray-700');
      }
    });
    const el = $(id);
    el.classList.add('border-pink-600', 'bg-pink-50', 'text-pink-900');
    el.classList.remove('border-gray-200', 'text-gray-700');
    el.querySelector('input').checked = true;
  }

  setupRadioGroup(['optDocA4', 'optPhoto'], () => {
    const isPhoto = checked('paper_size') === '4x6';
    duplexWrapper.style.display = isPhoto ? 'none' : '';
    nupWrapper.style.display = isPhoto ? 'none' : '';
    calculateBill();
  });
  setupRadioGroup(['optNup1', 'optNup2', 'optNup4'], calculateBill);
  setupRadioGroup(['optBw', 'optColor'], () => { calculateBill(); refreshNidInline(); });
  setupRadioGroup(['optSingle', 'optDouble'], calculateBill);
  setupRadioGroup(['optPortrait', 'optLandscape'], calculateBill);
  setupRadioGroup(['optFitCover', 'optFitContain'], refreshNidInline);
  setupRadioGroup(['payMethodCash', 'payMethodBkash', 'payMethodNagad'], () => {
    const method = checked('payment_method');
    const box = $('mfsDetailsBox');
    if (method === 'bkash') {
      box.classList.remove('hidden');
      $('mfsInstruction').innerText = 'দোকানের বিকাশ নম্বরে সেন্ড মানি করুন:';
      $('mfsNumber').innerText = BKASH_NUM;
    } else if (method === 'nagad') {
      box.classList.remove('hidden');
      $('mfsInstruction').innerText = 'দোকানের নগদ নম্বরে সেন্ড মানি করুন:';
      $('mfsNumber').innerText = NAGAD_NUM;
    } else {
      box.classList.add('hidden');
    }
  });

  $('btnMinusCopy').addEventListener('click', () => {
    const v = parseInt(inputCopies.value, 10);
    if (v > 1) { inputCopies.value = v - 1; calculateBill(); }
  });
  $('btnPlusCopy').addEventListener('click', () => {
    const v = parseInt(inputCopies.value, 10);
    if (v < 100) { inputCopies.value = v + 1; calculateBill(); }
  });

  function calculateBill() {
    if (!uploadedFile) return;
    const copies = parseInt(inputCopies.value, 10);
    const color = checked('color_mode') || 'bw';
    const colorBn = color === 'bw' ? 'সাদাকালো' : 'রঙিন';
    let total = 0;

    if (currentMode === 'studio') {
      const sp = studioParams();
      const rate = sp.paper === '4x6' ? RATES.photo_4x6 : (sp.bw ? RATES.bw_single : RATES.photo_a4);
      const sc = parseInt($('studioCopies').value, 10);
      total = rate * sc;
      billSummaryText.innerText = `ফটো শিট (${sp.paper === '4x6' ? '৪×৬' : 'A4'}) × ${bn(sc)} কপি`;
    } else if (uploadedFile.is_nid) {
      total = (color === 'bw' ? RATES.bw_single : RATES.color_single) * copies;
      billSummaryText.innerText = `NID কার্ড (${colorBn}) × ${bn(copies)} সেট`;
    } else if (checked('paper_size') === '4x6' && uploadedFile.file_type !== 'pdf') {
      total = RATES.photo_4x6 * copies;
      billSummaryText.innerText = `৪×৬ পাসপোর্ট শিট × ${bn(copies)} কপি`;
    } else {
      const duplex = checked('duplex');
      const nup = uploadedFile.file_type === 'pdf' ? parseInt(checked('pages_per_sheet') || '1', 10) : 1;
      const sheets = Math.ceil(uploadedFile.page_count / nup);
      const rate = color === 'bw'
        ? (duplex === 'double' ? RATES.bw_double : RATES.bw_single)
        : (duplex === 'double' ? RATES.color_double : RATES.color_single);
      total = rate * sheets * copies;
      billSummaryText.innerText = `${bn(sheets)} টি শিট (${nup > 1 ? nup + '-in-1' : 'স্বাভাবিক'}, ${colorBn}) × ${bn(copies)} কপি`;
    }
    totalPriceText.innerText = `৳ ${total.toFixed(2)}`;
  }

  window.copyMfsNumber = function () {
    navigator.clipboard.writeText($('mfsNumber').innerText);
    alert('নম্বর কপি করা হয়েছে!');
  };


  // ── PHOTO STUDIO ─────────────────────────────────────────────────────────
  const PRESETS = [
    { id: 'p4555', name: 'পাসপোর্ট ৪৫×৫৫ মিমি', w: 45, h: 55 },
    { id: 'p3545', name: 'পাসপোর্ট/ভিসা ৩৫×৪৫ মিমি', w: 35, h: 45 },
    { id: 'p3851', name: 'স্টুডিও পাসপোর্ট ১.৫×২ ইঞ্চি (৩৮×৫১ মিমি)', w: 38, h: 51 },
    { id: 'p2530', name: 'ই-পাসপোর্ট অনলাইন ২৫×৩০ মিমি', w: 25, h: 30 },
    { id: 'stamp', name: 'স্ট্যাম্প সাইজ ২০×২৫ মিমি', w: 20, h: 25 },
    { id: 'custom', name: 'নিজের সাইজ (মিমি)', w: 40, h: 50 }
  ];
  let studioItems = [{ preset: 'p4555', w: 45, h: 55, count: 6 }];
  let studioBg = 'none';
  let studioSuit = 'none';
  let studioBlobUrl = null;
  let studioTimer = null;
  let studioInited = false;

  function renderStudioItems() {
    const box = $('studioItems');
    box.innerHTML = '';
    studioItems.forEach((it, i) => {
      const row = document.createElement('div');
      row.className = 'border rounded-xl p-2 space-y-1.5 bg-gray-50';
      row.innerHTML = `
        <div class="flex gap-2">
          <select data-i="${i}" data-k="preset" class="flex-1 min-w-0 px-2 py-1.5 border rounded-lg text-xs bg-white">
            ${PRESETS.map((p) => `<option value="${p.id}" ${p.id === it.preset ? 'selected' : ''}>${p.name}</option>`).join('')}
          </select>
          <input type="number" data-i="${i}" data-k="count" min="1" max="60" value="${it.count}" title="কয়টি" class="w-16 px-2 py-1.5 border rounded-lg text-xs text-center">
          ${i > 0 ? `<button type="button" data-rm="${i}" class="px-2 text-red-600 font-bold">✕</button>` : ''}
        </div>
        ${it.preset === 'custom' ? `<div class="flex items-center gap-2 text-xs">
          চওড়া <input type="number" data-i="${i}" data-k="w" min="10" max="190" value="${it.w}" class="w-16 px-2 py-1 border rounded text-center"> মিমি ×
          উচ্চতা <input type="number" data-i="${i}" data-k="h" min="10" max="280" value="${it.h}" class="w-16 px-2 py-1 border rounded text-center"> মিমি</div>` : ''}`;
      box.appendChild(row);
    });
    if (studioItems.length < 12) {
      const add = document.createElement('button');
      add.type = 'button';
      add.dataset.add = '1';
      add.className = 'w-full py-1.5 border-2 border-dashed rounded-lg text-xs font-bold text-gray-500 hover:border-pink-500 hover:text-pink-600';
      add.innerText = '➕ আরো ছবির সাইজ ও সংখ্যা যোগ করুন (পাসপোর্ট / স্ট্যাম্প / কাস্টম)';
      box.appendChild(add);
    }
  }

  $('studioItems').addEventListener('input', (e) => {
    const i = e.target.dataset.i, k = e.target.dataset.k;
    if (i === undefined) return;
    const it = studioItems[+i];
    if (k === 'count') it.count = Math.max(1, Math.min(60, parseInt(e.target.value, 10) || 1));
    else if (k === 'w' || k === 'h') it[k] = Math.max(10, parseFloat(e.target.value) || 10);
    scheduleStudio();
  });
  $('studioItems').addEventListener('change', (e) => {
    if (e.target.dataset.k === 'preset') {
      const it = studioItems[+e.target.dataset.i];
      const p = PRESETS.find((x) => x.id === e.target.value);
      it.preset = p.id; it.w = p.w; it.h = p.h;
      renderStudioItems();
      scheduleStudio();
    }
  });
  $('studioItems').addEventListener('click', (e) => {
    if (e.target.dataset.rm) { studioItems.splice(+e.target.dataset.rm, 1); renderStudioItems(); scheduleStudio(); }
    else if (e.target.dataset.add) { studioItems.push({ preset: 'stamp', w: 20, h: 25, count: 8 }); renderStudioItems(); scheduleStudio(); }
  });

  function studioParams() {
    return {
      paper: $('studioPaper').value,
      items: studioItems.map((it) => ({ w: it.w, h: it.h, count: it.count })),
      fill: $('studioFill').checked,
      brightness: +$('studioBrightness').value,
      contrast: +$('studioContrast').value,
      bw: $('studioBw').value === '1',
      border: $('studioBorder').checked,
      bg: 'none',
      tol: 45,
      zoom: +$('studioZoom').value / 100,
      ox: +$('studioOx').value / 100,
      oy: +$('studioOy').value / 100,
      suit: 'none',
      suit_zoom: 1,
      suit_ox: 0,
      suit_oy: 0
    };
  }

  function scheduleStudio() {
    $('valBrightness').innerText = $('studioBrightness').value;
    $('valContrast').innerText = $('studioContrast').value;
    $('valZoom').innerText = (+$('studioZoom').value / 100).toFixed(1) + '×';
    calculateBill();
    clearTimeout(studioTimer);
    studioTimer = setTimeout(() => refreshStudio(false), 250);
  }

  let studioReq = 0;
  async function refreshStudio(force) {
    if (!uploadedFile || currentMode !== 'studio') return;
    const my = ++studioReq;
    try {
      const res = await fetch('/api/studio-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: uploadedFile.file_id, params: studioParams() })
      });
      if (!res.ok) throw new Error('preview failed');
      const info = JSON.parse(res.headers.get('X-Studio-Info') || '{}');
      const blob = await res.blob();
      if (my !== studioReq) return;
      if (studioBlobUrl) URL.revokeObjectURL(studioBlobUrl);
      studioBlobUrl = URL.createObjectURL(blob);
      $('studioPreviewImg').src = studioBlobUrl;
      const placed = (info.placed || []).map(bn).join(' + ');
      const miss = (info.requested || []).some((r, i) => r !== null && info.placed[i] < r);
      $('studioInfo').innerText = `মোট ${bn(info.total || 0)}টি ছবি বসেছে${placed ? ' (' + placed + ')' : ''}${miss ? ' ⚠️ সব ধরেনি' : ''}`;
      $('studioInfo').className = 'font-bold ' + (miss ? 'text-red-600' : 'text-pink-700');
    } catch (e) {
      $('studioInfo').innerText = '⚠️ প্রিভিউ তৈরি হয়নি';
    }
  }

  function initStudio() {
    if (!studioInited) {
      studioInited = true;
      renderStudioItems();
      ['studioBrightness', 'studioContrast', 'studioZoom', 'studioOx', 'studioOy'].forEach((id) => {
        const el = $(id);
        if (el) el.addEventListener('input', scheduleStudio);
      });
      ['studioPaper', 'studioBw', 'studioFill', 'studioBorder'].forEach((id) => {
        const el = $(id);
        if (el) el.addEventListener('change', scheduleStudio);
      });
      $('btnStudioMinus').addEventListener('click', () => { const v = +$('studioCopies').value; if (v > 1) { $('studioCopies').value = v - 1; scheduleStudio(); } });
      $('btnStudioPlus').addEventListener('click', () => { const v = +$('studioCopies').value; if (v < 100) { $('studioCopies').value = v + 1; scheduleStudio(); } });
    }
    scheduleStudio();
  }

  // ── PRINT PREVIEW MODAL ──────────────────────────────────────────────────
  const overlay = $('previewModalOverlay');
  const pagesBox = $('previewPages');
  const loadingEl = $('previewLoading');
  const pdfFrame = $('previewPdfFrame');
  const noteEl = $('previewGuideNote');
  const MAX_PREVIEW_SHEETS = 12;

  function addPreviewImage(url, caption) {
    const fig = document.createElement('figure');
    fig.className = 'space-y-1';
    const img = document.createElement('img');
    img.className = 'w-full bg-white rounded shadow border';
    img.loading = 'lazy';
    img.alt = caption;
    img.onload = () => loadingEl.classList.add('hidden');
    img.onerror = () => {
      loadingEl.classList.add('hidden');
      img.replaceWith(Object.assign(document.createElement('p'), {
        className: 'text-center text-xs text-red-600 py-6',
        innerText: '⚠️ প্রিভিউ তৈরি করা যায়নি।'
      }));
    };
    img.src = url;
    const cap = document.createElement('figcaption');
    cap.className = 'text-center text-[11px] font-semibold text-gray-600';
    cap.innerText = caption;
    fig.append(img, cap);
    pagesBox.appendChild(fig);
  }

  async function openPrintPreview() {
    if (!uploadedFile) { alert('অনুগ্রহ করে আগে ফাইল সিলেক্ট বা আপলোড করুন।'); return; }

    pagesBox.innerHTML = '';
    pdfFrame.classList.add('hidden');
    pdfFrame.src = '';
    noteEl.innerText = '';
    loadingEl.classList.remove('hidden');
    overlay.classList.remove('hidden');

    $('previewModalFileName').innerText = uploadedFile.original_filename;
    $('previewModalTotal').innerText = totalPriceText.innerText;
    const color = checked('color_mode') || 'bw';
    const badge = $('previewModeBadge');
    badge.innerText = color === 'bw' ? 'সাদাকালো (B&W)' : 'রঙিন (Color)';
    badge.className = 'text-[11px] px-2.5 py-0.5 rounded-full font-bold shrink-0 ' +
      (color === 'bw' ? 'bg-gray-200 text-gray-800' : 'bg-pink-100 text-pink-700');
    const dim = $('previewDimensionText');

    if (currentMode === 'studio') {
      const sp = studioParams();
      dim.innerText = sp.paper === '4x6' ? '৪×৬ ইঞ্চি ফটো পেপার' : 'A4 পেপার (আসল মাপে ছবি)';
      badge.innerText = sp.bw ? 'সাদাকালো (B&W)' : 'রঙিন (Color)';
      await refreshStudio(true);
      if (studioBlobUrl) addPreviewImage(studioBlobUrl, 'প্রিন্ট শিট');
      noteEl.innerText = '✂️ ছবির দাগ বরাবর কাটুন। প্রিন্টে "Actual size / 100%" দিন।';
      return;
    }

    if (uploadedFile.is_nid) {
      dim.innerText = 'NID কার্ড: ৮৫.৬ মিমি × ৫৪ মিমি (আসল মাপ)';
      // Build full URL to avoid cached errors
      const previewSrc = nidPreviewUrl(900);
      addPreviewImage(previewSrc, 'A4 পেজ — সামনে ও পেছনে (আসল মাপ)');
      noteEl.innerText = '✂️ ড্যাশ লাইন বরাবর কাটুন। প্রিন্টে "Actual size / 100%" দিন; নিচের ৫০ মিমি দাগ মেপে সাইজ মিলিয়ে নিন।';
      return;
    }

    const orient = checked('orientation') || 'portrait';
    const orientBn = orient === 'landscape' ? 'আড়াআড়ি (Landscape)' : 'লম্বালম্বি (Portrait)';
    const btnOrientIcon = $('previewOrientIcon');
    const btnOrientText = $('previewOrientText');
    if (btnOrientIcon && btnOrientText) {
      if (orient === 'landscape') {
        btnOrientIcon.innerText = '📱';
        btnOrientText.innerText = 'Portrait করুন';
      } else {
        btnOrientIcon.innerText = '🖥️';
        btnOrientText.innerText = 'Landscape করুন';
      }
    }

    if (uploadedFile.file_type === 'image') {
      const photo = checked('paper_size') === '4x6';
      dim.innerText = photo ? '৪×৬ ইঞ্চি পাসপোর্ট শিট (৬টি ফটো)' : `A4 পেপার — ${orientBn}`;
      addPreviewImage(
        `/api/preview-page/${uploadedFile.file_id}?paper_size=${photo ? '4x6' : 'A4'}&color=${color}&orientation=${orient}&t=${Date.now()}`,
        photo ? '৪×৬ ফটো শিট' : `A4 পেজ (${orientBn})`
      );
      noteEl.innerText = photo ? '✂️ ৬টি ছবি ৪×৬ ফটো পেপারে সাজানো হবে।' : '📄 ছবি A4 পাতায় ফিট করে প্রিন্ট হবে।';
      return;
    }

    // PDF
    const nup = parseInt(checked('pages_per_sheet') || '1', 10);
    dim.innerText = nup > 1
      ? `A4 পেপার (${nup}-in-1, ${bn(uploadedFile.page_count)} পাতা, ${orientBn})`
      : `A4 পেপার (${bn(uploadedFile.page_count)} টি পাতা, ${orientBn})`;
    try {
      const info = await (await fetch(`/api/preview-info/${uploadedFile.file_id}?nup=${nup}`)).json();
      if (!info.can_render) throw new Error('no renderer');
      const shown = Math.min(info.sheets, MAX_PREVIEW_SHEETS);
      for (let i = 1; i <= shown; i++) {
        addPreviewImage(
          `/api/preview-page/${uploadedFile.file_id}?page=${i}&nup=${nup}&color=${color}&orientation=${orient}&t=${Date.now()}`,
          `শিট ${bn(i)} / ${bn(info.sheets)}`
        );
      }
      if (info.sheets > shown) noteEl.innerText = `প্রথম ${bn(shown)} টি শিট দেখানো হয়েছে (মোট ${bn(info.sheets)} টি)।`;
    } catch (err) {
      // fallback: browser's own PDF viewer
      loadingEl.classList.add('hidden');
      pdfFrame.classList.remove('hidden');
      pdfFrame.src = (uploadedFile.preview_url || `/api/preview/${uploadedFile.file_id}`) + '#toolbar=0';
      noteEl.innerText = '📄 ডকুমেন্টের পাতাগুলো স্ক্রল করে দেখুন';
    }
  }

  function closePrintPreview() {
    overlay.classList.add('hidden');
    pdfFrame.src = '';
    pagesBox.innerHTML = '';
  }

  ['btnOpenPreview', 'btnOpenPreviewMini', 'btnOpenPreviewBanner'].forEach((id) => {
    const b = $(id);
    if (b) b.addEventListener('click', openPrintPreview);
  });
  $('btnClosePreviewModal').addEventListener('click', closePrintPreview);
  $('btnClosePreviewBtn').addEventListener('click', closePrintPreview);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closePrintPreview(); });
  $('btnConfirmFromPreview').addEventListener('click', () => {
    closePrintPreview();
    paymentCard.scrollIntoView({ behavior: 'smooth' });
    const btn = $('btnConfirmOrder');
    btn.classList.add('ring-4', 'ring-pink-300');
    setTimeout(() => btn.classList.remove('ring-4', 'ring-pink-300'), 1500);
  });

  // ── PLACE ORDER ──────────────────────────────────────────────────────────
  $('btnConfirmOrder').addEventListener('click', async () => {
    if (!uploadedFile) { alert('অনুগ্রহ করে ফাইল বা ছবি আপলোড করুন।'); return; }
    const paymentMethod = checked('payment_method');
    const trxId = $('trxIdInput').value.trim();
    if ((paymentMethod === 'bkash' || paymentMethod === 'nagad') && !trxId) {
      alert('অনুগ্রহ করে বিকাশের/নগদের TrxID লিখুন।');
      return;
    }

    const isNid = !!uploadedFile.is_nid;
    const btn = $('btnConfirmOrder');
    btn.disabled = true;
    const payload = {
      shop_id: SHOP_ID,
      file_id: uploadedFile.file_id,
      original_filename: uploadedFile.original_filename,
      copies: parseInt((currentMode === 'studio' ? $('studioCopies') : inputCopies).value, 10),
      paper_size: isNid ? 'NID' : (currentMode === 'studio' ? 'PHOTO' : 'A4'),
      studio: currentMode === 'studio' ? studioParams() : undefined,
      color_mode: currentMode === 'studio' ? (studioParams().bw ? 'bw' : 'color') : checked('color_mode'),
      duplex: isNid ? 'single' : checked('duplex'),
      pages_per_sheet: (!isNid && uploadedFile.file_type === 'pdf') ? parseInt(checked('pages_per_sheet') || '1', 10) : 1,
      nid_fit: checked('nid_fit') || 'cover',
      nid_rot_front: nidRot.front,
      nid_rot_back: nidRot.back,
      orientation: checked('orientation') || 'portrait',
      payment_method: paymentMethod,
      trx_id: trxId,
      customer_name: $('custNameInput').value.trim() || 'Walk-in'
    };

    try {
      const res = await fetch('/api/order/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.success) openTracker(data.order_id);
      else alert(data.detail || 'অর্ডার তৈরিতে সমস্যা হয়েছে।');
    } catch (err) {
      alert('সার্ভারে সংযোগ পাওয়া যায়নি।');
    } finally {
      btn.disabled = false;
    }
  });

  function openTracker(orderId) {
    $('trackerOverlay').classList.remove('hidden');
    $('statusOrderId').innerText = `Order ID: #${orderId}`;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const sock = new WebSocket(`${proto}//${location.host}/ws/order/${orderId}`);
    sock.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.event === 'STATUS_CHANGE') handleStatusUpdate(msg.status, msg.message);
    };
  }

  const DOT_DONE = 'w-4 h-4 rounded-full bg-green-500 text-white flex items-center justify-center text-[10px]';
  function handleStatusUpdate(status, message) {
    $('statusMessage').innerText = message;
    if (status === 'paid') {
      $('step2Dot').className = DOT_DONE; $('step2Dot').innerText = '✓';
      $('statusIcon').innerText = '🖨️'; $('statusTitle').innerText = 'পেমেন্ট সম্পন্ন';
    } else if (status === 'printing') {
      $('step2Dot').className = DOT_DONE; $('step2Dot').innerText = '✓';
      $('step3Dot').className = 'w-4 h-4 rounded-full bg-pink-600 text-white flex items-center justify-center text-[10px] pulse-dot';
      $('statusIcon').innerText = '⚡'; $('statusTitle').innerText = 'প্রিন্ট হচ্ছে...';
    } else if (status === 'completed') {
      ['step2Dot', 'step3Dot', 'step4Dot'].forEach((d) => { $(d).className = DOT_DONE; $(d).innerText = '✓'; });
      $('statusIcon').innerText = '🎉'; $('statusTitle').innerText = 'প্রিন্ট সম্পন্ন!';
      $('trackerActionBtn').classList.remove('hidden');
    } else if (status === 'cancelled') {
      $('statusIcon').innerText = '❌'; $('statusTitle').innerText = 'অর্ডার বাতিল';
      $('trackerActionBtn').classList.remove('hidden');
    }
  }

  // ── FILE TRANSFER (customer -> shop) ─────────────────────────────────────
  let trFile = null;
  $('trDropzone').addEventListener('click', () => $('trFileInput').click());
  $('trFileInput').addEventListener('change', (e) => {
    const f = e.target.files[0];
    if (!f) return;
    if (f.size > MAX_BYTES) { alert('ফাইলের সাইজ সর্বোচ্চ ৫০ MB হতে পারবে।'); e.target.value = ''; return; }
    trFile = f;
    $('trFileLabel').innerText = `✓ ${f.name} (${fmtSize(f.size)})`;
    $('trSuccess').classList.add('hidden');
  });

  $('btnTrSend').addEventListener('click', async () => {
    if (!trFile) { alert('আগে একটি ফাইল বাছাই করুন।'); return; }
    const btn = $('btnTrSend');
    btn.disabled = true;
    $('trProgress').classList.remove('hidden');
    setBar($('trBar'), $('trPercent'), 0);

    const fd = new FormData();
    fd.append('file', trFile);
    fd.append('sender_name', $('trNameInput').value.trim());
    fd.append('note', $('trNoteInput').value.trim());
    const res = await sendForm(`/api/transfer/send/${encodeURIComponent(SHOP_CODE)}`, fd, (p) => setBar($('trBar'), $('trPercent'), p));
    $('trProgress').classList.add('hidden');
    btn.disabled = false;

    if (res.ok) {
      $('trSuccessId').innerText = res.json.transfer_id;
      $('trSuccess').classList.remove('hidden');
      trFile = null;
      $('trFileInput').value = '';
      $('trFileLabel').innerText = 'আরেকটি ফাইল বাছাই করতে ক্লিক করুন';
      $('trNoteInput').value = '';
    } else {
      alert(res.json.detail || 'ফাইল পাঠানো যায়নি। আবার চেষ্টা করুন।');
    }
  });

  function goToTransfer() {
    const code = $('trCodeInput').value.trim().toUpperCase();
    if (code.length < 4) { alert('দোকানদারের দেওয়া কোডটি লিখুন।'); return; }
    location.href = '/t/' + encodeURIComponent(code);
  }
  $('btnTrGet').addEventListener('click', goToTransfer);
  $('trCodeInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') goToTransfer(); });

  // open the transfer tab directly via /p/shop#transfer
  if (location.hash === '#transfer') setMode('transfer');

  // ── Cropper.js Integration (Supports both Normal Image & NID Front/Back) ──
  let cropper = null;
  let activeRatio = 'free';
  let cropNidSide = 'front';

  function loadCropTargetImage() {
    const target = $('cropImgTarget');
    if (cropper) {
      cropper.destroy();
      cropper = null;
    }
    target.onload = () => {
      cropper = new Cropper(target, {
        viewMode: 1,
        autoCropArea: 0.92,
        responsive: true,
        background: false,
        ready() {
          setCropRatio(uploadedFile && uploadedFile.is_nid ? 'nid' : 'free');
        }
      });
    };
    const sideParam = (uploadedFile && uploadedFile.is_nid) ? `&side=${cropNidSide}` : '';
    target.src = `/api/preview/${encodeURIComponent(uploadedFile.file_id)}?t=${Date.now()}${sideParam}`;
  }

  window.switchNidCropSide = function(side) {
    cropNidSide = side === 'back' ? 'back' : 'front';
    const bf = $('btnCropSideFront');
    const bb = $('btnCropSideBack');
    if (bf && bb) {
      bf.className = cropNidSide === 'front'
        ? 'py-1.5 rounded-lg font-bold bg-pink-600 text-white shadow-sm'
        : 'py-1.5 rounded-lg font-bold text-gray-700 hover:bg-gray-200';
      bb.className = cropNidSide === 'back'
        ? 'py-1.5 rounded-lg font-bold bg-pink-600 text-white shadow-sm'
        : 'py-1.5 rounded-lg font-bold text-gray-700 hover:bg-gray-200';
    }
    loadCropTargetImage();
  };

  window.openCropModal = function(side) {
    if (!uploadedFile || uploadedFile.file_type !== 'image') return;
    const modal = $('cropModal');
    const nidSwitch = $('nidCropSideSwitch');
    if (nidSwitch) nidSwitch.classList.toggle('hidden', !uploadedFile.is_nid);
    modal.classList.remove('hidden');
    const chosenSide = (typeof side === 'string' && (side === 'front' || side === 'back')) ? side : 'front';
    if (uploadedFile.is_nid) {
      window.switchNidCropSide(chosenSide);
    } else {
      loadCropTargetImage();
    }
  };

  window.closeCropModal = function() {
    const modal = $('cropModal');
    if (modal) modal.classList.add('hidden');
    if (cropper) {
      cropper.destroy();
      cropper = null;
    }
  };

  window.setCropRatio = function(ratio) {
    activeRatio = ratio;
    const btns = {
      free: $('cropBtnFree'),
      nid: $('cropBtnNid'),
      passport: $('cropBtnPassport'),
      stamp: $('cropBtnStamp'),
      square: $('cropBtnSquare')
    };
    Object.keys(btns).forEach(k => {
      const b = btns[k];
      if (!b) return;
      const on = k === ratio;
      b.className = on
        ? 'crop-ratio-btn py-1.5 px-2 bg-pink-600 text-white font-bold rounded-lg shadow-sm'
        : 'crop-ratio-btn py-1.5 px-2 bg-gray-100 hover:bg-gray-200 text-gray-700 font-bold rounded-lg';
    });

    if (!cropper) return;
    if (ratio === 'nid') cropper.setAspectRatio(85.6 / 54);
    else if (ratio === 'passport') cropper.setAspectRatio(35 / 45);
    else if (ratio === 'stamp') cropper.setAspectRatio(20 / 25);
    else if (ratio === 'square') cropper.setAspectRatio(1);
    else cropper.setAspectRatio(NaN);
  };

  window.rotateCropper = function(deg) {
    if (cropper) cropper.rotate(deg);
  };

  window.applyCrop = function() {
    if (!uploadedFile || !cropper) return;
    const btn = $('btnSaveCrop');
    btn.disabled = true;
    btn.innerText = 'ক্রপ সংরক্ষিত হচ্ছে...';

    cropper.getCroppedCanvas({ maxWidth: 2400, maxHeight: 2400 }).toBlob(async (blob) => {
      if (!blob) {
        alert('ক্যানভাস থেকে ছবি তৈরি করা যায়নি।');
        btn.disabled = false;
        btn.innerHTML = '<span>✓</span> <span>ক্রপ সম্পন্ন করুন</span>';
        return;
      }
      const fd = new FormData();
      fd.append('file_id', uploadedFile.file_id);
      fd.append('side', cropNidSide);
      fd.append('file', blob, 'cropped.jpg');

      try {
        const res = await fetch('/api/image/save-crop', {
          method: 'POST',
          body: fd
        });
        const data = await res.json();
        if (data.success) {
          closeCropModal();
          if (uploadedFile.is_nid) refreshNidInline();
          else if (currentMode === 'studio') scheduleStudio();
          alert('✓ ছবি সফলভাবে ক্রপ সম্পন্ন হয়েছে!');
        } else {
          alert('ক্রপ সংরক্ষণ ব্যর্থ হয়েছে: ' + (data.detail || ''));
        }
      } catch (e) {
        alert('সার্ভার এরর: ' + e);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>✓</span> <span>ক্রপ সম্পন্ন করুন</span>';
      }
    }, 'image/jpeg', 0.95);
  };

  // ── CamScanner Enhancer with Live Before/After Preview ──
  let currentCamscanMode = 'magic_color';
  const CAM_LABELS = {
    original: '🖼️ আসল ছবি (Original)',
    magic_color: '✨ ম্যাজিক কালার',
    bw_doc: '🖨️ ফটোকপি (B&W)',
    grayscale: '◐ গ্রে-স্কেল'
  };

  window.openCamscanModal = function() {
    if (!uploadedFile || uploadedFile.file_type !== 'image') return;
    const modal = $('camscanModal');
    if (modal) modal.classList.remove('hidden');
    window.selectCamscanMode('magic_color');
  };

  window.closeCamscanModal = function() {
    const modal = $('camscanModal');
    if (modal) modal.classList.add('hidden');
  };

  window.selectCamscanMode = function(mode) {
    if (!uploadedFile) return;
    currentCamscanMode = mode;
    ['original', 'magic_color', 'bw_doc', 'grayscale'].forEach(m => {
      const b = $('camBtn_' + m);
      if (!b) return;
      const on = m === mode;
      b.className = on
        ? 'cam-mode-btn p-2 border-2 border-emerald-600 bg-emerald-50 text-emerald-900 rounded-xl font-bold shadow-sm'
        : 'cam-mode-btn p-2 border-2 border-gray-200 hover:border-emerald-500 rounded-xl font-bold bg-gray-50 text-gray-700';
    });

    const badge = $('camscanModeBadge');
    if (badge) badge.innerText = CAM_LABELS[mode] || mode;

    const img = $('camscanPreviewImg');
    const loader = $('camscanLoading');
    if (img && loader) {
      loader.classList.remove('hidden');
      img.classList.add('hidden');
      img.onload = () => {
        loader.classList.add('hidden');
        img.classList.remove('hidden');
      };
      img.onerror = () => {
        loader.innerText = '⚠️ প্রিভিউ লোড করা যায়নি';
      };
      img.src = `/api/image/camscan-preview?file_id=${encodeURIComponent(uploadedFile.file_id)}&mode=${encodeURIComponent(mode)}&t=${Date.now()}`;
    }
  };

  window.saveCamscanEffect = async function() {
    if (!uploadedFile) return;
    const btn = $('btnSaveCamscan');
    if (btn) {
      btn.disabled = true;
      btn.innerText = 'সেভ হচ্ছে...';
    }
    try {
      const res = await fetch('/api/image/camscan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: uploadedFile.file_id,
          mode: currentCamscanMode
        })
      });
      const data = await res.json();
      if (data.success) {
        closeCamscanModal();
        if (uploadedFile.is_nid) refreshNidInline();
        else if (currentMode === 'studio') scheduleStudio();
        alert('✓ স্ক্যান ইফেক্ট সফলভাবে সেভ হয়েছে!');
      } else {
        alert('স্ক্যানার ইফেক্ট ব্যর্থ: ' + (data.detail || ''));
      }
    } catch (e) {
      alert('স্ক্যানার ইফেক্ট প্রয়োগ ব্যর্থ হয়েছে: ' + e);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<span>✓</span> <span>স্ক্যান সেভ করুন</span>';
      }
    }
  };

  // ── Direct remove.bg Website Bridge Modal Logic ──
  let rbgNidSide = 'front';

  window.switchNidRbgSide = function(side) {
    rbgNidSide = side === 'back' ? 'back' : 'front';
    const bf = $('btnRbgSideFront');
    const bb = $('btnRbgSideBack');
    if (bf && bb) {
      bf.className = rbgNidSide === 'front'
        ? 'py-1.5 rounded-lg font-bold bg-indigo-600 text-white shadow-sm'
        : 'py-1.5 rounded-lg font-bold text-gray-700 hover:bg-gray-200';
      bb.className = rbgNidSide === 'back'
        ? 'py-1.5 rounded-lg font-bold bg-indigo-600 text-white shadow-sm'
        : 'py-1.5 rounded-lg font-bold text-gray-700 hover:bg-gray-200';
    }
  };

  window.openRemoveBgModal = function() {
    if (!uploadedFile || uploadedFile.file_type !== 'image') return;
    const modal = $('removeBgModal');
    const nidSwitch = $('nidRbgSideSwitch');
    if (nidSwitch) nidSwitch.classList.toggle('hidden', !uploadedFile.is_nid);
    if (modal) modal.classList.remove('hidden');
  };

  window.closeRemoveBgModal = function() {
    const modal = $('removeBgModal');
    if (modal) modal.classList.add('hidden');
  };

  const cutoutInput = $('cutoutFileInput');
  if (cutoutInput) {
    cutoutInput.addEventListener('change', async (e) => {
      const f = e.target.files[0];
      if (!f || !uploadedFile) return;
      const fd = new FormData();
      fd.append('file_id', uploadedFile.file_id);
      fd.append('side', rbgNidSide);
      fd.append('file', f);
      fd.append('bg_color', '#ffffff');

      try {
        const res = await fetch('/api/image/replace-cutout', {
          method: 'POST',
          body: fd
        });
        const data = await res.json();
        if (data.success) {
          closeRemoveBgModal();
          if (uploadedFile.is_nid) refreshNidInline();
          else if (currentMode === 'studio') scheduleStudio();
          alert('✓ remove.bg থেকে ডাউনলোড করা ছবি সফলভাবে যুক্ত হয়েছে!');
        } else {
          alert('আপলোড ব্যর্থ হয়েছে: ' + (data.detail || ''));
        }
      } catch (err) {
        alert('সার্ভার এরর: ' + err);
      } finally {
        cutoutInput.value = '';
      }
    });
  }

  // ── Button triggers for header bar ──
  const btnCropTrigger = $('btnOpenCrop');
  if (btnCropTrigger) btnCropTrigger.addEventListener('click', () => window.openCropModal('front'));
  const btnCamTrigger = $('btnOpenCamScan');
  if (btnCamTrigger) btnCamTrigger.addEventListener('click', window.openCamscanModal);
  const btnRbgTrigger = $('btnOpenRemoveBg');
  if (btnRbgTrigger) btnRbgTrigger.addEventListener('click', window.openRemoveBgModal);

  // ── Rotate File Logic (Image / NID / PDF 90° clockwise) ──
  async function rotateFile(showAlert = true) {
    if (!uploadedFile) return;
    const btn = $('btnRotateFile');
    if (btn) btn.disabled = true;
    try {
      const res = await fetch('/api/file/rotate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_id: uploadedFile.file_id })
      });
      const data = await res.json();
      if (data.success) {
        if (currentMode === 'studio') {
          scheduleStudio();
        } else if (uploadedFile.is_nid) {
          refreshNidInline();
        }
        if (showAlert) alert('✓ ফাইল সফলভাবে ৯০° ঘোরানো হয়েছে!');
      } else {
        if (showAlert) alert('ঘোরানো ব্যর্থ হয়েছে: ' + (data.detail || ''));
      }
    } catch (e) {
      if (showAlert) alert('সার্ভার এরর: ' + e);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  const btnRotTrigger = $('btnRotateFile');
  if (btnRotTrigger) btnRotTrigger.addEventListener('click', () => rotateFile(true));

  // ── Print Preview toolbar listeners ──
  const btnPrevRot = $('btnPreviewRotate');
  if (btnPrevRot) {
    btnPrevRot.addEventListener('click', async () => {
      btnPrevRot.disabled = true;
      await rotateFile(false);
      btnPrevRot.disabled = false;
      openPrintPreview();
    });
  }

  const btnPrevToggle = $('btnPreviewToggleOrient');
  if (btnPrevToggle) {
    btnPrevToggle.addEventListener('click', () => {
      const cur = checked('orientation') || 'portrait';
      const target = cur === 'landscape' ? 'portrait' : 'landscape';
      selectRadio(target === 'landscape' ? 'optLandscape' : 'optPortrait');
      openPrintPreview();
    });
  }

})();
