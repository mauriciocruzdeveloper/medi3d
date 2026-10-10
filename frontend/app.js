
const $ = (id) => document.getElementById(id);
const state = { uid: null, info: null, z: 0, mask: null };

const status = (msg, err = false) => { $('status').textContent = msg; $('status').className = err ? 'err' : ''; };
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ---------------- series
$('scan-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  status('Escaneando…');
  try {
    const list = await api('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: $('path').value }) });
    const ul = $('series-list'); ul.innerHTML = '';
    list.forEach((s) => {
      const li = document.createElement('li');
      li.innerHTML = `<div>${s.descripcion || 'Sin descripción'}</div>
        <span class="tag">${s.secuencia}</span><span class="tag">${s.plano}</span>
        ${s.contraste ? `<span class="tag ${s.contraste}">${s.contraste === 'post' ? 'con contraste' : 'sin contraste'}</span>` : ''}
        ${s.fatsat ? '<span class="tag">fat-sat</span>' : ''}
        <div class="sub">${s.cortes} cortes · ${s.grosor} mm</div>`;
      li.onclick = () => { document.querySelectorAll('#series-list li').forEach((x) => x.classList.remove('active')); li.classList.add('active'); openSeries(s); };
      ul.appendChild(li);
    });
    status(`${list.length} series encontradas.`);
  } catch (err) { status(err.message, true); }
});

async function openSeries(s) {
  state.uid = s.uid; state.mask = null;
  state.info = await api(`/api/series/${s.uid}/info`);
  $('series-title').textContent = s.descripcion;
  $('slider').max = state.info.cortes - 1;
  state.z = Math.floor(state.info.cortes / 2); $('slider').value = state.z;
  $('marker').hidden = true; showSlice();
}
function showSlice() {
  $('slice').src = `/api/series/${state.uid}/slice/${state.z}.png` + (state.mask && document.body.classList.contains('mode-3d') ? `?mask=${state.mask}&t=${Date.now()}` : '');
  $('slice-label').textContent = `${state.z + 1}/${state.info.cortes}`;
}
$('slider').addEventListener('input', (e) => { state.z = +e.target.value; showSlice(); });
$('slice-wrap').addEventListener('wheel', (e) => {
  if (!state.info) return; e.preventDefault();
  state.z = Math.max(0, Math.min(state.info.cortes - 1, state.z + Math.sign(e.deltaY))); $('slider').value = state.z; showSlice();
}, { passive: false });
$('mult').addEventListener('input', (e) => ($('mult-out').textContent = e.target.value));

// ---------------- clic → segmentación
$('slice').addEventListener('click', async (e) => {
  if (!state.uid || !document.body.classList.contains('mode-3d')) return;
  const img = $('slice'), r = img.getBoundingClientRect();
  const x = Math.floor(((e.clientX - r.left) / r.width) * state.info.ancho);
  const y = Math.floor(((e.clientY - r.top) / r.height) * state.info.alto);
  const m = $('marker'), wr = $('slice-wrap').getBoundingClientRect();
  m.style.left = e.clientX - wr.left + 'px'; m.style.top = e.clientY - wr.top + 'px'; m.hidden = false;
  status('Segmentando…');
  try {
    const res = await api('/api/segment', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uid: state.uid, x, y, z: state.z, multiplicador: +$('mult').value, radio_max_mm: +$('radius').value }) });
    state.mask = res.mask; showSlice();
    $('result').textContent = `Volumen: ${res.volumen_ml} mL (${res.voxeles} vóxeles)`;
    status('Generando modelo 3D…');
    await loadModel(res.mask);
    status('Listo.');
  } catch (err) { status(err.message, true); }
});

// ---------------- interruptor de modelado 3D
$('toggle3d').addEventListener('change', async (e) => {
  const on = e.target.checked;
  if (on) {
    const cap = await api('/api/capabilities').catch(() => ({ modelado3d: false, motivo: 'Sin conexión con el servidor' }));
    if (!cap.modelado3d) {
      e.target.checked = false;
      $('cap-warning').textContent = `El modelado 3D no está disponible en esta máquina. ${cap.motivo || ''}`;
      $('cap-warning').hidden = false; return;
    }
    try { await init3D(); } catch (err) {
      e.target.checked = false;
      $('cap-warning').textContent = 'No se pudo cargar el visor 3D (¿sin internet o sin WebGL?): ' + err.message;
      $('cap-warning').hidden = false; return;
    }
  }
  $('cap-warning').hidden = true;
  document.body.classList.toggle('mode-viewer', !on);
  document.body.classList.toggle('mode-3d', on);
  if (state.uid) showSlice();
});

// ---------------- 3D (carga perezosa)
let three = null, model = null;
async function init3D() {
  if (three) { three.resize(); return; }
  const THREE = await import('three');
  const { OrbitControls } = await import('three/addons/controls/OrbitControls.js');
  const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
  const host = $('three');
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
  camera.position.set(0, 0, 200);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 1.2));
  const dl = new THREE.DirectionalLight(0xffffff, 2); dl.position.set(1, 1, 2); scene.add(dl);
  const resize = () => { const w = host.clientWidth, h = host.clientHeight; if (!w || !h) return; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe(host);
  (function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
  three = { THREE, GLTFLoader, scene, camera, controls, resize };
}

async function loadModel(mid) {
  ['stl', 'obj', 'glb'].forEach((f) => { const a = $('dl-' + f); a.href = `/api/mesh/${mid}.${f}`; a.download = `modelo.${f}`; a.classList.remove('disabled'); });
  const { THREE, GLTFLoader, scene, camera, controls } = three;
  const gltf = await new GLTFLoader().loadAsync(`/api/mesh/${mid}.glb`);
  if (model) scene.remove(model);
  model = gltf.scene;
  model.traverse((o) => { if (o.isMesh) o.material = new THREE.MeshStandardMaterial({ color: 0xff6b6b, roughness: 0.45, metalness: 0.1 }); });
  const box = new THREE.Box3().setFromObject(model), c = box.getCenter(new THREE.Vector3()), sz = box.getSize(new THREE.Vector3()).length();
  model.position.sub(c); scene.add(model);
  camera.position.set(0, 0, sz * 1.8); controls.target.set(0, 0, 0);
}
