const $ = (id) => document.getElementById(id);
const views = ["overview", "verify", "registry", "qr-tools"];
let cameraStream = null;
let scanLoopActive = false;
let qrDetector = null;
let ipcamMode = false;
let ipcamScanBusy = false;

function showView(name) {
  views.forEach(v => $("view-" + v).classList.toggle("hidden", v !== name));
  document.querySelectorAll(".nav-item").forEach(btn => btn.classList.toggle("active", btn.dataset.view === name));
  const titles = {overview:"Verification overview", verify:"Verify a medicine", registry:"Medicine registry", "qr-tools":"QR code tools"};
  $("page-title").textContent = titles[name] || "TrueDose AI";
  if (name === "registry") loadRegistry();
}

document.querySelectorAll(".nav-item").forEach(btn => btn.addEventListener("click", () => showView(btn.dataset.view)));
$("hero-verify").addEventListener("click", () => { showView("verify"); $("medicine-code-secondary").focus(); });
$("refresh-btn").addEventListener("click", refreshDashboard);
$("registry-refresh").addEventListener("click", loadRegistry);
$("verify-form").addEventListener("submit", e => { e.preventDefault(); verifyCode($("medicine-code").value, "result-box"); });
$("verify-form-secondary").addEventListener("submit", e => { e.preventDefault(); verifyCode($("medicine-code-secondary").value, "result-box-secondary"); });
$("generate-qr-btn").addEventListener("click", generateQr);
$("verify-selected-qr").addEventListener("click", () => verifyCode($("qr-code-select").value, "qr-scan-result"));
$("start-scan-btn").addEventListener("click", startCameraScan);
$("stop-scan-btn").addEventListener("click", stopCameraScan);
$("connect-ipcam-btn").addEventListener("click", connectIpWebcam);
window.addEventListener("beforeunload", stopCameraScan);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function statusClass(result) {
  if (result === "REGISTERED RECORD") return "pill-good";
  if (result === "EXPIRED BATCH" || result === "REVIEW REPEATED SCANS") return "pill-warn";
  if (["SUSPICIOUS", "UNKNOWN CODE", "RECALLED PRODUCT", "REGISTRY SUSPENDED"].includes(result)) return "pill-bad";
  return "pill-neutral";
}
function renderResult(data, targetId) {
  const m = data.medicine || {};
  const target = $(targetId);
  target.classList.remove("hidden");
  target.innerHTML = `
    <div class="result-top"><div><div class="result-title">${escapeHtml(data.result)}</div><span class="status-pill ${statusClass(data.result)}">${escapeHtml(data.result)}</span></div>
    <div style="text-align:right"><div class="risk-number">${Number(data.risk_score)}<span style="font-size:11px;color:#8a999d">/100</span></div><div class="risk-label">RISK SCORE</div></div></div>
    <p class="result-reason">${escapeHtml(data.reason)}</p>
    <div class="risk-explainer"><div class="risk-explainer-head"><b>Explainable risk signals</b><span class="risk-level risk-${String(data.risk_level || "LOW").toLowerCase()}">${escapeHtml(data.risk_level || "LOW")} RISK</span></div>
    ${(data.signals || []).map(signal => `<div class="risk-signal"><span class="signal-dot signal-${escapeHtml(signal.level)}"></span><div><b>${escapeHtml(signal.title)}</b><small>${escapeHtml(signal.detail)}</small></div></div>`).join("")}</div>
    ${m.name ? `<div class="result-fields"><div><span>Medicine</span><b>${escapeHtml(m.name)}</b></div><div><span>Manufacturer</span><b>${escapeHtml(m.manufacturer)}</b></div><div><span>Batch</span><b>${escapeHtml(m.batch)}</b></div><div><span>Recorded expiry</span><b>${escapeHtml(m.expiry)}</b></div></div>` : ""}
    <div class="result-disclaimer">${escapeHtml(data.disclaimer)}</div>`;
}
async function verifyCode(code, targetId) {
  const clean = String(code || "").trim();
  if (!clean) { toast("Enter a code first."); return; }
  const target = $(targetId);
  target.classList.remove("hidden");
  target.innerHTML = '<div class="empty-state">Checking code against the local registry…</div>';
  try {
    const response = await fetch("/api/verify", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({code:clean})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Verification failed.");
    renderResult(data, targetId);
    $("medicine-code").value = clean.toUpperCase();
    $("medicine-code-secondary").value = clean.toUpperCase();
    await refreshDashboard();
    toast("Verification result updated.");
  } catch (err) {
    target.innerHTML = `<p class="result-reason">${escapeHtml(err.message)} Check that the Flask server is running.</p>`;
    toast("Could not complete verification.");
  }
}
async function refreshDashboard() {
  try {
    const response = await fetch("/api/stats", {cache:"no-store"});
    if (!response.ok) throw new Error("Stats unavailable");
    const data = await response.json();
    $("metric-medicines").textContent = data.medicine_records;
    $("metric-scans").textContent = data.total_scans;
    $("metric-flagged").textContent = data.flagged_scans;
    const recent = $("recent-scans");
    if (!data.recent_scans.length) recent.innerHTML = '<div class="empty-state">No verification activity yet. Enter a code or scan a QR label to begin.</div>';
    else recent.innerHTML = data.recent_scans.map(s => `<div class="scan-row"><div class="scan-symbol">⌕</div><div class="scan-info"><b>${escapeHtml(s.code)}</b><small>${escapeHtml(s.scanned_at)} · Risk ${Number(s.risk_score)}/100</small></div><span class="status-pill ${statusClass(s.result)}">${escapeHtml(s.result)}</span></div>`).join("");
    setApiStatus(true);
  } catch (err) { setApiStatus(false); }
}
function setApiStatus(connected) {
  const badge = document.querySelector(".connection");
  badge.innerHTML = connected ? '<i></i> API connected' : '<i style="background:#d65e5e"></i> API disconnected';
  badge.title = connected ? "API is responding" : "Could not reach the local API";
}
async function checkApiHealth() {
  try { const response = await fetch("/api/health", {cache:"no-store"}); if (!response.ok) throw new Error(); await response.json(); setApiStatus(true); }
  catch (err) { setApiStatus(false); }
}
async function loadRegistry() {
  try {
    const response = await fetch("/api/medicines");
    if (!response.ok) throw new Error("Registry unavailable");
    const rows = await response.json();
    const statusLabel = status => status === "suspected_clone" ? "Suspected clone" : status.replaceAll("_", " ").replace(/^\w/, c => c.toUpperCase());
    $("registry-body").innerHTML = rows.length ? rows.map(m => `<tr><td><b>${escapeHtml(m.code)}</b></td><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.manufacturer)}</td><td>${escapeHtml(m.batch)}</td><td>${escapeHtml(m.expiry)}</td><td><span class="status-pill ${["suspected_clone", "recalled", "suspended"].includes(m.status) ? "pill-warn" : "pill-good"}">${escapeHtml(statusLabel(m.status))}</span></td></tr>`).join("") : '<tr><td colspan="6">No medicine records are configured. Add records through Registry Admin.</td></tr>';
    const select = $("qr-code-select");
    select.innerHTML = rows.length ? rows.map(m => `<option value="${escapeHtml(m.code)}">${escapeHtml(m.code)} · ${escapeHtml(m.name)}</option>`).join("") : '<option value="">No registry records available</option>';
    $("generate-qr-btn").disabled = rows.length === 0;
    $("verify-selected-qr").disabled = rows.length === 0;
  } catch (err) {
    $("registry-body").innerHTML = '<tr><td colspan="6">Could not load registry. Check the server.</td></tr>';
    $("qr-code-select").innerHTML = '<option value="">Registry unavailable</option>';
  }
}
async function generateQr() {
  const code = $("qr-code-select").value;
  if (!code) { toast("No medicine records are available. Add a record in Registry Admin first."); return; }
  const img = $("qr-preview");
  const placeholder = $("qr-placeholder");
  const download = $("download-qr");
  try {
    const response = await fetch(`/api/qr?code=${encodeURIComponent(code)}`);
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || "QR generation failed"); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
    img.dataset.objectUrl = url; img.src = url; img.classList.remove("hidden"); placeholder.classList.add("hidden");
    download.href = url; download.download = `truedose-${code.toLowerCase()}.png`; download.classList.remove("hidden");
    toast("QR code generated.");
  } catch (err) { placeholder.textContent = err.message; placeholder.classList.remove("hidden"); img.classList.add("hidden"); download.classList.add("hidden"); }
}
async function startCameraScan() {
  stopCameraScan();
  ipcamMode = false;
  const status = $("scanner-status");
  if (!navigator.mediaDevices?.getUserMedia) { status.textContent = "Camera access is unavailable in this browser. Use the phone IP Webcam option below or enter the code manually."; return; }
  if (!("BarcodeDetector" in window)) { status.textContent = "This browser does not support built-in QR detection. Use the phone IP Webcam option below, or try Chrome/Edge."; return; }
  try {
    qrDetector = new BarcodeDetector({formats:["qr_code"]});
    cameraStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"}}, audio:false});
    const video = $("qr-video"); video.srcObject = cameraStream; video.classList.remove("hidden"); $("ipcam-image").classList.add("hidden"); $("scanner-placeholder").classList.add("hidden");
    await video.play(); scanLoopActive = true; $("start-scan-btn").disabled = true; $("connect-ipcam-btn").disabled = true; $("stop-scan-btn").disabled = false;
    status.textContent = "This device's camera is running. Point it at a generated TrueDose QR code.";
    scanFrame();
  } catch (err) { status.textContent = `Could not start camera: ${err.message}. Check browser camera permissions.`; stopCameraScan(); }
}
async function connectIpWebcam() {
  stopCameraScan();
  const status = $("scanner-status");
  const entered = $("ipcam-url").value.trim();
  let parsed;
  try { parsed = new URL(entered); } catch { status.textContent = "Enter the IP Webcam stream URL shown in the Android app, e.g. http://192.168.1.25:8080/video"; return; }
  if (parsed.protocol !== "http:" || !parsed.hostname || !/^\d{1,3}(\.\d{1,3}){3}$/.test(parsed.hostname)) {
    status.textContent = "Enter the phone's local IPv4 URL, e.g. http://192.168.1.25:8080/video"; return;
  }
  if (!parsed.pathname || parsed.pathname === "/") parsed.pathname = "/video";
  const proxyUrl = `/api/ipcam/stream?url=${encodeURIComponent(parsed.toString())}`;
  const img = $("ipcam-image");
  status.textContent = "Connecting to phone webcam… make sure IP Webcam is streaming and both devices use the same Wi-Fi.";
  img.onload = () => {
    ipcamMode = true; scanLoopActive = true; img.classList.remove("hidden"); $("qr-video").classList.add("hidden"); $("scanner-placeholder").classList.add("hidden");
    $("start-scan-btn").disabled = true; $("connect-ipcam-btn").disabled = true; $("stop-scan-btn").disabled = false;
    status.textContent = "Phone webcam connected. Hold a TrueDose QR code in front of your phone camera.";
    scanIpWebcamFrame();
  };
  img.onerror = () => { status.textContent = "Could not load the phone stream. Check the IP address, /video path, IP Webcam app, and Wi-Fi network."; stopCameraScan(); };
  img.src = proxyUrl;
}
function scanIpWebcamFrame() {
  if (!scanLoopActive || !ipcamMode || ipcamScanBusy) { if (scanLoopActive && ipcamMode) requestAnimationFrame(scanIpWebcamFrame); return; }
  const img = $("ipcam-image"), canvas = $("ipcam-canvas");
  if (img.complete && img.naturalWidth > 0 && typeof window.jsQR === "function") {
    ipcamScanBusy = true;
    try {
      const width = Math.min(img.naturalWidth, 960);
      const height = Math.max(1, Math.round(img.naturalHeight * width / img.naturalWidth));
      canvas.width = width; canvas.height = height;
      const ctx = canvas.getContext("2d", {willReadFrequently:true});
      ctx.drawImage(img, 0, 0, width, height);
      const frame = ctx.getImageData(0, 0, width, height);
      const code = window.jsQR(frame.data, frame.width, frame.height, {inversionAttempts:"attemptBoth"});
      if (code?.data) {
        const raw = code.data.trim(); stopCameraScan();
        $("scanner-status").textContent = `QR read: ${raw}. Checking registry…`;
        verifyCode(raw, "qr-scan-result"); return;
      }
    } catch (err) { /* Wait for the next frame. */ }
    finally { ipcamScanBusy = false; }
  }
  if (scanLoopActive && ipcamMode) setTimeout(scanIpWebcamFrame, 150);
}
async function scanFrame() {
  if (!scanLoopActive || ipcamMode) return;
  const video = $("qr-video");
  if (video.readyState >= 2) {
    try {
      const codes = await qrDetector.detect(video);
      if (codes.length && codes[0].rawValue) {
        const raw = codes[0].rawValue.trim(); stopCameraScan();
        $("scanner-status").textContent = `QR read: ${raw}. Checking registry…`;
        await verifyCode(raw, "qr-scan-result"); return;
      }
    } catch (err) { /* Continue scanning while the camera frame is not ready. */ }
  }
  if (scanLoopActive && !ipcamMode) requestAnimationFrame(scanFrame);
}
function stopCameraScan() {
  scanLoopActive = false; ipcamMode = false; ipcamScanBusy = false;
  if (cameraStream) { cameraStream.getTracks().forEach(track => track.stop()); cameraStream = null; }
  const video = $("qr-video");
  if (video) { video.pause(); video.srcObject = null; video.classList.add("hidden"); }
  const img = $("ipcam-image"); if (img) { img.onload = null; img.onerror = null; img.removeAttribute("src"); img.classList.add("hidden"); }
  const placeholder = $("scanner-placeholder"); if (placeholder) placeholder.classList.remove("hidden");
  if ($("start-scan-btn")) $("start-scan-btn").disabled = false;
  if ($("connect-ipcam-btn")) $("connect-ipcam-btn").disabled = false;
  if ($("stop-scan-btn")) $("stop-scan-btn").disabled = true;
}
let toastTimeout;
function toast(message) {
  const t = $("toast"); t.textContent = message; t.classList.remove("hidden");
  clearTimeout(toastTimeout); toastTimeout = setTimeout(() => t.classList.add("hidden"), 2400);
}
checkApiHealth();
refreshDashboard();
loadRegistry();
