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

  const STORAGE_KEY = 'qsp_state_' + (typeof SHOP_CODE !== 'undefined' ? SHOP_CODE : 'default');
  const RECENT_ORDERS_KEY = 'qsp_recent_orders_' + (typeof SHOP_CODE !== 'undefined' ? SHOP_CODE : 'default');
  let fileRev = 1;
  let instantLocalUrl = null;
  let docInlinePage = 1;

  function bumpFileRev() {
    fileRev++;
    docInlinePage = 1;
    if (instantLocalUrl) {
      URL.revokeObjectURL(instantLocalUrl);
      instantLocalUrl = null;
    }
  }

  // Fast client-side image compression before upload (shrinks 5-12MB camera photos to ~300KB in <60ms)
  function compressImageBeforeUpload(file, maxDim = 2000, quality = 0.86) {
    return new Promise((resolve) => {
      if (!file || !/^image\/(jpeg|png|webp)$/i.test(file.type) || file.size < 350 * 1024) {
        return resolve(file);
      }
      const url = URL.createObjectURL(file);
      const im = new Image();
      im.onload = () => {
        URL.revokeObjectURL(url);
        let { width, height } = im;
        if (width <= maxDim && height <= maxDim && file.size < 700 * 1024) {
          return resolve(file);
        }
        if (width > maxDim || height > maxDim) {
          const scale = maxDim / Math.max(width, height);
          width = Math.round(width * scale);
          height = Math.round(height * scale);
        }
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, width, height);
        ctx.drawImage(im, 0, 0, width, height);
        canvas.toBlob((blob) => {
          if (!blob || blob.size >= file.size) return resolve(file);
          const cleanName = (file.name || 'photo.jpg').replace(/\.[^.]+$/, '') + '.jpg';
          resolve(new File([blob], cleanName, { type: 'image/jpeg' }));
        }, 'image/jpeg', quality);
      };
      im.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(file);
      };
      im.src = url;
    });
  }

  function saveSessionState() {
    try {
      if (!uploadedFile) {
        localStorage.removeItem(STORAGE_KEY);
        return;
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        mode: currentMode,
        uploadedFile: uploadedFile,
        nidRot: nidRot
      }));
    } catch (e) { /* ignore storage quota errors */ }
  }

  async function restoreSessionState() {
    if (location.hash === '#transfer') return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const state = JSON.parse(raw);
      if (!state || !state.uploadedFile || !state.uploadedFile.file_id) return;

      const res = await fetch(`/api/file/check/${encodeURIComponent(state.uploadedFile.file_id)}?t=${Date.now()}`);
      if (!res.ok) return;
      const check = await res.json();
      if (!check.exists) {
        localStorage.removeItem(STORAGE_KEY);
        return;
      }

      uploadedFile = state.uploadedFile;
      nidRot = state.nidRot || { front: 0, back: 0 };
      const targetMode = state.mode && state.mode !== 'transfer' ? state.mode : (uploadedFile.is_nid ? 'nid' : 'document');
      setMode(targetMode, true);
      showUploadedFile();
    } catch (e) { /* ignore */ }
  }

  function refreshTopLivePreview(forceReloadImg = false) {
    const docPrev = $('docInlinePreview');
    const nidPrev = $('nidInlinePreview');
    if (!uploadedFile) {
      if (docPrev) docPrev.classList.add('hidden');
      if (nidPrev) nidPrev.classList.add('hidden');
      return;
    }

    if (uploadedFile.is_nid) {
      if (docPrev) docPrev.classList.add('hidden');
      if (nidPrev) nidPrev.classList.remove('hidden');
      refreshNidInline();
      return;
    }

    if (nidPrev) nidPrev.classList.add('hidden');
    if (!docPrev) return;
    docPrev.classList.remove('hidden');

    const img = $('docInlineImg');
    const loader = $('docInlineLoading');
    const pageNav = $('docInlinePageNav');
    const pageSel = $('docInlinePageSelect');
    const btnPrev = $('btnDocPrevPage');
    const btnNext = $('btnDocNextPage');
    const badge = $('docInlineModeBadge');
    const sheet = $('docPaperSheet');
    const orientNote = $('docPaperOrientNote');
    const isBw = currentMode === 'studio'
      ? ($('studioBw') && $('studioBw').value === '1')
      : ((checked('color_mode') || 'bw') === 'bw');
    const orient = checked('orientation') || 'portrait';

    if (sheet) {
      if (currentMode === 'studio') {
        sheet.className = 'w-full max-w-[300px] bg-white rounded shadow-md border border-gray-300 p-2 flex items-center justify-center transition-all duration-300 overflow-hidden';
        if (orientNote) orientNote.innerText = '📸 ফটো স্টুডিওর জন্য সিলেক্ট করা ছবি';
      } else if (orient === 'landscape') {
        sheet.className = 'w-full max-w-[340px] aspect-[297/210] bg-white rounded shadow-md border-2 border-pink-300 p-2.5 flex items-center justify-center transition-all duration-300 overflow-hidden';
        if (orientNote) orientNote.innerText = '🖥️ A4 আড়াআড়ি (Landscape) পেজ লেআউট';
      } else {
        sheet.className = 'w-[220px] sm:w-[250px] aspect-[210/297] bg-white rounded shadow-md border-2 border-pink-300 p-2.5 flex items-center justify-center transition-all duration-300 overflow-hidden';
        if (orientNote) orientNote.innerText = '📱 A4 লম্বালম্বি (Portrait) পেজ লেআউট';
      }
    }

    if (badge) {
      if (currentMode === 'studio') {
        badge.innerText = isBw ? 'ফটো স্টুডিও • সাদাকালো' : 'ফটো স্টুডিও • রঙিন';
      } else {
        const orientBn = orient === 'landscape' ? 'আড়াআড়ি (Landscape)' : 'লম্বালম্বি (Portrait)';
        badge.innerText = `${isBw ? 'সাদাকালো (B&W)' : 'রঙিন (Color)'} • ${orientBn}`;
      }
      badge.className = 'text-[10px] px-2 py-0.5 rounded-full font-bold ' +
        (isBw ? 'bg-gray-200 text-gray-800' : 'bg-pink-100 text-pink-700');
    }

    if (img) {
      if (uploadedFile.file_type === 'image') {
        if (pageNav) pageNav.classList.add('hidden');
        if (loader) loader.classList.add('hidden');
        img.style.filter = isBw ? 'grayscale(100%)' : 'none';
        const revKey = `${uploadedFile.file_id}_${fileRev}`;
        if (forceReloadImg || img.dataset.revKey !== revKey) {
          img.dataset.revKey = revKey;
          img.src = instantLocalUrl || `/api/preview/${encodeURIComponent(uploadedFile.file_id)}?w=900&v=${fileRev}`;
        }
      } else {
        const nup = parseInt(checked('pages_per_sheet') || '1', 10);
        const totalSheets = Math.max(1, Math.ceil((uploadedFile.page_count || 1) / nup));
        if (docInlinePage > totalSheets) docInlinePage = totalSheets;
        if (docInlinePage < 1) docInlinePage = 1;

        if (pageNav) {
          pageNav.classList.toggle('hidden', totalSheets <= 1);
          if (pageSel) {
            if (pageSel.options.length !== totalSheets) {
              pageSel.innerHTML = '';
              for (let p = 1; p <= totalSheets; p++) {
                const opt = document.createElement('option');
                opt.value = String(p);
                opt.textContent = `${bn(p)} / ${bn(totalSheets)}`;
                pageSel.appendChild(opt);
              }
            }
            pageSel.value = String(docInlinePage);
          }
          if (btnPrev) btnPrev.disabled = docInlinePage <= 1;
          if (btnNext) btnNext.disabled = docInlinePage >= totalSheets;
        }

        const colorParam = isBw ? 'bw' : 'color';
        const pdfKey = `${uploadedFile.file_id}_${fileRev}_${colorParam}_${orient}_p${docInlinePage}_n${nup}`;
        img.style.filter = 'none';
        if (forceReloadImg || img.dataset.revKey !== pdfKey) {
          img.dataset.revKey = pdfKey;
          if (loader) loader.classList.remove('hidden');
          img.onload = () => { if (loader) loader.classList.add('hidden'); };
          img.onerror = () => { if (loader) loader.classList.add('hidden'); };
          img.src = `/api/preview-page/${encodeURIComponent(uploadedFile.file_id)}?page=${docInlinePage}&nup=${nup}&color=${colorParam}&orientation=${orient}&v=${fileRev}`;
        }
      }
    }
  }

  // Wire multi-page controls in #docInlinePreview
  if ($('btnDocPrevPage')) {
    $('btnDocPrevPage').addEventListener('click', () => {
      if (docInlinePage > 1) {
        docInlinePage--;
        refreshTopLivePreview();
      }
    });
  }
  if ($('btnDocNextPage')) {
    $('btnDocNextPage').addEventListener('click', () => {
      if (!uploadedFile) return;
      const nup = parseInt(checked('pages_per_sheet') || '1', 10);
      const totalSheets = Math.max(1, Math.ceil((uploadedFile.page_count || 1) / nup));
      if (docInlinePage < totalSheets) {
        docInlinePage++;
        refreshTopLivePreview();
      }
    });
  }
  if ($('docInlinePageSelect')) {
    $('docInlinePageSelect').addEventListener('change', (e) => {
      docInlinePage = parseInt(e.target.value, 10) || 1;
      refreshTopLivePreview();
    });
  }

  // ── mode switching ───────────────────────────────────────────────────────
  function setMode(mode, keepFile = false) {
    currentMode = mode;
    Object.keys(tabs).forEach((k) => (tabs[k].className = k === mode ? ACTIVE_TAB : IDLE_TAB));
    const isTransfer = mode === 'transfer';
    printFlow.classList.toggle('hidden', isTransfer);
    transferFlow.classList.toggle('hidden', !isTransfer);
    if (isTransfer) return;

    const isNid = mode === 'nid';
    const isStudio = mode === 'studio';
    step1Title.innerText = isNid ? 'NID / ID কার্ডের ২ পিঠের ছবি দিন'
      : isStudio ? 'যে ছবিটি দিয়ে ফটো বানাবেন সেটি দিন' : 'ডকুমেন্ট বা ছবি সিলেক্ট করুন';
    fileInput.accept = isStudio ? 'image/png,image/jpeg,image/webp' : '.pdf,image/png,image/jpeg,image/webp';
    paperTypeWrapper.classList.add('hidden');            // photo sheets now live in the Photo Studio tab
    nupWrapper.classList.toggle('hidden', isNid || isStudio);
    duplexWrapper.classList.toggle('hidden', isNid || isStudio);
    nidFitWrapper.classList.toggle('hidden', !isNid);
    settingsCard.classList.toggle('hidden', isStudio);
    studioCard.classList.toggle('hidden', !isStudio);

    // Preserve uploaded photo across document <-> studio switch or page refresh unless incompatible
    const canKeep = keepFile || (
      uploadedFile &&
      !isNid &&
      !uploadedFile.is_nid &&
      (mode === 'document' || (mode === 'studio' && uploadedFile.file_type === 'image'))
    );

    if (canKeep && uploadedFile) {
      dropzoneNormal.classList.add('hidden');
      dropzoneNid.classList.add('hidden');
      saveSessionState();
      showUploadedFile();
    } else {
      dropzoneNormal.classList.toggle('hidden', isNid);
      dropzoneNid.classList.toggle('hidden', !isNid);
      resetUploads(false);
    }
  }
  tabs.document.addEventListener('click', () => setMode('document'));
  tabs.nid.addEventListener('click', () => setMode('nid'));
  tabs.studio.addEventListener('click', () => setMode('studio'));
  tabs.transfer.addEventListener('click', () => setMode('transfer'));

  function resetUploads(clearStorage = true) {
    uploadedFile = null;
    nidFrontFile = null;
    nidBackFile = null;
    nidRot = { front: 0, back: 0 };
    if (clearStorage) {
      try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    }
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
    if ($('docInlinePreview')) $('docInlinePreview').classList.add('hidden');
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

    const [compFront, compBack] = await Promise.all([
      compressImageBeforeUpload(nidFrontFile, 1800, 0.86),
      compressImageBeforeUpload(nidBackFile, 1800, 0.86)
    ]);
    const fd = new FormData();
    fd.append('front', compFront);
    fd.append('back', compBack);
    const res = await sendForm('/api/upload-nid', fd, (p) => setBar(progressBar, progressPercent, p));
    uploadProgress.classList.add('hidden');
    if (res.ok) {
      bumpFileRev();
      uploadedFile = res.json;
      showUploadedFile();
    } else {
      alert(res.json.detail || 'NID ছবি প্রসেসিং ব্যর্থ হয়েছে।');
      dropzoneNid.classList.remove('hidden');
    }
  });

  function nidPreviewUrl(width) {
    const orient = checked('orientation') || 'portrait';
    return `/api/nid-preview/${uploadedFile.nid_token}?color=${checked('color_mode') || 'bw'}` +
      `&fit=${checked('nid_fit') || 'cover'}&rf=${nidRot.front}&rb=${nidRot.back}` +
      `&orientation=${orient}&w=${width}&t=${Date.now()}`;
  }
  let nidTimer = null;
  function refreshNidInline() {
    if (!uploadedFile || !uploadedFile.is_nid) return;
    clearTimeout(nidTimer);
    nidTimer = setTimeout(() => { $('nidInlineImg').src = nidPreviewUrl(800); }, 150);
  }
  $('btnRotFront').addEventListener('click', () => { nidRot.front = (nidRot.front + 90) % 360; saveSessionState(); refreshNidInline(); });
  $('btnRotBack').addEventListener('click', () => { nidRot.back = (nidRot.back + 90) % 360; saveSessionState(); refreshNidInline(); });

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

    // Show 0ms instant local preview for images while uploading
    bumpFileRev();
    let uploadTarget = file;
    if (/^image\//i.test(file.type)) {
      instantLocalUrl = URL.createObjectURL(file);
      const docPrev = $('docInlinePreview');
      const docImg = $('docInlineImg');
      if (docPrev && docImg) {
        docPrev.classList.remove('hidden');
        docImg.src = instantLocalUrl;
      }
      uploadTarget = await compressImageBeforeUpload(file, 2000, 0.86);
    }

    progressLabel.innerText = 'ফাইল আপলোড ও প্রসেসিং হচ্ছে...';
    setBar(progressBar, progressPercent, 0);
    uploadProgress.classList.remove('hidden');

    const fd = new FormData();
    fd.append('file', uploadTarget);
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
    saveSessionState();
    dropzoneNormal.classList.add('hidden');
    dropzoneNid.classList.add('hidden');
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

    refreshTopLivePreview();

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
  setupRadioGroup(['optNup1', 'optNup2', 'optNup4'], () => { docInlinePage = 1; calculateBill(); refreshTopLivePreview(); });
  setupRadioGroup(['optBw', 'optColor'], () => { calculateBill(); refreshTopLivePreview(); });
  setupRadioGroup(['optSingle', 'optDouble'], calculateBill);
  setupRadioGroup(['optPortrait', 'optLandscape'], () => { calculateBill(); refreshTopLivePreview(); });
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
    refreshTopLivePreview();
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
      if (info.sheets > shown) {
        noteEl.innerText = `প্রথম ${bn(shown)} টি শিট দেখানো হয়েছে (মোট ${bn(info.sheets)} টি)।`;
        const btnLoadAll = document.createElement('button');
        btnLoadAll.type = 'button';
        btnLoadAll.className = 'w-full py-2.5 bg-pink-600 hover:bg-pink-700 text-white text-xs font-bold rounded-xl shadow transition';
        btnLoadAll.innerText = `📄 বাকি সবগুলো পাতা দেখুন (${bn(shown + 1)} থেকে ${bn(info.sheets)} পর্যন্ত)`;
        btnLoadAll.addEventListener('click', () => {
          btnLoadAll.remove();
          for (let j = shown + 1; j <= info.sheets; j++) {
            addPreviewImage(
              `/api/preview-page/${uploadedFile.file_id}?page=${j}&nup=${nup}&color=${color}&orientation=${orient}&t=${Date.now()}`,
              `শিট ${bn(j)} / ${bn(info.sheets)}`
            );
          }
          noteEl.innerText = `সবগুলো (${bn(info.sheets)} টি) শিট দেখানো হচ্ছে।`;
        });
        pagesBox.appendChild(btnLoadAll);
      }
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

  // ── RECENT ORDERS & LIVE TRACKER ─────────────────────────────────────────
  let activeTrackerOrderId = null;
  const orderSockets = {};

  function loadRecentOrders() {
    try {
      const raw = localStorage.getItem(RECENT_ORDERS_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) {
      return [];
    }
  }

  function saveRecentOrders(list) {
    try {
      localStorage.setItem(RECENT_ORDERS_KEY, JSON.stringify((list || []).slice(0, 15)));
    } catch (e) {}
  }

  function recordRecentOrder(orderObj) {
    const list = loadRecentOrders().filter((x) => x && x.id !== orderObj.id);
    list.unshift(orderObj);
    saveRecentOrders(list);
    renderRecentOrdersPanel();
    ensureOrderSocket(orderObj.id);
  }

  function updateRecentOrderStatus(orderId, status, message) {
    const list = loadRecentOrders();
    let changed = false;
    list.forEach((item) => {
      if (item && item.id === orderId) {
        if (status && item.status !== status) {
          item.status = status;
          changed = true;
        }
        if (message) {
          item.message = message;
          changed = true;
        }
      }
    });
    if (changed) {
      saveRecentOrders(list);
    }
    renderRecentOrdersPanel();
    if (activeTrackerOrderId === orderId) {
      applyStatusToTrackerModal(status, message);
    }
  }

  function getStatusMeta(status) {
    if (status === 'paid') {
      return {
        badge: '💳 পেইড / প্রিন্টের অপেক্ষায়',
        cls: 'bg-blue-100 text-blue-800 border-blue-200',
        defaultMsg: 'পেমেন্ট গৃহীত হয়েছে। দোকানদার প্রিন্ট শুরু করছেন...'
      };
    }
    if (status === 'printing') {
      return {
        badge: '⚡ প্রিন্ট হচ্ছে...',
        cls: 'bg-pink-100 text-pink-700 border-pink-200 animate-pulse',
        defaultMsg: 'প্রিন্টারে আপনার ফাইলটি প্রিন্ট হচ্ছে...'
      };
    }
    if (status === 'completed') {
      return {
        badge: '✓ প্রিন্ট সম্পন্ন',
        cls: 'bg-green-100 text-green-800 border-green-200',
        defaultMsg: 'আপনার প্রিন্ট সফলভাবে সম্পন্ন হয়েছে! কাউন্টার থেকে কাগজ সংগ্রহ করুন।'
      };
    }
    if (status === 'cancelled') {
      return {
        badge: '❌ বাতিল',
        cls: 'bg-red-100 text-red-700 border-red-200',
        defaultMsg: 'এই প্রিন্ট অর্ডারটি বাতিল করা হয়েছে।'
      };
    }
    return {
      badge: '⏳ পেমেন্ট অপেক্ষমাণ',
      cls: 'bg-amber-100 text-amber-800 border-amber-200',
      defaultMsg: 'কাউন্টার থেকে পেমেন্ট ও অর্ডার নিশ্চিত করা হচ্ছে। অনুগ্রহ করে অপেক্ষা করুন...'
    };
  }

  function renderRecentOrdersPanel() {
    const list = loadRecentOrders();
    const badge = $('recentPrintsBadge');
    const box = $('recentPrintsList');
    if (badge) {
      if (list.length > 0) {
        badge.innerText = bn(list.length);
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }
    if (!box) return;
    if (list.length === 0) {
      box.innerHTML = '<p class="text-xs text-gray-400 text-center py-4">এখনো কোনো প্রিন্ট অর্ডার পাঠানো হয়নি।</p>';
      return;
    }
    box.innerHTML = '';
    list.forEach((item) => {
      const meta = getStatusMeta(item.status);
      const row = document.createElement('div');
      row.className = 'p-2.5 rounded-xl bg-gray-50 hover:bg-pink-50/40 border border-gray-200/80 flex items-center justify-between gap-2 cursor-pointer transition';
      row.innerHTML = `
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-1.5">
            <span class="font-bold text-xs text-gray-800 truncate">${item.original_filename || 'ডকুমেন্ট'}</span>
            <span class="text-[10px] font-mono text-gray-400 shrink-0">#${item.id}</span>
          </div>
          <div class="text-[11px] text-gray-500 mt-0.5 flex items-center gap-2 flex-wrap">
            <span>${bn(item.copies || 1)} কপি</span>
            <span>•</span>
            <span class="font-bold text-pink-600">৳ ${Number(item.total_price || 0).toFixed(0)}</span>
            ${item.created_at ? `<span>• ${item.created_at}</span>` : ''}
          </div>
        </div>
        <div class="flex flex-col items-end gap-1 shrink-0">
          <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${meta.cls}">${meta.badge}</span>
          <span class="text-[10px] text-pink-600 font-bold underline">বিস্তারিত দেখুন ➔</span>
        </div>
      `;
      row.addEventListener('click', () => {
        openTracker(item.id);
      });
      box.appendChild(row);
    });
  }

  async function syncRecentOrdersStatus() {
    const list = loadRecentOrders();
    if (!list.length) {
      renderRecentOrdersPanel();
      return;
    }
    const ids = list.map((x) => x.id).filter(Boolean).join(',');
    try {
      const res = await fetch(`/api/orders/status?ids=${encodeURIComponent(ids)}&t=${Date.now()}`);
      if (!res.ok) return;
      const data = await res.json();
      if (Array.isArray(data.orders)) {
        const map = {};
        data.orders.forEach((o) => { map[o.id] = o; });
        list.forEach((item) => {
          const srv = map[item.id];
          if (srv) {
            item.status = srv.status;
            item.original_filename = srv.original_filename || item.original_filename;
            item.copies = srv.copies || item.copies;
            item.total_price = srv.total_price !== undefined ? srv.total_price : item.total_price;
          }
        });
        saveRecentOrders(list);
        renderRecentOrdersPanel();
        if (activeTrackerOrderId && map[activeTrackerOrderId]) {
          applyStatusToTrackerModal(map[activeTrackerOrderId].status, null);
        }
      }
    } catch (e) {}
    // Ensure active orders have live WebSockets connected
    list.forEach((item) => {
      if (item && item.id && item.status !== 'completed' && item.status !== 'cancelled') {
        ensureOrderSocket(item.id);
      }
    });
  }

  function ensureOrderSocket(orderId) {
    if (!orderId || orderSockets[orderId]) return;
    try {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const sock = new WebSocket(`${proto}//${location.host}/ws/order/${orderId}`);
      orderSockets[orderId] = sock;
      sock.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.event === 'STATUS_CHANGE') {
            updateRecentOrderStatus(orderId, msg.status, msg.message);
          }
        } catch (e) {}
      };
      sock.onclose = () => {
        delete orderSockets[orderId];
      };
    } catch (e) {}
  }

  if ($('btnToggleRecentPrints')) {
    $('btnToggleRecentPrints').addEventListener('click', () => {
      const panel = $('recentPrintsPanel');
      if (!panel) return;
      const isHidden = panel.classList.contains('hidden');
      panel.classList.toggle('hidden', !isHidden);
      if (isHidden) {
        syncRecentOrdersStatus();
      }
    });
  }
  if ($('btnCloseRecentPrints')) {
    $('btnCloseRecentPrints').addEventListener('click', () => {
      if ($('recentPrintsPanel')) $('recentPrintsPanel').classList.add('hidden');
    });
  }
  if ($('btnRefreshRecentPrints')) {
    $('btnRefreshRecentPrints').addEventListener('click', () => {
      syncRecentOrdersStatus();
    });
  }

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
    const origBtnHtml = btn.innerHTML;
    btn.disabled = true;
    btn.classList.add('opacity-80', 'cursor-wait');
    btn.innerHTML = '<span class="inline-block animate-spin mr-1.5">⏳</span> <span>প্রিন্টের জন্য পাঠানো হচ্ছে... অনুগ্রহ করে অপেক্ষা করুন</span>';

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
      printer_target: (currentMode === 'studio'
        ? ($('studioPrinterSelect') ? $('studioPrinterSelect').value : '')
        : ($('printerSelect') ? $('printerSelect').value : '')) || '',
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
      if (data.success) {
        recordRecentOrder({
          id: data.order_id,
          original_filename: uploadedFile.original_filename,
          copies: payload.copies,
          total_price: data.total_price,
          status: 'pending_payment',
          created_at: new Date().toLocaleTimeString('bn-BD', { hour: '2-digit', minute: '2-digit' })
        });
        openTracker(data.order_id);
      } else {
        alert(data.detail || 'অর্ডার তৈরিতে সমস্যা হয়েছে।');
      }
    } catch (err) {
      alert('সার্ভারে সংযোগ পাওয়া যায়নি।');
    } finally {
      btn.disabled = false;
      btn.classList.remove('opacity-80', 'cursor-wait');
      btn.innerHTML = origBtnHtml;
    }
  });

  const DOT_DONE = 'w-4 h-4 rounded-full bg-green-500 text-white flex items-center justify-center text-[10px]';
  const DOT_IDLE = 'w-4 h-4 rounded-full bg-gray-300 text-white flex items-center justify-center text-[10px]';

  function resetTrackerDots() {
    if ($('step2Dot')) { $('step2Dot').className = DOT_IDLE; $('step2Dot').innerText = '2'; }
    if ($('step3Dot')) { $('step3Dot').className = DOT_IDLE; $('step3Dot').innerText = '3'; }
    if ($('step4Dot')) { $('step4Dot').className = DOT_IDLE; $('step4Dot').innerText = '4'; }
    if ($('statusIcon')) $('statusIcon').innerText = '⏳';
    if ($('statusTitle')) $('statusTitle').innerText = 'অর্ডার গ্রহণ করা হয়েছে';
    if ($('statusMessage')) $('statusMessage').innerText = 'কাউন্টার থেকে পেমেন্ট নিশ্চিত করা হচ্ছে। অনুগ্রহ করে অপেক্ষা করুন...';
  }

  function applyStatusToTrackerModal(status, message) {
    resetTrackerDots();
    const meta = getStatusMeta(status);
    if ($('statusMessage')) $('statusMessage').innerText = message || meta.defaultMsg;
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
    } else if (status === 'cancelled') {
      $('statusIcon').innerText = '❌'; $('statusTitle').innerText = 'অর্ডার বাতিল';
    }
  }

  function openTracker(orderId) {
    activeTrackerOrderId = orderId;
    $('trackerOverlay').classList.remove('hidden');
    $('statusOrderId').innerText = `Order ID: #${orderId}`;
    const existing = loadRecentOrders().find((x) => x && x.id === orderId);
    applyStatusToTrackerModal(existing ? existing.status : 'pending_payment', existing ? existing.message : null);
    ensureOrderSocket(orderId);
    syncRecentOrdersStatus();
  }

  function closeTrackerOverlay() {
    $('trackerOverlay').classList.add('hidden');
    if ($('recentPrintsPanel')) {
      $('recentPrintsPanel').classList.remove('hidden');
    }
    renderRecentOrdersPanel();
  }

  function startNewPrintFromTracker() {
    $('trackerOverlay').classList.add('hidden');
    resetUploads(true);
    (currentMode === 'nid' ? dropzoneNid : dropzoneNormal).classList.remove('hidden');
    if ($('recentPrintsPanel')) {
      $('recentPrintsPanel').classList.remove('hidden');
    }
    renderRecentOrdersPanel();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  if ($('btnCloseTrackerTop')) $('btnCloseTrackerTop').addEventListener('click', closeTrackerOverlay);
  if ($('btnTrackerGoBack')) $('btnTrackerGoBack').addEventListener('click', closeTrackerOverlay);
  if ($('btnTrackerNewPrint')) $('btnTrackerNewPrint').addEventListener('click', startNewPrintFromTracker);

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

  function joinDeviceRoomDirect() {
    const inp = $('roomJoinCodeDirect');
    if (!inp) return;
    const raw = inp.value.trim();
    const match = raw.match(/\/r\/([A-Za-z0-9]{4,12})/i);
    const code = (match ? match[1] : raw).toUpperCase();
    if (code.length < 4) { alert('অন্য ডিভাইসের রুম কোডটি লিখুন।'); return; }
    location.href = '/r/' + encodeURIComponent(code);
  }
  if ($('btnRoomJoinDirect')) $('btnRoomJoinDirect').addEventListener('click', joinDeviceRoomDirect);
  if ($('roomJoinCodeDirect')) $('roomJoinCodeDirect').addEventListener('keydown', (e) => { if (e.key === 'Enter') joinDeviceRoomDirect(); });

  // open the transfer tab directly via /p/shop#transfer
  if (location.hash === '#transfer') setMode('transfer');

  // ── Smart 4-Corner Perspective Crop (CamScanner Style) + Normal Cropper.js ──
  let cropper = null;
  let cropMode = 'smart'; // 'smart' | 'normal'
  let activeRatio = 'free';
  let smartTargetRatio = 'auto';
  let cropNidSide = 'front';

  let smartImg = new Image();
  let smartPts = [
    { x: 0.07, y: 0.07 },
    { x: 0.93, y: 0.07 },
    { x: 0.93, y: 0.93 },
    { x: 0.07, y: 0.93 }
  ];
  let smartGeom = { pad: 16, drawW: 300, drawH: 300 };
  let activeDragIdx = -1; // 0..3 = corners, 4..7 = edge midpoints

  function resetSmartPtsDefault() {
    smartPts = [
      { x: 0.07, y: 0.07 },
      { x: 0.93, y: 0.07 },
      { x: 0.93, y: 0.93 },
      { x: 0.07, y: 0.93 }
    ];
  }

  window.autoDetectSmartCorners = async function() {
    if (!uploadedFile) return;
    try {
      const res = await fetch(`/api/image/detect-corners?file_id=${encodeURIComponent(uploadedFile.file_id)}&side=${encodeURIComponent(cropNidSide)}&t=${Date.now()}`);
      const data = await res.json();
      if (data.success && Array.isArray(data.points) && data.points.length === 4) {
        smartPts = data.points.map(p => ({ x: +p.x, y: +p.y }));
        drawSmartCrop();
      }
    } catch (e) {
      drawSmartCrop();
    }
  };

  function bilerp(p0, p1, p2, p3, u, v) {
    const tx = p0.x * (1 - u) + p1.x * u;
    const ty = p0.y * (1 - u) + p1.y * u;
    const bx = p3.x * (1 - u) + p2.x * u;
    const by = p3.y * (1 - u) + p2.y * u;
    return { x: tx * (1 - v) + bx * v, y: ty * (1 - v) + by * v };
  }

  function drawSmartCrop() {
    const canvas = $('smartCropCanvas');
    const cont = $('smartCropContainer');
    if (!canvas || !cont || !smartImg.width) return;

    const pad = 16;
    const availW = Math.max(200, (cont.clientWidth || 360) - pad * 2 - 4);
    const availH = Math.max(200, (cont.clientHeight || 340) - pad * 2 - 4);
    const scale = Math.min(availW / smartImg.width, availH / smartImg.height);
    const drawW = Math.round(smartImg.width * scale);
    const drawH = Math.round(smartImg.height * scale);

    canvas.width = drawW + pad * 2;
    canvas.height = drawH + pad * 2;
    smartGeom = { pad, drawW, drawH };

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 1. Draw image
    ctx.drawImage(smartImg, pad, pad, drawW, drawH);

    const cPts = smartPts.map(p => ({
      x: pad + p.x * drawW,
      y: pad + p.y * drawH
    }));

    // 2. Darken outside polygon
    ctx.save();
    ctx.fillStyle = 'rgba(0, 0, 0, 0.58)';
    ctx.beginPath();
    ctx.rect(pad, pad, drawW, drawH);
    ctx.moveTo(cPts[0].x, cPts[0].y);
    ctx.lineTo(cPts[1].x, cPts[1].y);
    ctx.lineTo(cPts[2].x, cPts[2].y);
    ctx.lineTo(cPts[3].x, cPts[3].y);
    ctx.closePath();
    ctx.fill('evenodd');
    ctx.restore();

    // 3. Perspective 3x3 internal guide lines
    ctx.save();
    ctx.strokeStyle = 'rgba(16, 185, 129, 0.45)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    [1 / 3, 2 / 3].forEach(t => {
      const vTop = bilerp(cPts[0], cPts[1], cPts[2], cPts[3], t, 0);
      const vBot = bilerp(cPts[0], cPts[1], cPts[2], cPts[3], t, 1);
      ctx.beginPath(); ctx.moveTo(vTop.x, vTop.y); ctx.lineTo(vBot.x, vBot.y); ctx.stroke();

      const hLeft = bilerp(cPts[0], cPts[1], cPts[2], cPts[3], 0, t);
      const hRight = bilerp(cPts[0], cPts[1], cPts[2], cPts[3], 1, t);
      ctx.beginPath(); ctx.moveTo(hLeft.x, hLeft.y); ctx.lineTo(hRight.x, hRight.y); ctx.stroke();
    });
    ctx.restore();

    // 4. Main Quadrilateral Border
    ctx.save();
    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(cPts[0].x, cPts[0].y);
    ctx.lineTo(cPts[1].x, cPts[1].y);
    ctx.lineTo(cPts[2].x, cPts[2].y);
    ctx.lineTo(cPts[3].x, cPts[3].y);
    ctx.closePath();
    ctx.stroke();
    ctx.restore();

    // 5. Edge midpoint handles
    for (let i = 0; i < 4; i++) {
      const a = cPts[i];
      const b = cPts[(i + 1) % 4];
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      ctx.beginPath();
      ctx.arc(mx, my, 6.5, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#059669';
      ctx.stroke();
    }

    // 6. 4 Corner handles (large touch-friendly circles)
    cPts.forEach((pt, idx) => {
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 11, 0, Math.PI * 2);
      ctx.fillStyle = idx === activeDragIdx ? '#ec4899' : '#10b981';
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 3, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.fill();
    });
  }

  function updateLoupe(normPt) {
    const loupe = $('smartLoupeCanvas');
    if (!loupe || !smartImg.width) return;
    loupe.classList.remove('hidden');
    // Move loupe to top-right if finger is in top-left
    if (normPt.x < 0.42 && normPt.y < 0.42) {
      loupe.style.left = 'auto';
      loupe.style.right = '10px';
    } else {
      loupe.style.right = 'auto';
      loupe.style.left = '10px';
    }

    const lctx = loupe.getContext('2d');
    const lw = loupe.width, lh = loupe.height;
    lctx.clearRect(0, 0, lw, lh);
    lctx.fillStyle = '#0f172a';
    lctx.fillRect(0, 0, lw, lh);

    const srcX = normPt.x * smartImg.width;
    const srcY = normPt.y * smartImg.height;
    const zoomRadius = Math.max(24, Math.min(smartImg.width, smartImg.height) * 0.08);

    lctx.drawImage(
      smartImg,
      srcX - zoomRadius, srcY - zoomRadius, zoomRadius * 2, zoomRadius * 2,
      0, 0, lw, lh
    );

    // Crosshair inside loupe
    lctx.strokeStyle = '#10b981';
    lctx.lineWidth = 1.5;
    lctx.beginPath();
    lctx.moveTo(lw / 2, 0); lctx.lineTo(lw / 2, lh);
    lctx.moveTo(0, lh / 2); lctx.lineTo(lw, lh / 2);
    lctx.stroke();
  }

  function initSmartCanvasEvents() {
    const canvas = $('smartCropCanvas');
    if (!canvas || canvas.dataset.bound) return;
    canvas.dataset.bound = '1';

    function getCanvasPos(e) {
      const rect = canvas.getBoundingClientRect();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      return {
        x: (clientX - rect.left) * scaleX,
        y: (clientY - rect.top) * scaleY
      };
    }

    function onStart(e) {
      if (!smartImg.width) return;
      const pos = getCanvasPos(e);
      const { pad, drawW, drawH } = smartGeom;
      const cPts = smartPts.map(p => ({ x: pad + p.x * drawW, y: pad + p.y * drawH }));

      // Find nearest corner (0..3)
      let bestIdx = -1;
      let bestDist = 36; // generous touch radius
      cPts.forEach((pt, i) => {
        const d = Math.hypot(pos.x - pt.x, pos.y - pt.y);
        if (d < bestDist) { bestDist = d; bestIdx = i; }
      });

      // Check edge midpoints (4..7) if no corner was closer
      if (bestIdx === -1) {
        for (let i = 0; i < 4; i++) {
          const a = cPts[i], b = cPts[(i + 1) % 4];
          const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
          const d = Math.hypot(pos.x - mx, pos.y - my);
          if (d < 30) { bestDist = d; bestIdx = 4 + i; break; }
        }
      }

      if (bestIdx !== -1) {
        if (e.cancelable) e.preventDefault();
        activeDragIdx = bestIdx;
        onMove(e);
      }
    }

    function onMove(e) {
      if (activeDragIdx === -1) return;
      if (e.cancelable) e.preventDefault();
      const pos = getCanvasPos(e);
      const { pad, drawW, drawH } = smartGeom;
      const nx = Math.max(0.0, Math.min(1.0, (pos.x - pad) / drawW));
      const ny = Math.max(0.0, Math.min(1.0, (pos.y - pad) / drawH));

      if (activeDragIdx < 4) {
        smartPts[activeDragIdx] = { x: nx, y: ny };
        updateLoupe(smartPts[activeDragIdx]);
      } else {
        // Edge midpoint: move the two endpoints along the axis
        const edge = activeDragIdx - 4;
        const i1 = edge;
        const i2 = (edge + 1) % 4;
        const midX = (smartPts[i1].x + smartPts[i2].x) / 2;
        const midY = (smartPts[i1].y + smartPts[i2].y) / 2;
        const dx = nx - midX;
        const dy = ny - midY;
        smartPts[i1].x = Math.max(0, Math.min(1, smartPts[i1].x + dx));
        smartPts[i1].y = Math.max(0, Math.min(1, smartPts[i1].y + dy));
        smartPts[i2].x = Math.max(0, Math.min(1, smartPts[i2].x + dx));
        smartPts[i2].y = Math.max(0, Math.min(1, smartPts[i2].y + dy));
        updateLoupe({ x: nx, y: ny });
      }
      drawSmartCrop();
    }

    function onEnd() {
      activeDragIdx = -1;
      const loupe = $('smartLoupeCanvas');
      if (loupe) loupe.classList.add('hidden');
      drawSmartCrop();
    }

    canvas.addEventListener('mousedown', onStart);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onEnd);

    canvas.addEventListener('touchstart', onStart, { passive: false });
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('touchend', onEnd);
  }

  window.setSmartRatio = function(r) {
    smartTargetRatio = r;
    ['auto', 'nid', 'a4', 'passport', 'square'].forEach(k => {
      const b = $('smartBtn_' + k);
      if (!b) return;
      const on = k === r;
      b.className = on
        ? 'smart-ratio-btn py-1.5 px-1.5 bg-emerald-600 text-white font-bold rounded-lg shadow-sm'
        : 'smart-ratio-btn py-1.5 px-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 font-bold rounded-lg';
    });
  };

  window.setCropMode = function(mode) {
    cropMode = mode === 'normal' ? 'normal' : 'smart';
    const bSmart = $('btnCropModeSmart');
    const bNorm = $('btnCropModeNormal');
    const secSmart = $('smartCropSection');
    const secNorm = $('normalCropSection');

    if (bSmart && bNorm) {
      bSmart.className = cropMode === 'smart'
        ? 'py-1.5 rounded-lg font-bold bg-emerald-600 text-white shadow-sm flex items-center justify-center gap-1'
        : 'py-1.5 rounded-lg font-bold text-gray-700 hover:bg-gray-200 flex items-center justify-center gap-1';
      bNorm.className = cropMode === 'normal'
        ? 'py-1.5 rounded-lg font-bold bg-pink-600 text-white shadow-sm flex items-center justify-center gap-1'
        : 'py-1.5 rounded-lg font-bold text-gray-700 hover:bg-gray-200 flex items-center justify-center gap-1';
    }
    if (secSmart) secSmart.classList.toggle('hidden', cropMode !== 'smart');
    if (secNorm) secNorm.classList.toggle('hidden', cropMode !== 'normal');

    if (cropMode === 'normal') {
      initNormalCropper();
    } else {
      drawSmartCrop();
    }
  };

  function initNormalCropper() {
    const target = $('cropImgTarget');
    if (cropper) {
      cropper.destroy();
      cropper = null;
    }
    const sideParam = (uploadedFile && uploadedFile.is_nid) ? `&side=${cropNidSide}` : '';
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
    target.src = `/api/preview/${encodeURIComponent(uploadedFile.file_id)}?w=1000&v=${fileRev}${sideParam}`;
  }

  function loadCropTargetImage() {
    initSmartCanvasEvents();
    resetSmartPtsDefault();
    const sideParam = (uploadedFile && uploadedFile.is_nid) ? `&side=${cropNidSide}` : '';
    const imgUrl = `/api/preview/${encodeURIComponent(uploadedFile.file_id)}?w=1000&v=${fileRev}${sideParam}`;

    smartImg = new Image();
    smartImg.crossOrigin = 'anonymous';
    smartImg.onload = () => {
      drawSmartCrop();
      window.autoDetectSmartCorners();
    };
    smartImg.src = imgUrl;

    if (cropMode === 'normal') {
      initNormalCropper();
    }
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

  let pendingCropBlob = null;
  let pendingCropPreviewUrl = null;

  window.backToCropEditor = function() {
    if ($('cropLiveResultView')) $('cropLiveResultView').classList.add('hidden');
    if ($('cropFooterStep2')) $('cropFooterStep2').classList.add('hidden');
    if ($('cropEditorView')) $('cropEditorView').classList.remove('hidden');
    if ($('cropFooterStep1')) $('cropFooterStep1').classList.remove('hidden');
    if (cropMode === 'smart') {
      setTimeout(drawSmartCrop, 30);
    }
  };

  function showCropStep2LiveResult(blobUrl) {
    const resImg = $('cropResultPreviewImg');
    if (resImg) resImg.src = blobUrl;
    if ($('cropEditorView')) $('cropEditorView').classList.add('hidden');
    if ($('cropFooterStep1')) $('cropFooterStep1').classList.add('hidden');
    if ($('cropLiveResultView')) $('cropLiveResultView').classList.remove('hidden');
    if ($('cropFooterStep2')) $('cropFooterStep2').classList.remove('hidden');
  }

  window.openCropModal = function(side) {
    if (!uploadedFile || uploadedFile.file_type !== 'image') return;
    const modal = $('cropModal');
    const nidSwitch = $('nidCropSideSwitch');
    if (nidSwitch) nidSwitch.classList.toggle('hidden', !uploadedFile.is_nid);
    modal.classList.remove('hidden');

    window.backToCropEditor();
    window.setSmartRatio(uploadedFile.is_nid ? 'nid' : 'auto');
    window.setCropMode('smart');

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
    if (pendingCropPreviewUrl) {
      URL.revokeObjectURL(pendingCropPreviewUrl);
      pendingCropPreviewUrl = null;
    }
    pendingCropBlob = null;
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

  // Step 1 -> Step 2: Generate straightened/cropped image and show it LIVE inside the modal
  window.previewCroppedImage = async function() {
    if (!uploadedFile) return;
    const btn = $('btnPreviewCrop');
    if (btn) {
      btn.disabled = true;
      btn.innerText = 'সোজা ও ক্রপ করা হচ্ছে...';
    }

    if (cropMode === 'smart') {
      try {
        const res = await fetch('/api/image/perspective-crop-preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            file_id: uploadedFile.file_id,
            side: cropNidSide,
            points: smartPts,
            ratio: smartTargetRatio
          })
        });
        if (!res.ok) {
          let errMsg = 'স্মার্ট ক্রপ ব্যর্থ হয়েছে';
          try { const j = await res.json(); errMsg = j.detail || errMsg; } catch (e) {}
          alert(errMsg);
          return;
        }
        const blob = await res.blob();
        pendingCropBlob = blob;
        if (pendingCropPreviewUrl) URL.revokeObjectURL(pendingCropPreviewUrl);
        pendingCropPreviewUrl = URL.createObjectURL(blob);
        showCropStep2LiveResult(pendingCropPreviewUrl);
      } catch (e) {
        alert('সার্ভার এরর: ' + e);
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<span>✂️</span> <span>ক্রপ ও সোজা করুন</span>';
        }
      }
      return;
    }

    // Normal Cropper.js mode
    if (!cropper) {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<span>✂️</span> <span>ক্রপ ও সোজা করুন</span>';
      }
      return;
    }

    cropper.getCroppedCanvas({ maxWidth: 2400, maxHeight: 2400 }).toBlob((blob) => {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<span>✂️</span> <span>ক্রপ ও সোজা করুন</span>';
      }
      if (!blob) {
        alert('ক্যানভাস থেকে ছবি তৈরি করা যায়নি।');
        return;
      }
      pendingCropBlob = blob;
      if (pendingCropPreviewUrl) URL.revokeObjectURL(pendingCropPreviewUrl);
      pendingCropPreviewUrl = URL.createObjectURL(blob);
      showCropStep2LiveResult(pendingCropPreviewUrl);
    }, 'image/jpeg', 0.95);
  };

  // Step 2 "OK" button: Save cropped/straightened image and go back to print page with live preview updated
  window.confirmAndSaveCrop = async function() {
    if (!uploadedFile) return;
    const btn = $('btnConfirmCropOk');
    if (btn) {
      btn.disabled = true;
      btn.innerText = 'সেভ হচ্ছে...';
    }

    try {
      if (cropMode === 'smart') {
        const res = await fetch('/api/image/perspective-crop', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            file_id: uploadedFile.file_id,
            side: cropNidSide,
            points: smartPts,
            ratio: smartTargetRatio
          })
        });
        const data = await res.json();
        if (!data.success) {
          alert('স্মার্ট ক্রপ সেভ ব্যর্থ হয়েছে: ' + (data.detail || ''));
          return;
        }
      } else {
        if (!pendingCropBlob) {
          alert('ক্রপ করা ছবি পাওয়া যায়নি।');
          return;
        }
        const fd = new FormData();
        fd.append('file_id', uploadedFile.file_id);
        fd.append('side', cropNidSide);
        fd.append('file', pendingCropBlob, 'cropped.jpg');

        const res = await fetch('/api/image/save-crop', {
          method: 'POST',
          body: fd
        });
        const data = await res.json();
        if (!data.success) {
          alert('ক্রপ সংরক্ষণ ব্যর্থ হয়েছে: ' + (data.detail || ''));
          return;
        }
      }

      bumpFileRev();
      if (!uploadedFile.is_nid && pendingCropBlob) {
        instantLocalUrl = URL.createObjectURL(pendingCropBlob);
      }
      closeCropModal();
      refreshTopLivePreview(true);
      if (currentMode === 'studio') scheduleStudio();
    } catch (e) {
      alert('সার্ভার এরর: ' + e);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<span>✓</span> <span>ওকে (প্রিন্ট পেইজে যান)</span>';
      }
    }
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
        bumpFileRev();
        closeCamscanModal();
        refreshTopLivePreview(true);
        if (currentMode === 'studio') scheduleStudio();
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
          bumpFileRev();
          closeRemoveBgModal();
          refreshTopLivePreview(true);
          if (currentMode === 'studio') scheduleStudio();
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
        bumpFileRev();
        refreshTopLivePreview(true);
        if (currentMode === 'studio') {
          scheduleStudio();
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
      refreshTopLivePreview();
      openPrintPreview();
    });
  }

  // Restore previously uploaded file across page refresh
  restoreSessionState();

  // Initialize Recent Prints panel & live status sync on load
  renderRecentOrdersPanel();
  syncRecentOrdersStatus().then(() => {
    const hasActive = loadRecentOrders().some((x) => x && (x.status === 'pending_payment' || x.status === 'paid' || x.status === 'printing'));
    if (hasActive && $('recentPrintsPanel')) {
      $('recentPrintsPanel').classList.remove('hidden');
    }
  });

})();
