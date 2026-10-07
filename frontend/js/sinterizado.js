// sinterizado.js — Pestaña "Sinterizado" (solo index.html): la planilla de campo
// PCS-PG851.06-A3 hecha pantalla, mas el procedimiento PG 851.06 (Rev. 05) y su
// indicador. Una planilla = un crisol que se desarma y se arma de nuevo:
//
//   Hoja 1 · Desarme  -> estado del crisol viejo (aspecto, espesor, filtraciones,
//                        manta, polvo) + marcas de color sobre el dibujo
//   Hoja 2 · Armado   -> control/colocacion de formaleta, piso, paredes
//   Registro          -> hora / potencia / temperatura del sinterizado
//   Avance            -> los 7 pasos del procedimiento, cada uno firmado (quien
//                        y cuando) por el servidor la primera vez que se tilda
//
// Toda la validacion y las reglas (congelada al finalizar, choque de ediciones,
// firma de pasos, reabrir solo admin) estan en main.py (/api/sinterizados); esto
// solo pinta y arma el cuerpo del PUT.
//
// Guardado automático: cada cambio programa un guardado (ver _snProgramarGuardado
// / _snAutoguardar, más abajo) -- así apagar el equipo a mitad de la carga no
// pierde lo que ya se tipeó. El botón "Guardar ahora" solo fuerza que el guardado
// pendiente pase ya, no es un mecanismo aparte.
//
// Dependencias que define index.html ANTES de usar esto: $id, setMain, api, _esc,
// fmtFechaCorta, _AUTH. Los estilos se inyectan solos la primera vez (_snEstilos).

const _SN_META_C = 1580;   // °C a alcanzar (PG 851.06, paso Sinterizado)
const _SN_META_MIN = 60;   // minutos que hay que sostenerla

// Colores de las marcas: los que pide la planilla en cada campo de la Hoja 1.
const _SN_MARCAS = {
  espesor:    { color: '#e53935', corto: 'Rojo',  texto: 'espesor mínimo' },
  filtracion: { color: '#43a047', corto: 'Verde', texto: 'filtraciones' },
  manta:      { color: '#1e88e5', corto: 'Azul',  texto: 'manta oscura / quemada' },
};

// Campos editables tal cual los valida el backend (_SINTER_CAMPOS).
const _SN_CAMPOS = [
  'desarme_fecha', 'coladas', 'diametro_final', 'altura', 'altura_conicidad', 'aspecto', 'espesor_min', 'filtraciones', 'manta_oscura',
  'foto', 'foto_archivo', 'polvo', 'polvo_mm', 'armado_fecha', 'antena_orificio_ok', 'formaleta_medidas', 'formaleta_medidas_ok',
  'formaleta_rebabado', 'formaleta_rebabado_ok', 'centrado_reviso', 'centrado_vb_fusion', 'centrado_vb_produccion',
  'paredes_vb_produccion', 'observaciones',
];

// Los 7 pasos del procedimiento. Las claves son las de _SINTER_PASOS en main.py.
const _SN_PASOS = [
  { clave: 'desarme', titulo: 'Desarme del horno',
    detalles: ['Desarmar el crisol existente.', 'Completar la Hoja 1 (Desarme) de la planilla: aspecto general, espesor mínimo, filtraciones, estado de la manta y polvo refractario.'] },
  { clave: 'revoque', titulo: 'Revoque de la bobina',
    detalles: ['Parcial o total, según el estado de la bobina.', 'Si es total, colocar aros para dar la conicidad.', 'Aplicar el enduido.', 'Dejar secar.'] },
  { clave: 'manta', titulo: 'Colocación de la manta cerámica',
    detalles: ['3 trozos de manta cerámica de 1,3 m cada uno.', 'Solapados entre sí de 30 a 40 cm.'] },
  { clave: 'antena_piso', titulo: 'Colocación de antena y construcción del piso',
    detalles: ['Antena preparada según el Anexo, con aislación eléctrica de la base.', 'Picado del piso: primero 2 bolsas (≈ 50 kg), luego 1,5 bolsas, hasta completar 5 bolsas en total.', 'Compactar con tridente y vibrador.'],
    nota: 'El piso tiene que quedar de ≈ 18 cm. La cantidad de bolsas depende del diámetro inferior del horno sin refractario.' },
  { clave: 'formaleta', titulo: 'Posicionamiento de la formaleta',
    detalles: ['Formaleta centrada y contrapesada, sin óxidos.', 'Verificar la soldadura de la costura.', 'Completar en la Hoja 2 el control y la colocación de la formaleta.'],
    nota: 'Modificar la altura de la formaleta según el espesor del piso.' },
  { clave: 'paredes', titulo: 'Construcción de las paredes',
    detalles: ['Una bolsa (25 kg) por vuelta.', '4 vueltas con tridente y 3 con vibrador.', 'Pasar el tridente entre capas hasta llegar al nivel.', 'A la altura de la piquera, terminar con refractario de fragua a baja temperatura.'] },
  { clave: 'sinterizado', titulo: 'Sinterizado',
    detalles: ['Cargar hasta ¼ del crisol y colocar el canasto guiador de llama.', 'Completar la carga y encender el quemador.', 'Calentar según la curva del fabricante.', 'Llegar a ' + _SN_META_C + ' °C y mantener ' + _SN_META_MIN + ' minutos (registrarlo abajo).', 'El quemador se usa solo por motivos energéticos; respetar los tiempos de mantenimiento.'] },
];

const _SN = { tab: 'lista', lista: [], actual: null, sucio: false, pluma: 'espesor', errorLista: '',
  perfilAngulo: 0,  // slider del perfil: ángulo (0-359) que se está mirando ahora
  alturaIdx: 0,     // slider del óvalo: índice dentro de marcas.alturas que se está mostrando ahora
  nuevaAlturaAbierta: false,
  fotoMmActual: null,       // a qué medición (mm) corresponde el próximo archivo que se elija en #sn-foto-input
  productosRefractarios: null,  // catálogo de Proveedores > Material Refractario (se carga una vez, no por planilla)
  curvaRef: null,  // curva de referencia del paso Sinterizado: UNA sola, compartida (se carga una vez, no por planilla)
  procPg85106: null };  // pestaña Procedimiento: contenido real del módulo general de Procedimientos (ver procedimientos.js), se carga una vez

// ── Utilidades ───────────────────────────────────────────────────────────────
function _snV(v) { return v == null ? '' : v; }
function _snNum(v) { return v == null || v === '' ? '—' : Number(v).toLocaleString('es-AR'); }
function _snFechaHora(iso) {
  if (!iso) return '';
  const s = String(iso);
  return s.slice(8, 10) + '/' + s.slice(5, 7) + ' ' + s.slice(11, 16);
}
function _snHoraAhora() {
  const d = new Date();
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
// Fecha con la que cuenta la planilla en el indicador: cuando se armo el crisol
// nuevo; si todavia no se cargo, el desarme; y si tampoco, cuando se abrio.
function _snFechaPrep(r) { return r.armado_fecha || r.desarme_fecha || String(r.creado_en || '').slice(0, 10); }
function _snCongelada() { return !_SN.actual || _SN.actual.estado !== 'en_curso'; }
function _snDis() { return _snCongelada() ? 'disabled' : ''; }
function _snMsgError(e) {
  let m = String((e && e.message) || e);
  try { const j = JSON.parse(m); if (j && j.detail) m = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) { /* no era JSON */ }
  return m;
}
function _snEstadoTag(e) {
  return e === 'finalizado' ? '<span class="tag tag-g">Finalizado</span>' : '<span class="tag tag-y">En curso</span>';
}
function _snRefrescar(id, html) { const el = $id(id); if (el) el.innerHTML = html; }

function _snEstilos() {
  if ($id('sn-style')) return;
  const st = document.createElement('style');
  st.id = 'sn-style';
  st.textContent = `
.sn-bar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 8px 12px; margin-bottom: 8px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }
.sn-titulo { font-size: 15px; font-weight: 600; }
.sn-acciones { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.sn-sucio { font-size: 12px; font-weight: 600; }
.sn-meta { font-size: 11px; color: var(--muted); margin-bottom: 12px; }
.sn-card { margin-bottom: 14px; }
.sn-sub { font-size: 12px; font-weight: 600; color: var(--accent); margin: 14px 0 8px; text-transform: uppercase; letter-spacing: .4px; }
.sn-hint { font-size: 11px; margin-top: 3px; }
.sn-progreso { float: right; text-transform: none; letter-spacing: 0; }
.sn-steps { display: flex; flex-direction: column; gap: 8px; }
.sn-step { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: var(--surface2); }
.sn-step.hecho { border-color: var(--green); }
.sn-step-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sn-step-num { flex: none; width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; background: var(--accent-dim); color: var(--accent); }
.sn-step.hecho .sn-step-num { background: var(--green); color: #fff; }
.sn-step-tit { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; cursor: pointer; }
.sn-step-firma { margin-left: auto; font-size: 11px; color: var(--muted); }
.sn-step ul { margin: 6px 0 0 34px; padding-left: 16px; font-size: 12px; }
.sn-step li { margin-bottom: 2px; }
.sn-nota { margin: 8px 0 0 34px; padding: 6px 10px; border-left: 3px solid var(--yellow); background: rgba(255,167,38,.09); font-size: 12px; }
.sn-campos { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--border); }
.sn-firma { display: flex; gap: 4px; }
.sn-firma .inp { flex: 1; min-width: 0; }
.sn-lect { border-collapse: collapse; width: 100%; }
.sn-lect th, .sn-lect td { padding: 4px 6px; }
.sn-idx { color: var(--muted); font-size: 11px; text-align: right; }
.sn-quitar { cursor: pointer; opacity: .6; font-weight: 700; font-size: 16px; padding: 0 4px; }
.sn-quitar:hover { opacity: 1; color: var(--red); }
.sn-vacio { color: var(--muted); font-size: 12px; padding: 10px 0; }
.sn-graf { width: 100%; max-width: 1100px; height: auto; display: block; }
.sn-graf text { fill: var(--muted); font-size: 10px; }
.sn-graf .sn-g { stroke: var(--border); stroke-width: 1; }
.sn-graf .sn-curva { fill: none; stroke: var(--accent); stroke-width: 2; }
.sn-graf .sn-curva-ref { fill: none; stroke: #ab47bc; stroke-width: 2; stroke-dasharray: 5 3; }
.sn-graf .sn-banda-ref { fill: #ab47bc; fill-opacity: .28; stroke: #ab47bc; stroke-opacity: .5; stroke-width: .5; }
.sn-graf .sn-lmeta { stroke: var(--red); stroke-width: 1; stroke-dasharray: 5 4; }
.sn-graf-leyenda { display: flex; gap: 14px; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
.sn-graf .sn-metatxt { fill: var(--red); }
.sn-plumas { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.sn-pluma { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border: 2px solid var(--border); border-radius: 16px; background: var(--surface2); color: var(--text); cursor: pointer; font-size: 12px; }
.sn-pluma.sel { border-color: var(--accent); background: var(--accent-dim); font-weight: 600; }
.sn-pluma i { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
.sn-explorar { display: flex; gap: 22px; flex-wrap: wrap; align-items: flex-start; }
.sn-explorar-col { flex: 1; min-width: 260px; }
.sn-hint2 { font-size: 11px; color: var(--muted); margin-bottom: 8px; }
.sn-sw { display: inline-block; width: 10px; height: 10px; margin: 0 4px 0 6px; vertical-align: -1px; border-radius: 2px; }
.sn-svg-perfil { width: 260px; max-width: 100%; height: auto; aspect-ratio: 100 / 110; display: block; }
.sn-angulo-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; }
.sn-angulo { flex: 1; min-width: 100px; accent-color: var(--accent); }
.sn-nueva-altura { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.sn-vista { position: relative; width: 300px; max-width: 100%; aspect-ratio: 150 / 130; margin: 0 auto; }
.sn-vista svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.sn-medpar { position: absolute; transform: translate(-50%, -50%); width: 17%; display: flex; flex-direction: column; gap: 2px; }
.sn-medpar-activa .sn-med, .sn-medpar-activa .sn-med-v { box-shadow: 0 0 0 1px var(--accent); }
.sn-vista .sn-med, .sn-vista .sn-med-v { box-sizing: border-box; width: 100%; text-align: center; font-size: 11px; }
.sn-vista .sn-med { padding: 2px 1px; border-left: 3px solid #ffa726; }
.sn-vista .sn-med-p { border-left-color: #8d6e63; }
.sn-vista .sn-med.sn-min { border-color: var(--red); box-shadow: 0 0 0 1px var(--red); font-weight: 700; }
.sn-vista .sn-med.sn-max { border-color: #8d6e63; box-shadow: 0 0 0 1px #8d6e63; font-weight: 700; }
.sn-vista .sn-med-v { border-bottom: 1px solid var(--muted); min-height: 15px; }
.sn-vista .sn-med-v::before { content: attr(data-l); font-size: 8px; color: var(--muted); margin-right: 3px; }
.sn-foto-centro { position: absolute; transform: translate(-50%, -50%); display: flex; align-items: center; justify-content: center; }
.sn-foto-btn { font-size: 10px; padding: 4px 6px; white-space: nowrap; }
.sn-foto-wrap { position: relative; width: 46px; height: 46px; }
.sn-foto-img { width: 100%; height: 100%; object-fit: cover; border-radius: 6px; border: 1px solid var(--border); cursor: pointer; background: var(--surface2); display: block; }
.sn-foto-acciones { position: absolute; top: -8px; right: -10px; display: flex; flex-direction: column; gap: 2px; }
.sn-foto-acciones .page-btn { padding: 1px 4px; font-size: 10px; line-height: 1.3; }
.sn-kpis { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.sn-kpi { flex: 1; min-width: 150px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface2); }
.sn-kpi b { display: block; font-size: 22px; margin-bottom: 2px; }
.sn-kpi span { font-size: 11px; color: var(--muted); }
`;
  document.head.appendChild(st);
}

// ── Entrada de la pestaña ────────────────────────────────────────────────────
let _snBeforeUnloadOk = false;

async function showSinterizado() {
  _snEstilos();
  if (!_snBeforeUnloadOk) {
    _snBeforeUnloadOk = true;
    window.addEventListener('beforeunload', e => { if (_SN.sucio) { e.preventDefault(); e.returnValue = ''; } });
  }
  setMain(`
    <div class="sec-title">&#127777; Sinterizado de horno de inducci&oacute;n</div>
    <div class="tab-bar" id="sn-tabbar">
      <button class="tab-btn" data-tab="lista" onclick="_snTab('lista')">Sinterizados</button>
      <button class="tab-btn" data-tab="procedimiento" onclick="_snTab('procedimiento')">Procedimiento PG 851.06</button>
      <button class="tab-btn" data-tab="curva" onclick="_snTab('curva')">Curva de referencia</button>
      <button class="tab-btn" data-tab="indicador" onclick="_snTab('indicador')">Indicador</button>
    </div>
    <div id="sn-content"><div class="loading">Cargando...</div></div>`);
  await Promise.all([
    _snCargarLista(),
    _SN.productosRefractarios ? Promise.resolve() : _snCargarProductosRefractarios(),
    _SN.curvaRef ? Promise.resolve() : _snCargarCurvaRef(),
    _SN.procPg85106 ? Promise.resolve() : _snCargarProcPg85106(),
  ]);
  _snTab(_SN.tab);
}

// Lo llama go() antes de irse de la pestaña. Se guarda solo, así que no hay
// nada que confirmar -- solo forzar que el guardado pendiente salga ya.
function _sinterPuedeSalir() {
  if (_SN.sucio) _snProgramarGuardado(true);
  _SN.sucio = false;
  _SN.actual = null;
  return true;
}

async function _snCargarLista() {
  try {
    _SN.lista = await api('/api/sinterizados');
    _SN.errorLista = '';
  } catch (e) {
    _SN.lista = [];
    _SN.errorLista = _snMsgError(e);
  }
}

function _snTab(tab) {
  _SN.tab = tab;
  document.querySelectorAll('#sn-tabbar .tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  const el = $id('sn-content');
  if (!el) return;
  el.innerHTML = tab === 'procedimiento' ? _snRenderProcedimiento()
    : tab === 'indicador' ? _snRenderIndicador()
    : tab === 'curva' ? _snRenderCurvaTab()
    : _SN.actual ? _snRenderDetalle() : _snRenderLista();
  if (tab === 'lista' && _SN.actual) _snPintarPartes();
}

// ── Lista ────────────────────────────────────────────────────────────────────
function _snRenderLista() {
  if (_SN.errorLista) return `<div class="card"><div class="sn-vacio" style="color:var(--red)">${_esc(_SN.errorLista)}</div></div>`;
  const filas = _SN.lista.map(r => `
    <tr class="tr-link" onclick="_snAbrir(${r.id})">
      <td><b>N&deg; ${r.id}</b></td>
      <td>${_esc(fmtFechaCorta(_snFechaPrep(r)))}</td>
      <td>${_snEstadoTag(r.estado)}</td>
      <td style="text-align:right">${_snNum(r.coladas)}</td>
      <td style="text-align:right">${_snNum(r.diametro_final)}</td>
      <td style="text-align:right">${_snNum(r.espesor_min)}</td>
      <td>${r.pasos_hechos}/${r.pasos_total}</td>
      <td style="text-align:right">${r.temp_max == null ? '—' : _snNum(r.temp_max) + ' °C'}</td>
      <td style="color:var(--muted);font-size:12px">${_esc(r.modificado_por_nombre || '')} &middot; ${_esc(_snFechaHora(r.modificado_en))}</td>
    </tr>`).join('');
  return `
    <div class="row-gap">
      <button class="btn-primary" onclick="_snNuevo()">&#10133; Nuevo sinterizado</button>
      <span style="font-size:12px;color:var(--muted)">Uno por cada crisol que se desarma y se arma de nuevo (planilla PCS-PG851.06-A3).</span>
    </div>
    <div class="card"><div class="tbl-wrap">
      ${_SN.lista.length ? `<table>
        <thead><tr><th>N&deg;</th><th>Fecha</th><th>Estado</th><th style="text-align:right">Coladas del crisol</th><th style="text-align:right">&Oslash; final (mm)</th><th style="text-align:right">Espesor m&iacute;n. (mm)</th><th>Pasos</th><th style="text-align:right">Temp. m&aacute;x.</th><th>&Uacute;ltima modificaci&oacute;n</th></tr></thead>
        <tbody>${filas}</tbody></table>` : '<div class="sn-vacio">Todavía no se cargó ningún sinterizado. Tocá <b>Nuevo sinterizado</b> para empezar la planilla.</div>'}
    </div></div>`;
}

async function _snNuevo() {
  try {
    const d = await api('/api/sinterizados', { method: 'POST' });
    _snEntrar(d);
    await _snCargarLista();
  } catch (e) { alert('No se pudo crear: ' + _snMsgError(e)); }
}

async function _snAbrir(id) {
  try { _snEntrar(await api('/api/sinterizados/' + id)); }
  catch (e) { alert('No se pudo abrir: ' + _snMsgError(e)); }
}

function _snEntrar(d) {
  _SN.actual = d;
  _SN.sucio = false;
  _SN.perfilAngulo = 0;
  _SN.alturaIdx = 0;
  _SN.nuevaAlturaAbierta = false;
  _snTab('lista');
  const m = $id('main'); if (m) m.scrollTop = 0;
}

// Se guarda solo (ver _snProgramarGuardado): salir de la planilla no pierde
// nada, así que no hace falta preguntar -- solo asegurarse de que el guardado
// pendiente (si había un debounce esperando) salga ya, aunque sea en segundo
// plano, sin bloquear la navegación.
async function _snVolver() {
  if (_SN.sucio) _snProgramarGuardado(true);
  _SN.actual = null;
  _SN.sucio = false;
  await _snCargarLista();
  _snTab('lista');
}

// ── Detalle: la planilla ─────────────────────────────────────────────────────
function _snIn(campo, etiqueta, o = {}) {
  const tipo = o.tipo || 'text';
  const extra = tipo === 'number' ? ' step="any" min="0" inputmode="decimal"' : '';
  return `<div class="${o.cls || 'pv-f'}"><label>${etiqueta}</label>
    <input class="inp" style="width:100%" type="${tipo}"${extra} data-f="${campo}" value="${_esc(_snV(_SN.actual[campo]))}" ${_snDis()} oninput="_snCampo(this)">${o.hint ? `<div class="sn-hint" style="color:${o.color || 'var(--muted)'}">${o.hint}</div>` : ''}</div>`;
}
function _snSiNo(campo, etiqueta, o = {}) {
  const v = _SN.actual[campo] || '';
  return `<div class="${o.cls || 'pv-f'}"><label>${etiqueta}</label>
    <select class="inp" style="width:100%" data-f="${campo}" ${_snDis()} onchange="_snCampo(this)">
      <option value=""${v === '' ? ' selected' : ''}>&mdash;</option><option value="SI"${v === 'SI' ? ' selected' : ''}>SI</option><option value="NO"${v === 'NO' ? ' selected' : ''}>NO</option>
    </select>${o.hint ? `<div class="sn-hint" style="color:${o.color || 'var(--muted)'}">${o.hint}</div>` : ''}</div>`;
}
function _snCheck(campo, etiqueta) {
  return `<label style="display:flex;align-items:center;gap:6px;font-size:12px;margin-top:22px;white-space:nowrap;color:var(--text)">
    <input type="checkbox" data-f="${campo}" ${_SN.actual[campo] ? 'checked' : ''} ${_snDis()} onchange="_snCampo(this)"> ${etiqueta}</label>`;
}
// Casilla de V°B°/firma: texto libre (puede firmar otra persona en un puesto
// compartido) con un boton para poner el nombre de quien esta logueado.
function _snFirma(campo, etiqueta, o = {}) {
  return `<div class="${o.cls || 'pv-f'}"><label>${etiqueta}</label>
    <div class="sn-firma"><input class="inp" type="text" data-f="${campo}" value="${_esc(_snV(_SN.actual[campo]))}" ${_snDis()} oninput="_snCampo(this)">
    ${_snCongelada() ? '' : `<button type="button" class="page-btn" title="Poner mi nombre" onclick="_snYo('${campo}')">Yo</button>`}</div></div>`;
}
function _snTxt(campo, etiqueta, filas) {
  return `<div class="pv-row"><div style="flex:1;min-width:260px"><label>${etiqueta}</label>
    <textarea class="inp" style="width:100%;resize:vertical" rows="${filas}" data-f="${campo}" ${_snDis()} oninput="_snCampo(this)">${_esc(_snV(_SN.actual[campo]))}</textarea></div></div>`;
}

function _snCampo(el) {
  if (!_SN.actual) return;
  _SN.actual[el.dataset.f] = el.type === 'checkbox' ? (el.checked ? 1 : 0) : el.value;
  // Tipear espera un instante por si se sigue escribiendo; un checkbox o un
  // <select> es una eleccion de una sola vez, se guarda ya.
  _snSucio(el.tagName === 'SELECT' || el.type === 'checkbox');
}
function _snYo(campo) {
  if (_snCongelada() || !_AUTH.user) return;
  _SN.actual[campo] = _AUTH.user.nombre;
  const inp = document.querySelector('#sn-content [data-f="' + campo + '"]');
  if (inp) inp.value = _AUTH.user.nombre;
  _snSucio(true);
}
// Marca que hay cambios sin guardar y programa el guardado automático
// (`inmediato`: true para una acción puntual -- click, checkbox --, false para
// dejar que tipear termine antes de mandar el PUT). Ver _snProgramarGuardado.
function _snSucio(inmediato) {
  _SN.sucio = true;
  _snEstadoGuardado('&#9679; cambios sin guardar', 'var(--yellow)');
  _snProgramarGuardado(inmediato);
}

// ── Guardado automático ─────────────────────────────────────────────────────
// Un solo temporizador compartido por toda la planilla: cada _snSucio(...) lo
// reprograma. `inmediato` lo dispara ya (un click, un checkbox); si no, espera
// _SN_AUTOGUARDADO_ESPERA por si la persona sigue tipeando en el mismo campo.
const _SN_AUTOGUARDADO_ESPERA = 1200;
let _snAutoguardadoTimer = null;
let _snAutoguardadoEnCurso = false;   // hay un PUT en vuelo ahora mismo
let _snAutoguardadoPendiente = false; // hubo un cambio nuevo mientras ese PUT viajaba

function _snProgramarGuardado(inmediato) {
  if (_snCongelada() || !_SN.actual) return;
  clearTimeout(_snAutoguardadoTimer);
  if (inmediato) _snAutoguardar();
  else _snAutoguardadoTimer = setTimeout(_snAutoguardar, _SN_AUTOGUARDADO_ESPERA);
}

function _snEstadoGuardado(html, color) {
  const s = $id('sn-sucio');
  if (s) { s.style.color = color || ''; s.innerHTML = html; }
}

// PUT con el estado completo del formulario (mismo cuerpo que arma
// _snCuerpoCompleto). A diferencia del guardado manual de antes, un choque de
// versiones NO interrumpe a la persona con un diálogo: se vuelve a pedir la
// marca vigente y se reintenta con lo que está tipeado en pantalla -- en un
// equipo de planta, perder lo que alguien está cargando ahora mismo es peor
// que, alguna vez, pisar un campo que otra persona tocó en el mismo minuto.
async function _snAutoguardar() {
  if (_snAutoguardadoEnCurso) { _snAutoguardadoPendiente = true; return; }
  if (_snCongelada() || !_SN.actual) return;
  clearTimeout(_snAutoguardadoTimer);
  _snAutoguardadoEnCurso = true;
  _snAutoguardadoPendiente = false;
  _snEstadoGuardado('&#9679; guardando...', 'var(--muted)');
  const a = _SN.actual;
  try {
    for (let intentos = 1; ; intentos++) {
      const body = Object.assign({ modificado_en: a.modificado_en }, _snCuerpoCompleto());
      try {
        const d = await api('/api/sinterizados/' + a.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        // Del servidor solo se copia lo que EL SERVIDOR calcula (la marca de
        // versión y la firma de los pasos que se acaban de sellar); el resto
        // de _SN.actual sigue siendo lo que la persona tiene en pantalla, para
        // no pisar un cambio hecho mientras el PUT viajaba.
        a.modificado_en = d.modificado_en;
        a.modificado_por_nombre = d.modificado_por_nombre;
        a.modificado_por_legajo = d.modificado_por_legajo;
        for (const clave of Object.keys(a.pasos)) {
          if (a.pasos[clave] && a.pasos[clave].pendiente && d.pasos[clave]) {
            a.pasos[clave] = d.pasos[clave];
            _snRefrescar('sn-firma-' + clave, _snFirmaPasoHtml(a.pasos[clave]));
          }
        }
        if (_snAutoguardadoPendiente) _snEstadoGuardado('&#9679; cambios sin guardar', 'var(--yellow)');
        else { _SN.sucio = false; _snEstadoGuardado('Guardado autom&aacute;ticamente ' + _snHoraAhora(), 'var(--green)'); }
        return;
      } catch (e) {
        const m = _snMsgError(e);
        if (m.startsWith('CONFLICTO') && intentos < 3) {
          try { a.modificado_en = (await api('/api/sinterizados/' + a.id)).modificado_en; }
          catch (e2) { /* sigue con la marca vieja; se vuelve a pedir en el proximo intento */ }
          continue;
        }
        _snEstadoGuardado('&#9888; sin guardar: ' + _esc(m.replace(/^[A-Z]+: /, '')), 'var(--red)');
        // No se queda esperando que alguien reintente a mano: sigue probando
        // solo (sirve tanto para un choque raro como para que el equipo se
        // haya quedado sin red un rato).
        _snAutoguardadoTimer = setTimeout(_snAutoguardar, 8000);
        return;
      }
    }
  } finally {
    _snAutoguardadoEnCurso = false;
    if (_snAutoguardadoPendiente) { _snAutoguardadoPendiente = false; _snAutoguardar(); }
  }
}

function _snEstadoInicialTexto() {
  const a = _SN.actual;
  if (!a || a.estado !== 'en_curso' || !a.modificado_en) return '';
  return 'Guardado autom&aacute;ticamente ' + _snFechaHora(a.modificado_en).slice(-5);
}

function _snRenderDetalle() {
  const a = _SN.actual, cong = _snCongelada();
  const esAdmin = !!(_AUTH.user && _AUTH.user.is_admin);
  const meta = 'Creado por <b>' + _esc(a.creado_por_nombre || '') + '</b> el ' + _esc(_snFechaHora(a.creado_en))
    + ' &middot; &uacute;ltima modificaci&oacute;n de <b>' + _esc(a.modificado_por_nombre || '') + '</b> el ' + _esc(_snFechaHora(a.modificado_en))
    + (a.finalizado_en ? ' &middot; finalizado por <b>' + _esc(a.finalizado_por_nombre || '') + '</b> el ' + _esc(_snFechaHora(a.finalizado_en)) : '');
  const notaCong = cong
    ? ' &middot; <b style="color:var(--yellow)">planilla finalizada &mdash; ' + (esAdmin ? 'reabrila' : 'solo un administrador puede reabrirla') + ' para editarla</b>'
    : ' &middot; se guarda autom&aacute;ticamente';
  return `
    <div class="sn-bar">
      <button class="page-btn" onclick="_snVolver()">&larr; Lista</button>
      <div class="sn-titulo">Sinterizado N&deg; ${a.id} ${_snEstadoTag(a.estado)}</div>
      <span class="sn-sucio" id="sn-sucio" style="${cong ? 'display:none' : ''}">${_snEstadoInicialTexto()}</span>
      <div class="sn-acciones">
        ${cong
          ? (esAdmin
              ? '<button class="page-btn" onclick="_snReabrir()">Reabrir para editar</button>'
              : '<span style="font-size:11px;color:var(--muted)">Solo un administrador puede reabrir esta planilla</span>')
          : '<button class="page-btn" id="sn-guardar" onclick="_snGuardarAhora()">Guardar ahora</button>'
            + '<button class="page-btn" onclick="_snFinalizar()">Finalizar</button>'
            + '<button class="page-btn" onclick="_snAnular()">Anular</button>'}
        <button class="page-btn" onclick="_snImprimir()">&#128424; Imprimir planilla</button>
      </div>
    </div>
    <div class="sn-meta">${meta}${notaCong}</div>

    <div class="card sn-card">
      <div class="card-title">Avance del procedimiento y planilla <span class="sn-progreso" id="sn-progreso"></span></div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:10px">Cada paso trae los campos de la planilla PCS-PG851.06-A3 que le corresponden: completalos a medida que lo hac&eacute;s y tild&aacute; el paso al terminarlo.</div>
      <div class="sn-steps" id="sn-pasos"></div>
    </div>

    <div class="card sn-card">
      <div class="card-title">Observaciones</div>
      ${_snTxt('observaciones', 'Observaciones', 4)}
    </div>`;
}

// Campos de la planilla que corresponden a cada paso del procedimiento (las
// claves son las de _SN_PASOS). Hoja 1 = desarme; Hoja 2 = armado, repartida
// entre los pasos de piso, formaleta y paredes; el registro de temperatura es el
// del sinterizado. Los pasos de revoque y de manta no tienen campos en el papel.
function _snSinCampos() {
  return '<div class="sn-hint" style="color:var(--muted)">Este paso no tiene campos en la planilla: alcanza con tildarlo.</div>';
}
const _SN_FORM = {
  desarme: () => `
    <div class="sn-sub" style="margin-top:0">Planilla &middot; Hoja 1 (Desarme)</div>
    <div class="pv-row">
      ${_snIn('desarme_fecha', 'Fecha', { tipo: 'date' })}
      ${_snIn('coladas', 'Cantidad de coladas del crisol', { tipo: 'number' })}
      ${_snIn('diametro_final', '&Oslash; final (mm)', { tipo: 'number' })}
      ${_snIn('altura', 'Altura del crisol (mm)', { tipo: 'number' })}
      ${_snIn('altura_conicidad', 'Altura de la conicidad (mm)', { tipo: 'number' })}
    </div>
    ${_snTxt('aspecto', 'Aspecto general', 4)}
    <div class="pv-row">
      ${_snIn('espesor_min', 'Espesor m&iacute;nimo (mm)', { tipo: 'number', hint: 'Indicar la zona en el gr&aacute;fico con <b>ROJO</b>', color: _SN_MARCAS.espesor.color })}
      ${_snIn('filtraciones', 'Filtraciones total', { hint: 'Indicar en el gr&aacute;fico con <b>VERDE</b>', color: _SN_MARCAS.filtracion.color })}
      ${_snSiNo('manta_oscura', 'La manta se observa de color oscuro / quemado', { hint: 'Indicar la zona en el gr&aacute;fico con <b>AZUL</b>', color: _SN_MARCAS.manta.color })}
    </div>
    <div class="pv-row">
      ${_snSiNo('foto', 'Se tom&oacute; fotograf&iacute;a')}
      ${_snIn('foto_archivo', 'Archivo', { cls: 'pv-f2' })}
    </div>
    <div class="pv-row">
      ${_snSiNo('polvo', 'Durante el desarme se comprob&oacute; polvo refractario en contacto con la manta cer&aacute;mica', { cls: 'pv-f2' })}
      ${_snIn('polvo_mm', 'Cantidad aprox. (mm)', { tipo: 'number' })}
    </div>
    <div class="sn-sub">Gr&aacute;fico del crisol</div>
    <div id="sn-diag"></div>`,
  revoque: _snSinCampos,
  manta: _snSinCampos,
  antena_piso: () => `
    <div class="sn-sub" style="margin-top:0">Planilla &middot; Hoja 2 (Armado)</div>
    <div class="pv-row">${_snIn('armado_fecha', 'Fecha del armado', { tipo: 'date' })}</div>
    <div class="sn-sub">Colocaci&oacute;n de la antena</div>
    <div class="pv-row">${_snCheck('antena_orificio_ok', 'Se revis&oacute; que el orificio de la antena no est&eacute; demasiado grande')}</div>
    <div class="sn-sub">Construcci&oacute;n del piso</div>
    <div id="sn-bolsas-piso">${_snBolsasSeccionHtml('piso')}</div>
    <div class="sn-hint2">Las bolsas de paredes y corona se cargan en el paso &laquo;Construcci&oacute;n de las paredes&raquo;.</div>
    ${_snDatalistProductosHtml()}`,
  formaleta: () => `
    <div class="sn-sub" style="margin-top:0">Planilla &middot; Hoja 2 (Armado) &middot; Control de formaleta</div>
    <div class="pv-row" style="align-items:flex-start">
      ${_snIn('formaleta_medidas', 'Medidas', { cls: 'pv-f2' })}${_snCheck('formaleta_medidas_ok', 'Revisado')}
      ${_snIn('formaleta_rebabado', 'Rebabado', { cls: 'pv-f2' })}${_snCheck('formaleta_rebabado_ok', 'Revisado')}
    </div>
    <div class="sn-sub">Planilla &middot; Hoja 2 (Armado) &middot; Colocaci&oacute;n de formaleta</div>
    <div class="pv-row">
      ${_snFirma('centrado_reviso', 'Revis&oacute; centrado')}
      ${_snFirma('centrado_vb_fusion', 'V&deg;B&deg; Resp. de Fusi&oacute;n')}
      ${_snFirma('centrado_vb_produccion', 'V&deg;B&deg; Resp. de Producci&oacute;n')}
    </div>`,
  paredes: () => `
    <div class="sn-sub" style="margin-top:0">Planilla &middot; Hoja 2 (Armado) &middot; Colocaci&oacute;n de enduido, formaleta y revestimiento</div>
    <div class="sn-hint2">Las bolsas de piso se cargan en el paso &laquo;Colocaci&oacute;n de antena y construcci&oacute;n del piso&raquo;.</div>
    <div class="pv-row">
      ${_snFirma('paredes_vb_produccion', 'V&deg;B&deg;', { cls: 'pv-f3' })}
    </div>
    <div class="sn-sub">Construcci&oacute;n de las paredes</div>
    <div id="sn-bolsas-pared">${_snBolsasSeccionHtml('pared')}</div>
    <div class="sn-sub">Corona</div>
    <div id="sn-bolsas-corona">${_snBolsasSeccionHtml('corona')}</div>`,
  sinterizado: () => `
    <div class="sn-sub" style="margin-top:0">Planilla &middot; Registro de hora, potencia y temperatura</div>
    <div id="sn-lect"></div>
    <div id="sn-lect-resumen"></div>
    <div class="sn-sub">Curva de referencia</div>
    <div id="sn-curva-ref">${_snCurvaRefHtml(true)}</div>`,
};

// El detalle se arma de contenedores que se repintan por separado (pasos,
// dibujo, registro) para no perder el foco de lo que se esta tipeando.
function _snPintarPartes() {
  _snRefrescar('sn-pasos', _snPasosHtml(true));
  _snProgreso();
  _snRefrescar('sn-diag', _snDiagHtml());
  _snRefrescar('sn-lect', _snLecturasHtml());
  _snLectResumen();
}

// ── Pasos del procedimiento ──────────────────────────────────────────────────
function _snFirmaPasoHtml(h) {
  return !h ? '' : h.pendiente ? 'se firma al guardar'
    : 'Hecho por <b>' + _esc(h.por_nombre || '') + '</b> &middot; ' + _esc(_snFechaHora(h.en));
}
// `editable`: en la planilla se tildan y traen sus campos; en la pestaña
// Procedimiento son de lectura.
function _snPasosHtml(editable) {
  const pasos = editable && _SN.actual ? _SN.actual.pasos : {};
  const dis = _snDis();
  return _SN_PASOS.map((p, i) => {
    const h = pasos[p.clave];
    const cab = editable
      ? `<label class="sn-step-tit"><input type="checkbox" ${h ? 'checked' : ''} ${dis} onchange="_snPaso('${p.clave}', this.checked)"> ${_esc(p.titulo)}</label>`
      : `<span class="sn-step-tit" style="cursor:default">${_esc(p.titulo)}</span>`;
    return `<div class="sn-step${h ? ' hecho' : ''}" id="sn-paso-${p.clave}">
      <div class="sn-step-head"><span class="sn-step-num">${i + 1}</span>${cab}<span class="sn-step-firma" id="sn-firma-${p.clave}">${editable ? _snFirmaPasoHtml(h) : ''}</span></div>
      <ul>${p.detalles.map(d => '<li>' + _esc(d) + '</li>').join('')}</ul>
      ${p.nota ? '<div class="sn-nota">' + _esc(p.nota) + '</div>' : ''}
      ${editable && _SN_FORM[p.clave] ? '<div class="sn-campos">' + _SN_FORM[p.clave]() + '</div>' : ''}
    </div>`;
  }).join('');
}
function _snProgreso() {
  const n = _SN.actual ? Object.keys(_SN.actual.pasos).length : 0;
  _snRefrescar('sn-progreso', n + ' de ' + _SN_PASOS.length + ' pasos hechos');
}
function _snPaso(clave, marcado) {
  if (_snCongelada()) return;
  const pasos = _SN.actual.pasos;
  if (marcado) { if (!pasos[clave]) pasos[clave] = { por_nombre: _AUTH.user ? _AUTH.user.nombre : '', en: null, pendiente: true }; }
  else delete pasos[clave];
  _snSucio(true);
  const el = $id('sn-paso-' + clave);
  if (el) el.classList.toggle('hecho', !!pasos[clave]);
  _snRefrescar('sn-firma-' + clave, _snFirmaPasoHtml(pasos[clave]));
  _snProgreso();
}

// ── Dibujo: perfil del crisol (rota con un slider) + óvalos por altura (uno a
// la vez, con su propio slider) ───────────────────────────────────────────────
// Dos columnas independientes -- perfil y óvalo -- cada una se repinta sola
// (por eso #sn-perfil-col / #sn-ovalo-col) para no perder el foco de un campo
// de la otra columna ni el ángulo/altura que se estaba mirando.
function _snPluma(t) { _SN.pluma = t; _snRefrescar('sn-perfil-col', _snPerfilColHtml()); }

function _snDiagHtml() {
  return `<div class="sn-explorar">
    <div class="sn-explorar-col" id="sn-perfil-col">${_snPerfilColHtml()}</div>
    <div class="sn-explorar-col" id="sn-ovalo-col">${_snOvaloColHtml()}</div>
  </div>`;
}

// Medidas reales del crisol (mm): el horno sin refractario es un cilindro de
// Ø700 x 1100 de profundidad; adentro va la formaleta (Anexo del Procedimiento)
// que define la cavidad -- recta en Ø500 los primeros 800mm y cónica los
// últimos 250mm hasta Ø400. Los 50mm que sobran hasta el fondo del horno
// (1100 - 1050) son el piso, sin cavidad. El perfil dibuja esta cavidad (es lo
// que se ve al desarmar y mirar adentro), no la chapa exterior del horno.
const _SN_CRISOL = { hornoD: 700, hornoProf: 1100, formD_sup: 500, formD_inf: 400, formAlto: 1050, formConica: 250 };

function _snPerfilColHtml() {
  const cong = _snCongelada();
  const m = _SN.actual.marcas;
  const plumas = cong ? '' : `<div class="sn-plumas">Marcar con:
    ${Object.entries(_SN_MARCAS).map(([t, d]) => `<button type="button" class="sn-pluma${_SN.pluma === t ? ' sel' : ''}" onclick="_snPluma('${t}')"><i style="background:${d.color}"></i>${d.corto} &middot; ${d.texto}</button>`).join('')}
    <button type="button" class="page-btn" onclick="_snQuitarUltimoPunto()"${m.perfil.length ? '' : ' disabled'}>&#8630; Quitar &uacute;ltimo</button>
    <button type="button" class="page-btn" onclick="_snLimpiarMarcas()">Borrar todos</button></div>`;
  return `<div class="sn-sub" style="margin-top:0">Perfil del crisol</div>
    <div class="sn-hint2">Gir&aacute; el slider para explorar el crisol: los puntos marcados cerca del &aacute;ngulo actual se ven de frente, los del lado opuesto se ven tenues. Clic sobre el dibujo para marcar un punto al &aacute;ngulo actual; para sacar uno, usa los botones de ac&aacute; abajo (no se puede tocar un punto para borrarlo).</div>
    ${plumas}
    <div id="sn-perfil-svg">${_snSvgPerfil(m, !cong, _SN.perfilAngulo)}</div>
    <div class="sn-angulo-row">
      <input type="range" class="sn-angulo" min="0" max="359" value="${_SN.perfilAngulo}" oninput="_snAngulo(this.value)">
      <b id="sn-angulo-lbl">${_SN.perfilAngulo}&deg;</b>
    </div>`;
}

function _snAngulo(valor) {
  _SN.perfilAngulo = Math.max(0, Math.min(359, Math.round(Number(valor) || 0)));
  _snRefrescar('sn-perfil-svg', _snSvgPerfil(_SN.actual.marcas, !_snCongelada(), _SN.perfilAngulo));
  _snRefrescar('sn-angulo-lbl', _SN.perfilAngulo + '&deg;');
  _snRefrescar('sn-ovalo-svg', _snOvaloSvgHtml()); // sincroniza la flecha resaltada del óvalo con el ángulo nuevo
}

// Menor distancia angular entre dos ángulos (0-359), siempre 0-180.
function _snDistAngular(a, b) { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; }

// Perfil = la cavidad del crisol (lo que se ve al desarmar), dibujada como un
// recipiente de costado con la boca abierta -- pared recta en la zona de Ø500,
// se va angostando en la parte cónica hasta Ø400, y se cierra en el fondo
// (piso). Es un solo dibujo estático (no hay geometría 3D real), pero al girar
// el slider: la piquera (que SÍ está en un solo lugar real del horno) se corre
// de un lado a otro y se atenúa cuando queda "atrás", y los puntos marcados se
// ven de frente o tenues según qué tan cerca esté su ángulo guardado del
// ángulo actual -- eso es lo que da la sensación de explorarlo por todos lados.
// `anguloActual`: null en la planilla impresa (ahí se ven todos de frente, sin
// atenuar nada -- el papel no gira).
// mm desde la boca -> y del dibujo (0mm = borde del reborde, hornoProf = piso).
function _snY(mm) { return 8 + Math.min(1, Math.max(0, mm) / _SN_CRISOL.hornoProf) * 100; }
// Unidades de dibujo por mm: la pared recta mide 60u de ancho (Ø500, zona
// derecha) y 100u de alto (los 1100mm de profundidad) -- se reusa esa misma
// escala para dibujar la piquera a su tamaño real, no a ojo.
const _SN_ESCALA_X = 60 / _SN_CRISOL.formD_sup;
const _SN_ESCALA_Y = 100 / _SN_CRISOL.hornoProf;

// Medio ancho del crisol (desde el centro, x=50) a una altura y del dibujo:
// pared recta (30u, x=20 a x=80) hasta y=78, después se va cerrando en la
// curva del fondo hasta 0 en y=102. Sirve para que nada (piquera, puntos) se
// dibuje "flotando" fuera de la silueta al moverse con el ángulo.
function _snParedMedioAncho(y) {
  if (y <= 78) return 30;
  if (y >= 102) return 0;
  return 30 - ((y - 78) / (102 - 78)) * 6; // aproximación lineal de la curva del fondo (30 a 24) hasta 0
}

function _snSvgPerfil(marcas, activo, anguloActual) {
  const rad = ((anguloActual || 0) * Math.PI) / 180;
  // La piquera no es una pieza sobre la pared: es un canal cortado EN la pared
  // (no queda pared ahí) -- dos bordes rectos (interior y exterior del corte)
  // que se van juntando hacia abajo, como una canaleta. Está en el borde
  // superior del crisol (el reborde), no flotando más abajo, y tiene 180mm de
  // extremo (ancho) por 100mm de profundidad -- medidas reales, no a ojo.
  // Nunca se hace transparente al girar (se perdía de vista): "de frente" o
  // "de espaldas" se distingue por tamaño (más chico atrás) y por línea de
  // contorno punteada atrás, igual que los puntos lejanos del perfil.
  const piqY0 = 8; // el borde del reborde -- ahí arranca el corte, no más abajo
  const piqFactor = anguloActual == null ? 1 : (Math.cos(rad) + 1) / 2; // 1 de frente, 0 de espaldas
  const piqEscala = 0.55 + piqFactor * 0.45; // nunca baja de 55% del tamaño real
  const piqHalfAncho = (180 * _SN_ESCALA_X / 2) * piqEscala;
  const piqProf = (100 * _SN_ESCALA_Y) * piqEscala;
  const piqDeEspaldas = anguloActual != null && Math.cos(rad) < 0;
  // Se corre sobre el reborde sin salirse de él (medio ancho del reborde en
  // y=8 menos el medio ancho del propio corte, con un margen chico).
  const piqAmplitud = Math.max(0, _snParedMedioAncho(piqY0) - piqHalfAncho - 1);
  const piqX = 50 + Math.sin(rad) * piqAmplitud;
  const alturas = marcas.alturas || [];
  const ticks = alturas.map((al, idx) => {
    const y = _snY(al.mm);
    return `<g${activo ? ` style="cursor:pointer" onclick="_snIrAltura(${idx})"` : ''}><title>Medici&oacute;n ${idx + 1}: ${al.mm} mm</title>
      <line x1="13" y1="${y.toFixed(1)}" x2="20" y2="${y.toFixed(1)}" stroke="#4fc3f7" stroke-width="1.2"/>
      <text x="11" y="${(y + 1.3).toFixed(1)}" text-anchor="end" font-size="4" fill="#4fc3f7">${al.mm}</text></g>`;
  }).join('');
  const puntos = (marcas.perfil || []).map(p => {
    const dist = anguloActual == null ? 0 : _snDistAngular(p.a || 0, anguloActual);
    const cerca = dist <= 90;
    const opac = anguloActual == null ? 1 : (0.25 + 0.75 * Math.max(0, Math.cos((dist * Math.PI) / 180)));
    // Viajan con el giro: mismo vaivén que la piquera (seno de la diferencia de
    // ángulo), como desplazamiento sobre su propia posición -- así no se
    // amontonan todos en el centro apenas se crean, pero sí se corren al mover
    // el slider, en vez de quedarse fijos y solo atenuarse. Se recortan contra
    // la pared del crisol A SU PROPIA ALTURA (no contra el dibujo entero), para
    // que nunca aparezcan flotando afuera de la silueta.
    const medioAncho = Math.max(2, _snParedMedioAncho(p.y) - 2);
    const dispX = anguloActual == null ? p.x : Math.max(50 - medioAncho, Math.min(50 + medioAncho, p.x + Math.sin(((p.a || 0) - anguloActual) * Math.PI / 180) * 15));
    return `<circle cx="${dispX.toFixed(1)}" cy="${p.y}" r="3.2" fill="${_SN_MARCAS[p.t].color}" fill-opacity="${opac.toFixed(2)}" stroke="#fff" stroke-width=".6" stroke-opacity="${opac.toFixed(2)}"${cerca ? '' : ' stroke-dasharray="1.2 1"'}><title>${_SN_MARCAS[p.t].texto} (${p.a || 0}&deg;)</title></circle>`;
  }).join('');
  return `<svg viewBox="0 0 100 110" class="sn-svg-perfil"${activo ? ' style="cursor:crosshair" onclick="_snClickPerfil(event)"' : ''}>
    <path d="M20 8 L20 78 Q20 96 26 102 Q50 110 74 102 Q80 96 80 78 L80 8 Z" fill="#9aa4ad" fill-opacity=".4" stroke="#7d8790" stroke-width="1"/>
    <path d="M${(piqX - piqHalfAncho).toFixed(1)} ${piqY0.toFixed(1)} L${(piqX - piqHalfAncho * 0.35).toFixed(1)} ${(piqY0 + piqProf).toFixed(1)} L${(piqX + piqHalfAncho * 0.35).toFixed(1)} ${(piqY0 + piqProf).toFixed(1)} L${(piqX + piqHalfAncho).toFixed(1)} ${piqY0.toFixed(1)} Z" style="fill:var(--surface,#fff)" stroke="#7d8790" stroke-width=".6"${piqDeEspaldas ? ' stroke-dasharray="1 1"' : ''}/>
    <ellipse cx="50" cy="8" rx="30" ry="4" fill="#7d8790" fill-opacity=".5" stroke="#7d8790" stroke-width="1"/>
    <ellipse cx="50" cy="8" rx="22" ry="2.6" style="fill:var(--surface,#fff)" stroke="#7d8790" stroke-width=".6"/>
    <text x="50" y="109" text-anchor="middle" font-size="4.5" fill="#7d8790">piso</text>
    <text x="50" y="80" text-anchor="middle" font-size="4" fill="#7d8790">c&oacute;nica</text>
    ${ticks}${puntos}</svg>`;
}

// A qué flecha (0-7) le corresponde un ángulo del perfil (0-359), con el mismo
// reparto de 45° por flecha, centrado en cada flecha, que usa _snVistaHtml.
function _snFlechaDeAngulo(angulo) {
  return Math.floor((((angulo + 22.5) % 360) + 360) % 360 / 45) % 8;
}

// Vista en planta: un óvalo con 8 flechas y, en cada una, DOS medidas en mm: el
// espesor del refractario (r) y el espesor del polvo (p). En pantalla son dos
// casillas apiladas encima del dibujo, en la punta de cada flecha (como se
// escribe en el papel); impresa o con la planilla finalizada, dos renglones de
// solo lectura. `idx` identifica a ESTA medición dentro de marcas.alturas (para
// los ids del marker y de las casillas). `extremos` = { min, max } con la clave
// de la casilla del menor refractario y la del mayor polvo, que salen
// resaltadas (en cualquier medición, no solo en la que se está mirando).
// `flechaActiva` (0-7 o null): la flecha que corresponde al ángulo actual del
// perfil -- lo que sincroniza las dos vistas (ver _snFlechaDeAngulo).
// `imprimiendo`: true solo desde _snImprimir (ver el comentario de la foto,
// más abajo, sobre por qué ahí no se puede mostrar la imagen).
function _snVistaHtml(altura, idx, activo, extremos, flechaActiva, imprimiendo) {
  const ex = extremos || {};
  const medidas = altura.medidas || {};
  const W = 150, H = 130, cx = 75, cy = 65, rx = 28, ry = 21;
  let flechas = '', casillas = '';
  for (let k = 0; k < 8; k++) {
    const th = (22.5 + 45 * k) * Math.PI / 180, s = Math.sin(th), c = -Math.cos(th);
    const x1 = cx + (rx + 18) * s, y1 = cy + (ry + 14) * c;    // cola de la flecha
    const x2 = cx + (rx + 2) * s, y2 = cy + (ry + 2) * c;      // punta, contra el borde
    const px = cx + (rx + 38) * s, py = cy + (ry + 31) * c;    // centro del par de casillas
    const activa = k === flechaActiva;
    flechas += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${activa ? 'var(--accent,#4fc3f7)' : '#7d8790'}" stroke-width="${activa ? 2.2 : 1.3}" marker-end="url(#sn-fl-${idx}${activa ? '-a' : ''})"/>`;
    const v = medidas[String(k)] || {};
    const pos = `left:${(px / W * 100).toFixed(2)}%;top:${(py / H * 100).toFixed(2)}%`;
    const celda = (campo, et, titulo) => {
      const n = idx + '-' + k + '-' + campo;
      return activo
        ? `<input class="inp sn-med sn-med-${campo}${ex.min === n ? ' sn-min' : ex.max === n ? ' sn-max' : ''}" data-n="${n}" type="text" inputmode="decimal" placeholder="${et}" title="${titulo}" value="${_esc(_snV(v[campo]))}" oninput="_snMedida(${idx},${k},'${campo}',this.value)">`
        : `<span class="sn-med-v sn-med-${campo}" data-l="${et}">${v[campo] == null ? '&nbsp;' : _esc(v[campo])}</span>`;
    };
    casillas += `<div class="sn-medpar${activa ? ' sn-medpar-activa' : ''}" style="${pos}" title="${activa ? 'Dirección actual del corte' : ''}">${celda('r', 'ref.', 'Espesor del refractario (mm)')}${celda('p', 'polvo', 'Espesor del polvo (mm)')}</div>`;
  }
  // Foto de esta medición, en el centro del óvalo (el hueco que deja la
  // elipse interior): sin foto, un botón para elegirla; con foto, la miniatura
  // (clic para verla más grande) más un botón para reemplazarla y otro para
  // borrarla -- nada de esto último si está congelada (solo lectura). En
  // pantalla (aunque sea de solo lectura) el <img> se llena solo después
  // (_snCargarFotoActual, necesita el token de sesión); en la planilla
  // impresa (`imprimiendo`) no hay forma de cargarla ahí, así que solo se
  // avisa que existe en vez de dejar un ícono de imagen rota.
  const fotoHtml = !altura.foto
    ? (activo ? `<button type="button" class="page-btn sn-foto-btn" onclick="_snFotoElegir(${altura.mm})">&#128247; Foto</button>` : '')
    : (imprimiendo
      ? `<span style="font-size:10px;color:#7d8790">&#128247; (con foto, ver en el sistema)</span>`
      : `<div class="sn-foto-wrap">
        <img id="sn-foto-img" class="sn-foto-img" alt="Foto de la medici&oacute;n a ${altura.mm} mm" onclick="_snFotoVer()">
        ${activo ? `<div class="sn-foto-acciones">
          <button type="button" class="page-btn" onclick="_snFotoElegir(${altura.mm})" title="Reemplazar foto">&#128247;</button>
          <button type="button" class="page-btn" onclick="_snFotoEliminar()" title="Eliminar foto">&times;</button>
        </div>` : ''}
      </div>`);
  return `<div class="sn-vista"><svg viewBox="0 0 ${W} ${H}">
    <defs>
      <marker id="sn-fl-${idx}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#7d8790"/></marker>
      <marker id="sn-fl-${idx}-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="var(--accent,#4fc3f7)"/></marker>
    </defs>
    <polygon points="${cx - 12},${cy - ry} ${cx + 12},${cy - ry} ${cx + 8},${cy - ry - 12} ${cx - 8},${cy - ry - 12}" fill="#9aa4ad" fill-opacity=".35" stroke="#7d8790" stroke-width="1"/>
    <text x="${cx}" y="${cy - ry - 15}" text-anchor="middle" font-size="5" fill="#7d8790">piquera</text>
    <ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="#9aa4ad" fill-opacity=".35" stroke="#7d8790" stroke-width="1"/>
    <ellipse cx="${cx}" cy="${cy}" rx="${rx - 9}" ry="${ry - 7}" style="fill:var(--surface,#fff)" stroke="#7d8790" stroke-width="1"/>
    ${flechas}</svg><div class="sn-foto-centro" style="left:${(cx / W * 100).toFixed(2)}%;top:${(cy / H * 100).toFixed(2)}%">${fotoHtml}</div>${casillas}</div>`;
}

function _snOvaloColHtml() {
  const cong = _snCongelada();
  const alturas = _SN.actual.marcas.alturas;
  if (_SN.alturaIdx >= alturas.length) _SN.alturaIdx = Math.max(0, alturas.length - 1);
  const actual = alturas[_SN.alturaIdx];
  const ext = _snExtremos();
  const extremos = { min: ext.min ? ext.min.clave : null, max: ext.max ? ext.max.clave : null };
  const nueva = cong ? '' : (_SN.nuevaAlturaAbierta
    ? `<div class="sn-nueva-altura">
        <input type="number" id="sn-nueva-altura-mm" class="inp" placeholder="mm desde la boca" min="0" max="${_SN_CRISOL.hornoProf + 100}" step="1" style="width:150px" onkeydown="if(event.key==='Enter')_snCrearAltura()">
        <button type="button" class="btn-primary" onclick="_snCrearAltura()">Crear</button>
        <button type="button" class="page-btn" onclick="_snCancelarNuevaAltura()">Cancelar</button>
      </div>`
    : `<button type="button" class="page-btn" onclick="_snAbrirNuevaAltura()">&#10133; Nueva medici&oacute;n</button>`);
  const nav = actual ? `<div class="sn-angulo-row">
      <button type="button" class="page-btn" onclick="_snIrAltura(${_SN.alturaIdx - 1})"${_SN.alturaIdx === 0 ? ' disabled' : ''}>&larr;</button>
      <input type="range" class="sn-angulo" min="0" max="${Math.max(0, alturas.length - 1)}" value="${_SN.alturaIdx}" oninput="_snIrAltura(this.value)"${alturas.length < 2 ? ' disabled' : ''}>
      <button type="button" class="page-btn" onclick="_snIrAltura(${_SN.alturaIdx + 1})"${_SN.alturaIdx >= alturas.length - 1 ? ' disabled' : ''}>&rarr;</button>
      <b>${_snNum(actual.mm)} mm</b> <span style="color:var(--muted);font-size:11px">(medici&oacute;n ${_SN.alturaIdx + 1} de ${alturas.length})</span>
      ${cong ? '' : `<span class="sn-quitar" title="Quitar esta medici&oacute;n" onclick="_snQuitarAltura(${_SN.alturaIdx})">&times;</span>`}
    </div>` : '';
  return `<input type="file" id="sn-foto-input" accept="image/*" style="display:none" onchange="_snFotoArchivoElegido(this)">
    <div class="sn-sub" style="margin-top:0">&Oacute;valos de medici&oacute;n</div>
    <div class="sn-hint2">Cada medici&oacute;n es un &oacute;valo a una altura elegida (mm desde la boca), con el espesor de <i class="sn-sw" style="background:#ffa726"></i>refractario y <i class="sn-sw" style="background:#8d6e63"></i>polvo en sus 8 flechas, y una foto opcional. La flecha celeste es hacia d&oacute;nde mira el corte del perfil ahora mismo.</div>
    ${nueva}
    <div id="sn-ovalo-svg">${_snOvaloSvgHtml()}</div>
    ${nav}
    <div id="sn-med-resumen" style="font-size:12px;margin-top:10px">${_snMedidaResumenHtml()}</div>`;
}

// Lo que va DENTRO de #sn-ovalo-svg: el óvalo actual (o el mensaje vacío), con
// la flecha resaltada que corresponde al ángulo actual del perfil (ver
// _snFlechaDeAngulo) -- factorizado aparte para poder refrescar solo esto
// cuando cambia el ángulo del perfil (_snAngulo), sin tocar el resto de la
// columna del óvalo.
function _snOvaloSvgHtml() {
  const cong = _snCongelada();
  const alturas = _SN.actual.marcas.alturas;
  const actual = alturas[_SN.alturaIdx];
  if (!actual) return '<div class="sn-vacio">Todav&iacute;a no hay ninguna medici&oacute;n. Toc&aacute; &laquo;Nueva medici&oacute;n&raquo; para crear el primer &oacute;valo.</div>';
  const ext = _snExtremos();
  const extremos = { min: ext.min ? ext.min.clave : null, max: ext.max ? ext.max.clave : null };
  const html = _snVistaHtml(actual, _SN.alturaIdx, !cong, extremos, _snFlechaDeAngulo(_SN.perfilAngulo));
  _snCargarFotoActual(); // se dispara solo (async): para cuando resuelva, el <img> ya está en el DOM
  return html;
}

function _snAbrirNuevaAltura() { _SN.nuevaAlturaAbierta = true; _snRefrescar('sn-ovalo-col', _snOvaloColHtml()); const i = $id('sn-nueva-altura-mm'); if (i) i.focus(); }
function _snCancelarNuevaAltura() { _SN.nuevaAlturaAbierta = false; _snRefrescar('sn-ovalo-col', _snOvaloColHtml()); }

function _snCrearAltura() {
  if (_snCongelada()) return;
  const inp = $id('sn-nueva-altura-mm');
  const mm = Number(inp && inp.value);
  if (!isFinite(mm) || mm < 0 || mm > _SN_CRISOL.hornoProf + 100) { alert('Ingresá una altura válida (mm desde la boca, hasta ' + (_SN_CRISOL.hornoProf + 100) + ').'); return; }
  const alturas = _SN.actual.marcas.alturas;
  if (alturas.length >= 30) { alert('Máximo 30 mediciones por planilla.'); return; }
  if (alturas.some(a => Math.abs(a.mm - mm) < 1)) { alert('Ya hay una medición a esa altura.'); return; }
  const redondeada = Math.round(mm);
  alturas.push({ mm: redondeada, medidas: {} });
  alturas.sort((a, b) => a.mm - b.mm);
  _SN.alturaIdx = alturas.findIndex(a => a.mm === redondeada); // mm es unico: se rechaza arriba si ya existia
  _SN.nuevaAlturaAbierta = false;
  _snSucio(true);
  _snRefrescar('sn-ovalo-col', _snOvaloColHtml());
}
function _snQuitarAltura(idx) {
  if (_snCongelada()) return;
  const alturas = _SN.actual.marcas.alturas;
  const altura = alturas[idx];
  if (!altura) return;
  if (!confirm('¿Quitar la medición a ' + altura.mm + ' mm? Se pierden los valores cargados ahí' + (altura.foto ? ' y la foto' : '') + '.')) return;
  // La foto vive aparte (no en marcas.alturas -- ver POST/DELETE .../foto en
  // main.py), así que borrar la medición no se la lleva sola: se pide aparte,
  // sin bloquear la UI por esto (si falla, queda una foto huérfana en el
  // servidor -- no rompe nada, solo ocupa lugar).
  if (altura.foto) api('/api/sinterizados/' + _SN.actual.id + '/alturas/' + altura.mm + '/foto', { method: 'DELETE' }).catch(() => {});
  alturas.splice(idx, 1);
  _SN.alturaIdx = Math.min(_SN.alturaIdx, Math.max(0, alturas.length - 1));
  _snSucio(true);
  _snRefrescar('sn-ovalo-col', _snOvaloColHtml());
}

// ── Foto de la medición actual ───────────────────────────────────────────────
// Vive aparte de marcas.alturas (un archivo por planilla+mm en el servidor, no
// en el JSON) -- no compite con el guardado automático ni con su choque de
// ediciones, ver POST/GET/DELETE /api/sinterizados/{id}/alturas/{mm}/foto.
function _snFotoElegir(mm) {
  if (_snCongelada()) return;
  _SN.fotoMmActual = mm;
  const inp = $id('sn-foto-input');
  if (inp) { inp.value = ''; inp.click(); }
}

async function _snFotoArchivoElegido(inp) {
  const file = inp.files && inp.files[0];
  const mm = _SN.fotoMmActual;
  if (!file || mm == null || _snCongelada()) return;
  const fd = new FormData();
  fd.append('file', file, file.name || 'foto.jpg');
  try {
    const resp = await fetch('/api/sinterizados/' + _SN.actual.id + '/alturas/' + mm + '/foto', {
      method: 'POST', headers: { Authorization: 'Bearer ' + _AUTH.token }, body: fd
    });
    if (!resp.ok) throw new Error(await resp.text());
    const altura = _SN.actual.marcas.alturas.find(a => a.mm === mm);
    if (altura) altura.foto = true;
    if (_SN.actual.marcas.alturas[_SN.alturaIdx] === altura) _snRefrescar('sn-ovalo-svg', _snOvaloSvgHtml());
  } catch (e) {
    alert('No se pudo subir la foto: ' + _snMsgError(e).replace(/^[A-Z]+: /, ''));
  }
}

function _snFotoVer() {
  const img = $id('sn-foto-img');
  if (img && img.src) window.open(img.src, '_blank');
}

async function _snFotoEliminar() {
  const altura = _SN.actual.marcas.alturas[_SN.alturaIdx];
  if (_snCongelada() || !altura || !altura.foto) return;
  if (!confirm('¿Eliminar la foto de esta medición (' + altura.mm + ' mm)?')) return;
  try {
    await api('/api/sinterizados/' + _SN.actual.id + '/alturas/' + altura.mm + '/foto', { method: 'DELETE' });
    altura.foto = false;
    _snRefrescar('sn-ovalo-svg', _snOvaloSvgHtml());
  } catch (e) {
    alert('No se pudo eliminar la foto: ' + _snMsgError(e).replace(/^[A-Z]+: /, ''));
  }
}

// Trae la foto de la medición que se está mirando AHORA y la mete en el <img>
// (necesita el token de sesión, así que no puede ir directo en un src="..."
// como una imagen pública -- mismo motivo por el que Proveedores hace lo
// mismo con sus fotos). Si mientras viajaba la persona ya se movió a otra
// medición u otra planilla, no la pisa: se fija que siga siendo la misma.
async function _snCargarFotoActual() {
  const altura = _SN.actual && _SN.actual.marcas.alturas[_SN.alturaIdx];
  if (!altura || !altura.foto) return;
  const sid = _SN.actual.id, mm = altura.mm;
  try {
    const resp = await fetch('/api/sinterizados/' + sid + '/alturas/' + mm + '/foto', { headers: { Authorization: 'Bearer ' + _AUTH.token } });
    if (!resp.ok) return;
    const blob = await resp.blob();
    const sigue = _SN.actual && _SN.actual.id === sid && _SN.actual.marcas.alturas[_SN.alturaIdx] === altura;
    const img = $id('sn-foto-img');
    if (sigue && img) img.src = URL.createObjectURL(blob);
  } catch (e) { /* silencioso: si no carga, se queda el botón/placeholder */ }
}
function _snIrAltura(idx) {
  const alturas = _SN.actual.marcas.alturas;
  if (!alturas.length) return;
  _SN.alturaIdx = Math.max(0, Math.min(alturas.length - 1, Math.round(Number(idx)) || 0));
  _snRefrescar('sn-ovalo-col', _snOvaloColHtml());
}

// Numero de una casilla de texto ("62,5" o " 48 "), o null si esta vacia o no es un numero.
function _snNumTxt(v) {
  if (v == null || String(v).trim() === '') return null;
  const n = Number(String(v).replace(',', '.'));
  return isFinite(n) ? n : null;
}
// Menor espesor de REFRACTARIO y mayor espesor de POLVO entre TODAS las
// mediciones cargadas (no solo la que se está mirando). Cada uno:
// { valor, clave: 'idx-flecha-r|p', idx, mm } o null si no hay ninguno.
function _snExtremos() {
  let min = null, max = null;
  (_SN.actual.marcas.alturas || []).forEach((altura, idx) => {
    for (const [k, par] of Object.entries(altura.medidas || {})) {
      const r = _snNumTxt(par.r), p = _snNumTxt(par.p);
      if (r != null && (!min || r < min.valor)) min = { valor: r, clave: idx + '-' + k + '-r', idx, mm: altura.mm };
      if (p != null && (!max || p > max.valor)) max = { valor: p, clave: idx + '-' + k + '-p', idx, mm: altura.mm };
    }
  });
  return { min, max };
}
function _snMedidaResumenHtml() {
  const { min, max } = _snExtremos();
  if (!min && !max) return '<span style="color:var(--muted)">Todav&iacute;a no hay medidas cargadas.</span>';
  const ver = idx => idx === _SN.alturaIdx ? '' : ` <button type="button" class="page-btn" style="margin-left:4px" onclick="_snIrAltura(${idx})">ver</button>`;
  const boton = (fn, txt) => _snCongelada() ? '' : ' <button type="button" class="page-btn" style="margin-left:6px" onclick="' + fn + '()">' + txt + '</button>';
  const partes = [];
  if (min) partes.push('Menor espesor de refractario: <b style="color:' + _SN_MARCAS.espesor.color + '">' + _snNum(min.valor) + ' mm</b> (a ' + _snNum(min.mm) + ' mm' + ver(min.idx) + ')'
    + boton('_snUsarMinimo', 'Usar como espesor m&iacute;nimo de la Hoja 1'));
  if (max) partes.push('Mayor espesor de polvo: <b style="color:#8d6e63">' + _snNum(max.valor) + ' mm</b> (a ' + _snNum(max.mm) + ' mm' + ver(max.idx) + ')'
    + boton('_snUsarPolvoMax', 'Usar como cantidad de polvo de la Hoja 1'));
  return partes.join('<br>');
}
function _snMedidaRefrescar() {
  _snRefrescar('sn-med-resumen', _snMedidaResumenHtml());
  const { min, max } = _snExtremos();
  document.querySelectorAll('#sn-ovalo-svg .sn-med').forEach(el => {
    el.classList.toggle('sn-min', !!min && el.dataset.n === min.clave);
    el.classList.toggle('sn-max', !!max && el.dataset.n === max.clave);
  });
}
// `idx`: posición dentro de marcas.alturas. `campo`: 'r' (refractario) o 'p'
// (polvo). Vaciar una casilla la borra; si la flecha queda sin nada, se va.
function _snMedida(idx, k, campo, valor) {
  if (_snCongelada()) return;
  const altura = _SN.actual.marcas.alturas[idx];
  if (!altura) return;
  const v = String(valor).trim();
  const par = altura.medidas[k] || (altura.medidas[k] = {});
  if (v === '') delete par[campo]; else par[campo] = v;
  if (!Object.keys(par).length) delete altura.medidas[k];
  _snSucio(false);
  _snMedidaRefrescar();
}
function _snUsarMinimo() {
  const { min } = _snExtremos();
  if (!min || _snCongelada()) return;
  _SN.actual.espesor_min = min.valor;
  const inp = document.querySelector('#sn-content [data-f="espesor_min"]');
  if (inp) inp.value = min.valor;
  _snSucio(true);
}
function _snUsarPolvoMax() {
  const { max } = _snExtremos();
  if (!max || _snCongelada()) return;
  _SN.actual.polvo_mm = max.valor;
  const inp = document.querySelector('#sn-content [data-f="polvo_mm"]');
  if (inp) inp.value = max.valor;
  _snSucio(true);
}

// ── Bolsas de refractario (paso 4: piso / pared / corona) ───────────────────
// Cada sección es una lista aparte (0 a N bolsas), cada una con cuántas
// bolsas, qué producto (sugerido desde Proveedores > Material Refractario,
// con texto libre si no está en la lista) y el lote para trazabilidad.
const _SN_ETIQUETA_BOLSA = { piso: 'piso', pared: 'pared', corona: 'corona' };

function _snBolsasSeccionHtml(seccion) {
  const dis = _snDis();
  const lista = (_SN.actual.bolsas && _SN.actual.bolsas[seccion]) || [];
  const filas = lista.map((b, i) => `<tr>
    <td><input class="inp" type="number" step="any" min="0" style="width:80px" value="${_esc(_snV(b.cantidad))}" ${dis} oninput="_snBolsa('${seccion}',${i},'cantidad',this.value)"></td>
    <td><input class="inp" type="text" list="sn-prod-refractarios" style="width:100%;min-width:180px" placeholder="Producto" value="${_esc(_snV(b.producto))}" ${dis} oninput="_snBolsa('${seccion}',${i},'producto',this.value)"></td>
    <td><input class="inp" type="text" style="width:140px" placeholder="Lote" value="${_esc(_snV(b.lote))}" ${dis} oninput="_snBolsa('${seccion}',${i},'lote',this.value)"></td>
    <td>${dis ? '' : `<span class="sn-quitar" title="Quitar bolsa" onclick="_snQuitarBolsa('${seccion}',${i})">&times;</span>`}</td>
  </tr>`).join('');
  return `<div class="tbl-wrap">${lista.length ? `<table class="sn-lect">
      <thead><tr><th>Bolsas</th><th>Producto</th><th>Lote</th><th></th></tr></thead><tbody>${filas}</tbody></table>`
    : `<div class="sn-vacio">Sin bolsas de ${_SN_ETIQUETA_BOLSA[seccion]} cargadas.</div>`}</div>
    ${dis ? '' : `<div style="margin-top:6px"><button type="button" class="page-btn" onclick="_snAgregarBolsa('${seccion}')">&#10133; Agregar bolsa</button></div>`}`;
}

function _snAgregarBolsa(seccion) {
  if (_snCongelada()) return;
  const lista = _SN.actual.bolsas[seccion] || (_SN.actual.bolsas[seccion] = []);
  if (lista.length >= 30) { alert('Máximo 30 bolsas por sección.'); return; }
  lista.push({ cantidad: '', producto: '', lote: '' });
  _snSucio(true);
  _snRefrescar('sn-bolsas-' + seccion, _snBolsasSeccionHtml(seccion));
}
function _snQuitarBolsa(seccion, i) {
  if (_snCongelada()) return;
  const lista = _SN.actual.bolsas[seccion];
  if (!lista || !lista[i]) return;
  lista.splice(i, 1);
  _snSucio(true);
  _snRefrescar('sn-bolsas-' + seccion, _snBolsasSeccionHtml(seccion));
}
function _snBolsa(seccion, i, campo, valor) {
  if (_snCongelada()) return;
  const lista = _SN.actual.bolsas[seccion];
  if (!lista || !lista[i]) return;
  lista[i][campo] = valor;
  _snSucio(false);
}

// Catálogo de Proveedores > Material Refractario, para sugerir el producto de
// cada bolsa (datalist: admite elegir de la lista o escribir uno que no está).
// Se carga una vez al entrar a la pestaña, no por planilla.
async function _snCargarProductosRefractarios() {
  try { _SN.productosRefractarios = await api('/api/sinterizados/productos-refractarios'); }
  catch (e) { _SN.productosRefractarios = []; }
}
function _snDatalistProductosHtml() {
  return `<datalist id="sn-prod-refractarios">${(_SN.productosRefractarios || []).map(p => `<option value="${_esc(p.nombre)}">`).join('')}</datalist>`;
}

// Clic en cualquier lado del dibujo agrega un punto ahí -- ya no se puede
// tocar un punto para borrarlo puntual (era fácil de gatillar sin querer al
// intentar marcar uno nuevo cerca de otro ya puesto); la única forma de sacar
// uno es "Quitar último punto" o "Borrar todos" (con confirmación), más abajo.
function _snClickPerfil(ev) {
  if (_snCongelada()) return;
  const r = ev.currentTarget.getBoundingClientRect();
  const x = (ev.clientX - r.left) / r.width * 100, y = (ev.clientY - r.top) / r.height * 110;
  if (!(x >= 0 && x <= 100 && y >= 0 && y <= 110)) return;
  if (_SN.actual.marcas.perfil.length >= 300) { alert('Ya hay demasiados puntos en el gráfico (máximo 300).'); return; }
  _SN.actual.marcas.perfil.push({ x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, t: _SN.pluma, a: _SN.perfilAngulo });
  _snSucio(true);
  _snRefrescar('sn-perfil-col', _snPerfilColHtml());
}
// Deshacer de a uno: saca el ultimo punto puesto en el perfil, sin confirmar
// (es la forma rápida de corregir un clic de más).
function _snQuitarUltimoPunto() {
  if (_snCongelada() || !_SN.actual.marcas.perfil.length) return;
  _SN.actual.marcas.perfil.pop();
  _snSucio(true);
  _snRefrescar('sn-perfil-col', _snPerfilColHtml());
}
function _snLimpiarMarcas() {
  if (_snCongelada() || !_SN.actual.marcas.perfil.length) return;
  if (!confirm('¿Borrar TODOS los puntos de color del perfil? (Las mediciones no se tocan. Para sacar solo el último, usá «Quitar último punto».)')) return;
  _SN.actual.marcas.perfil = [];
  _snSucio(true);
  _snRefrescar('sn-perfil-col', _snPerfilColHtml());
}

// ── Registro de temperatura ──────────────────────────────────────────────────
function _snLecturasHtml() {
  const L = _SN.actual.lecturas, dis = _snDis();
  const filas = L.map((x, i) => `<tr>
    <td class="sn-idx">${i + 1}</td>
    <td><input class="inp" type="time" style="width:110px" value="${_esc(_snV(x.hora))}" ${dis} oninput="_snLect(${i},'hora',this.value)" onchange="_snLectResumen()"></td>
    <td><input class="inp" type="text" inputmode="decimal" style="width:80px" value="${_esc(_snV(x.pot))}" ${dis} oninput="_snLect(${i},'pot',this.value)"></td>
    <td><input class="inp" type="number" step="any" min="0" style="width:100px" value="${_esc(_snV(x.temp))}" ${dis} oninput="_snLect(${i},'temp',this.value)" onchange="_snLectResumen()"></td>
    <td style="width:100%"><input class="inp" type="text" style="width:100%;min-width:200px" value="${_esc(_snV(x.obs))}" ${dis} oninput="_snLect(${i},'obs',this.value)"></td>
    <td>${dis ? '' : `<span class="sn-quitar" title="Quitar lectura" onclick="_snQuitarLect(${i})">&times;</span>`}</td>
  </tr>`).join('');
  return `<div class="tbl-wrap">${L.length ? `<table class="sn-lect">
      <thead><tr><th></th><th>Hora</th><th>Pot.</th><th>Temp. (&deg;C)</th><th>Observaciones</th><th></th></tr></thead><tbody>${filas}</tbody></table>`
    : '<div class="sn-vacio">Sin lecturas todavía.</div>'}</div>
    ${dis ? '' : '<div style="margin-top:8px"><button class="page-btn" onclick="_snAgregarLect()">&#10133; Agregar lectura (hora actual)</button></div>'}`;
}
function _snLect(i, campo, v) {
  if (_snCongelada() || !_SN.actual.lecturas[i]) return;
  _SN.actual.lecturas[i][campo] = v;
  _snSucio(false);
}
function _snAgregarLect() {
  if (_snCongelada()) return;
  if (_SN.actual.lecturas.length >= 300) { alert('Máximo 300 lecturas por planilla.'); return; }
  _SN.actual.lecturas.push({ hora: _snHoraAhora(), pot: '', temp: '', obs: '' });
  _snSucio(true);
  _snRefrescar('sn-lect', _snLecturasHtml());
  _snLectResumen();
}
function _snQuitarLect(i) {
  if (_snCongelada()) return;
  _SN.actual.lecturas.splice(i, 1);
  _snSucio(true);
  _snRefrescar('sn-lect', _snLecturasHtml());
  _snLectResumen();
}

// Lecturas con hora y temperatura, en el orden cargado. Si la hora retrocede se
// asume que se cruzo la medianoche (un sinterizado largo puede hacerlo).
function _snSerie(L) {
  const out = [];
  let dia = 0, prev = null;
  for (const x of L) {
    const temp = Number(x.temp);
    if (!x.hora || x.temp === '' || x.temp == null || !isFinite(temp)) continue;
    const [h, m] = x.hora.split(':').map(Number);
    let t = h * 60 + m + dia * 1440;
    if (prev != null && t < prev) { dia++; t += 1440; }
    prev = t;
    out.push({ t, temp, hora: x.hora });
  }
  return out;
}

// Gráfico real (accent, sólido) + curva de referencia (violeta, punteada) si
// hay una cargada -- se superponen en "minutos transcurridos": la real resta
// su propia primera hora (t0), la de referencia ya viene en minutos desde el
// arranque (0-based), así que ambas arrancan en el mismo punto del eje X sin
// importar a qué hora de reloj empezó esta colada en particular.
// `rango`: ancho TOTAL (°C) de la banda de tolerancia alrededor de la curva
// de referencia (±rango/2) -- se dibuja como una franja violeta traslúcida
// detrás de la línea, para ver de un vistazo si la curva real se sale.
function _snGraficoTemp(serie, referencia, rango) {
  referencia = referencia || [];
  rango = rango || 0;
  if (serie.length < 2 && referencia.length < 2) return '<div class="sn-vacio">Cargá al menos 2 lecturas con hora y temperatura (o mirá la curva de referencia, más abajo) para ver el gráfico.</div>';
  const W = 640, H = 220, pl = 44, pr = 14, pt = 12, pb = 26;
  const t0 = serie.length ? serie[0].t : 0;
  const elapsed = serie.map(p => p.t - t0);
  const spanReal = elapsed.length ? elapsed[elapsed.length - 1] : 0;
  const spanRef = referencia.length ? referencia[referencia.length - 1].minuto : 0;
  const span = Math.max(spanReal, spanRef, 1);
  const mitadRango = rango / 2;
  const temps = serie.map(p => p.temp).concat(referencia.map(p => p.temp + mitadRango), [_SN_META_C]);
  const ymax = Math.max(1700, Math.ceil(Math.max(...temps) / 100) * 100);
  const X = t => pl + t / span * (W - pl - pr), Y = v => pt + (1 - v / ymax) * (H - pt - pb);
  let grilla = '';
  for (let v = 0; v <= ymax; v += 500) grilla += `<line class="sn-g" x1="${pl}" x2="${W - pr}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text x="${pl - 6}" y="${(Y(v) + 3).toFixed(1)}" text-anchor="end">${v}</text>`;
  const pts = elapsed.map((t, i) => X(t).toFixed(1) + ',' + Y(serie[i].temp).toFixed(1)).join(' ');
  const puntos = serie.map((p, i) => `<circle cx="${X(elapsed[i]).toFixed(1)}" cy="${Y(p.temp).toFixed(1)}" r="2.6" fill="var(--accent)"><title>${p.hora} · ${p.temp} °C</title></circle>`).join('');
  const ptsRef = referencia.map(p => X(p.minuto).toFixed(1) + ',' + Y(p.temp).toFixed(1)).join(' ');
  // La escala del gráfico llega a ~1700 °C (para que entre la meta de
  // sinterizado); un rango de tolerancia de 10 °C ahí da una franja de 1-2
  // unidades de alto -- invisible. Para que SE VEA, el dibujo de la banda
  // nunca baja de _SN_BANDA_MIN_ALTO de alto, aunque en escala real le
  // corresponderían menos; la leyenda de abajo siempre muestra el ± real,
  // nunca el exagerado -- lo único que se estira es el dibujo.
  const _SN_BANDA_MIN_ALTO = 6;
  const altoBandaReal = mitadRango * 2 / ymax * (H - pt - pb);
  const mitadDibujo = (altoBandaReal > 0 && altoBandaReal < _SN_BANDA_MIN_ALTO) ? mitadRango * (_SN_BANDA_MIN_ALTO / altoBandaReal) : mitadRango;
  const banda = (referencia.length >= 2 && rango > 0)
    ? `<polygon class="sn-banda-ref" points="${referencia.map(p => X(p.minuto).toFixed(1) + ',' + Y(p.temp + mitadDibujo).toFixed(1)).join(' ')} ${referencia.slice().reverse().map(p => X(p.minuto).toFixed(1) + ',' + Y(p.temp - mitadDibujo).toFixed(1)).join(' ')}"/>`
    : '';
  const leyenda = (serie.length >= 2 && referencia.length >= 2)
    ? `<div class="sn-graf-leyenda"><span><i class="sn-sw" style="background:var(--accent)"></i>Real</span><span><i class="sn-sw" style="background:#ab47bc"></i>Curva de referencia${rango > 0 ? ` (&plusmn;${_snNum(mitadRango)} &deg;C)` : ''}</span></div>` : '';
  return leyenda + `<svg class="sn-graf" viewBox="0 0 ${W} ${H}">${grilla}
    ${banda}
    <line class="sn-lmeta" x1="${pl}" x2="${W - pr}" y1="${Y(_SN_META_C).toFixed(1)}" y2="${Y(_SN_META_C).toFixed(1)}"/>
    <text class="sn-metatxt" x="${W - pr}" y="${(Y(_SN_META_C) - 4).toFixed(1)}" text-anchor="end">${_SN_META_C} °C</text>
    ${referencia.length >= 2 ? `<polyline class="sn-curva-ref" points="${ptsRef}"/>` : ''}
    ${serie.length >= 2 ? `<polyline class="sn-curva" points="${pts}"/>${puntos}` : ''}
    ${serie.length ? `<text x="${X(elapsed[0]).toFixed(1)}" y="${H - 8}">${serie[0].hora}</text><text x="${X(elapsed[elapsed.length - 1]).toFixed(1)}" y="${H - 8}" text-anchor="end">${serie[serie.length - 1].hora}</text>` : ''}
    </svg>`;
}

function _snLectResumen() {
  const el = $id('sn-lect-resumen');
  if (!el || !_SN.actual) return;
  const serie = _snSerie(_SN.actual.lecturas);
  el.innerHTML = _snGraficoTemp(serie, _SN.curvaRef && _SN.curvaRef.puntos, _SN.curvaRef && _SN.curvaRef.rango);
}

// ── Curva de referencia del paso Sinterizado ─────────────────────────────────
// UNA sola curva, compartida por todas las planillas (no es un campo de ESTA
// planilla): un ejemplo de rampa a seguir, superpuesto al gráfico de arriba.
// Verla es parte de ver la pestaña (permiso 'sinterizado'); para editarla
// hace falta el permiso aparte 'sinterizado_curva' (o ser admin) -- cambiarla
// afecta lo que ve todo el mundo de ahí en más, no solo esta planilla.
//
// Una temperatura cada _SN_CURVA_PASO_MIN minutos, fijo -- no se tipea el
// minuto: es la POSICIÓN (i-ésimo punto = i*paso minutos). _SN.curvaRef.puntos
// guarda SOLO los puntos ya cargados (nunca uno vacío); el renglón en blanco
// del final es puramente de pantalla (no vive en el estado) y al completarlo
// (onchange, es decir al salir del campo) se suma como punto real y aparece
// solo el siguiente renglón en blanco -- así nunca se puede guardar un punto
// a medio cargar (esa fue la causa de que la curva no guardara: un renglón
// con el minuto vacío hacía fallar la validación del PUT entero).
const _SN_CURVA_PASO_MIN = 30;
const _SN_CURVA_MAX_PUNTOS = 100;
const _SN_CURVA_RANGO_DEFECTO = 10; // °C de ancho total de la banda de tolerancia, hasta que alguien la cambie
async function _snCargarCurvaRef() {
  try { _SN.curvaRef = await api('/api/sinterizados/curva-referencia'); }
  catch (e) { _SN.curvaRef = { puntos: [], rango: _SN_CURVA_RANGO_DEFECTO, cambiado_por_nombre: null, cambiado_en: null }; }
}
function _snPuedeEditarCurva() {
  return !!(_AUTH.user && (_AUTH.user.is_admin || (_AUTH.user.secciones || []).includes('sinterizado_curva')));
}
// `soloLectura`: true en el paso 7 de una planilla (ahí solo se MUESTRA, junto
// con el gráfico) -- la edición vive únicamente en la pestaña principal
// «Curva de referencia» (_snRenderCurvaTab), sea quien sea que esté mirando
// una planilla puntual en ese momento.
function _snCurvaRefHtml(soloLectura) {
  const puede = !soloLectura && _snPuedeEditarCurva();
  const puntos = (_SN.curvaRef && _SN.curvaRef.puntos) || [];
  const rango = (_SN.curvaRef && _SN.curvaRef.rango != null) ? _SN.curvaRef.rango : _SN_CURVA_RANGO_DEFECTO;
  const totalFilas = puede ? puntos.length + 1 : puntos.length; // +1: el renglón en blanco para el próximo punto
  const filas = [];
  for (let i = 0; i < totalFilas; i++) {
    const valor = i < puntos.length ? puntos[i].temp : '';
    filas.push(`<tr>
      <td>${i * _SN_CURVA_PASO_MIN}</td>
      <td>${puede
        ? `<input class="inp" id="sn-curva-temp-${i}" type="text" inputmode="decimal" style="width:90px" placeholder="°C" value="${_esc(_snV(valor))}" onchange="_snCurvaTemp(${i},this.value)" onkeydown="if(event.key==='Enter'){event.preventDefault();_snCurvaTemp(${i},this.value,true);}">`
        : _esc(_snV(valor))}</td>
    </tr>`);
  }
  const meta = _SN.curvaRef && _SN.curvaRef.cambiado_en
    ? `<div class="sn-hint2">Último cambio: ${_esc(_SN.curvaRef.cambiado_por_nombre || '')} &middot; ${fmtFechaCorta(_SN.curvaRef.cambiado_en)}</div>` : '';
  const dondeEditar = soloLectura ? '<div class="sn-hint2">Se edita desde la pesta&ntilde;a &laquo;Curva de referencia&raquo;, arriba.</div>' : '';
  const rangoHtml = `<label style="display:flex;align-items:center;gap:6px;font-size:12px;margin:8px 0">Rango de tolerancia (ancho total alrededor de la curva): ${puede
    ? `<input class="inp" id="sn-curva-rango" type="text" inputmode="decimal" style="width:70px" value="${_esc(_snV(rango))}" onchange="_snCurvaRango(this.value)">`
    : `<b>${_esc(_snNum(rango))}</b>`} &deg;C</label>`;
  return `<div class="sn-hint2">Es UNA sola curva, la misma para todos los sinterizados: se muestra como ejemplo a seguir (línea punteada violeta, con una franja de tolerancia) superpuesta a la curva real de esta colada. Una temperatura cada ${_SN_CURVA_PASO_MIN} minutos desde el arranque: completá una casilla y aparece sola la siguiente.</div>
    ${dondeEditar}
    ${rangoHtml}
    <div class="tbl-wrap">${totalFilas ? `<table class="sn-lect">
      <thead><tr><th>Minuto</th><th>Temp. (°C)</th></tr></thead><tbody>${filas.join('')}</tbody></table>`
    : '<div class="sn-vacio">Sin curva de referencia cargada todavía.</div>'}</div>
    ${puede ? `<div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      ${puntos.length ? `<button type="button" class="page-btn" onclick="_snCurvaQuitarUltimo()">Quitar &uacute;ltimo punto</button>` : ''}
      <button type="button" class="page-btn" id="sn-curva-guardar" onclick="_snCurvaGuardar()">Guardar curva</button>
    </div>` : ''}
    ${meta}`;
}
function _snCurvaRango(valor) {
  if (!_snPuedeEditarCurva()) return;
  if (!_SN.curvaRef) _SN.curvaRef = { puntos: [], rango: _SN_CURVA_RANGO_DEFECTO, cambiado_por_nombre: null, cambiado_en: null };
  _SN.curvaRef.rango = valor;
}
function _snRenderCurvaTab() {
  return `<div class="card sn-card">
    <div class="card-title">Curva de referencia del sinterizado</div>
    <div id="sn-curva-ref">${_snCurvaRefHtml(false)}</div>
  </div>`;
}
// `avanzarFoco`: true cuando lo dispara Enter (no un simple blur/tab) -- ahí
// además de confirmar el valor, salta el cursor a la casilla siguiente (exista
// ya o recién se haya creado), como en una planilla de cálculo.
function _snCurvaTemp(i, valor, avanzarFoco) {
  if (!_snPuedeEditarCurva()) return;
  if (!_SN.curvaRef) _SN.curvaRef = { puntos: [], cambiado_por_nombre: null, cambiado_en: null };
  const puntos = _SN.curvaRef.puntos;
  if (i < puntos.length) {
    if (valor === '') {
      // se vació un punto ya cargado: la curva es una secuencia sin huecos,
      // asi que se recorta ahí (y se pierden los que venían después)
      _SN.curvaRef.puntos = puntos.slice(0, i);
      _snRefrescar('sn-curva-ref', _snCurvaRefHtml(false));
      return;
    }
    puntos[i].temp = valor;
    if (avanzarFoco) _snCurvaEnfocar(i + 1);
    return;
  }
  // es el renglón en blanco del final
  if (valor === '') return;
  if (puntos.length >= _SN_CURVA_MAX_PUNTOS) { alert('Máximo ' + _SN_CURVA_MAX_PUNTOS + ' puntos en la curva de referencia.'); return; }
  puntos.push({ minuto: i * _SN_CURVA_PASO_MIN, temp: valor });
  _snRefrescar('sn-curva-ref', _snCurvaRefHtml(false));
  if (avanzarFoco) _snCurvaEnfocar(i + 1);
}
function _snCurvaEnfocar(i) {
  const inp = $id('sn-curva-temp-' + i);
  if (inp) inp.focus();
}
function _snCurvaQuitarUltimo() {
  if (!_snPuedeEditarCurva() || !_SN.curvaRef || !_SN.curvaRef.puntos.length) return;
  _SN.curvaRef.puntos.pop();
  _snRefrescar('sn-curva-ref', _snCurvaRefHtml(false));
}
async function _snCurvaGuardar() {
  if (!_snPuedeEditarCurva() || !_SN.curvaRef) return;
  const btn = $id('sn-curva-guardar');
  if (btn) { btn.disabled = true; btn.textContent = 'Guardando...'; }
  try {
    const puntos = _SN.curvaRef.puntos.map((p, i) => ({ minuto: i * _SN_CURVA_PASO_MIN, temp: p.temp }));
    const rango = _SN.curvaRef.rango;
    _SN.curvaRef = await api('/api/sinterizados/curva-referencia', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ puntos, rango }) });
    _snRefrescar('sn-curva-ref', _snCurvaRefHtml(false));
    _snLectResumen();
  } catch (e) {
    alert('No se pudo guardar la curva de referencia: ' + _snMsgError(e).replace(/^[A-Z]+: /, ''));
    if (btn) { btn.disabled = false; btn.textContent = 'Guardar curva'; }
  }
}

// ── Finalizar / reabrir / anular ─────────────────────────────────────────────
// Guardar "de todos los días" es el automático (_snAutoguardar, más arriba);
// esto es solo para las transiciones de estado, que son deliberadas y poco
// frecuentes -- por eso acá sí vale la pena un diálogo si hay un choque, y un
// re-render completo de la planilla (perder el foco de un campo no importa
// porque la persona recién tocó un botón, no estaba tipeando).
async function _snEnviar(body) {
  const a = _SN.actual;
  clearTimeout(_snAutoguardadoTimer);
  const btn = $id('sn-guardar');
  if (btn) { btn.disabled = true; btn.textContent = 'Guardando...'; }
  const scroll = $id('main') ? $id('main').scrollTop : 0;
  try {
    const d = await api('/api/sinterizados/' + a.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ modificado_en: a.modificado_en }, body)) });
    _SN.actual = d;
    _SN.sucio = false;
    _snTab('lista');
    if ($id('main')) $id('main').scrollTop = scroll;
    return true;
  } catch (e) {
    const m = _snMsgError(e);
    if (m.startsWith('CONFLICTO')) {
      if (confirm(m.replace('CONFLICTO: ', '') + '.\n\nPara no pisar lo suyo hay que recargar la planilla, y tus cambios sin guardar se pierden.\n\nAceptar = recargar · Cancelar = quedarme (para copiar lo que escribí)')) {
        await _snAbrir(a.id);
        await _snCargarLista();
      }
    } else {
      alert('No se pudo guardar: ' + m.replace(/^[A-Z]+: /, ''));
    }
    return false;
  } finally {
    const b = $id('sn-guardar');
    if (b) { b.disabled = false; b.textContent = 'Guardar'; }
  }
}

function _snCuerpoCompleto() {
  const a = _SN.actual;
  const body = { lecturas: a.lecturas, marcas: a.marcas, bolsas: a.bolsas, pasos: {} };
  for (const c of _SN_CAMPOS) body[c] = a[c];
  for (const k of Object.keys(a.pasos)) body.pasos[k] = true;
  return body;
}

// Fuerza que el guardado automático pendiente salga ya, sin esperar el
// debounce. No es un mecanismo aparte: es el mismo _snAutoguardar de siempre.
function _snGuardarAhora() {
  if (_snCongelada()) return;
  _snProgramarGuardado(true);
}

async function _snFinalizar() {
  if (_snCongelada()) return;
  const a = _SN.actual;
  const faltan = _SN_PASOS.filter(p => !a.pasos[p.clave]).map(p => p.titulo);
  let txt = '¿Finalizar el sinterizado N° ' + a.id + '?\n\nLa planilla queda congelada: para editarla hay que reabrirla.';
  if (faltan.length) txt += '\n\nTodavía figuran sin hacer:\n• ' + faltan.join('\n• ');
  if (!confirm(txt)) return;
  if (await _snEnviar(Object.assign(_snCuerpoCompleto(), { estado: 'finalizado' }))) await _snCargarLista();
}

// Reabrir es solo de administrador (lo valida tambien el servidor); el botón
// ya viene oculto para quien no lo es (ver _snRenderDetalle).
async function _snReabrir() {
  if (!_snCongelada()) return;
  if (!confirm('¿Reabrir la planilla N° ' + _SN.actual.id + ' para editarla? Queda registrado quién la reabrió.')) return;
  if (await _snEnviar({ estado: 'en_curso' })) await _snCargarLista();
}

async function _snAnular() {
  if (_snCongelada()) return;
  if (!confirm('¿Anular el sinterizado N° ' + _SN.actual.id + '?\n\nDeja de aparecer en la lista y en el indicador.')) return;
  clearTimeout(_snAutoguardadoTimer);
  try {
    await api('/api/sinterizados/' + _SN.actual.id, { method: 'DELETE' });
    _SN.actual = null;
    _SN.sucio = false;
    await _snCargarLista();
    _snTab('lista');
  } catch (e) { alert('No se pudo anular: ' + _snMsgError(e).replace(/^[A-Z]+: /, '')); }
}

// ── Imprimir la planilla ─────────────────────────────────────────────────────
// Lo que hay en pantalla (guardado o no): dos hojas como el papel, con el perfil y los espesores de las vistas.
function _snImprimir() {
  const a = _SN.actual;
  if (!a) return;
  const v = x => (x == null || x === '') ? '&nbsp;' : _esc(x);
  const f = iso => iso ? _esc(fmtFechaCorta(iso)) : '&nbsp;';
  const c = (et, val, flex) => `<div class="c" style="flex:${flex || 1}"><span>${et}</span><b>${val}</b></div>`;
  const fila = (...cs) => `<div class="f">${cs.join('')}</div>`;
  const ok = x => x ? '&#9745;' : '&#9744;';
  const filasLect = [];
  for (let i = 0; i < Math.max(20, a.lecturas.length); i++) {
    const x = a.lecturas[i] || {};
    filasLect.push(`<tr><td>${v(x.hora)}</td><td>${v(x.pot)}</td><td>${v(x.temp)}</td><td>${v(x.obs)}</td></tr>`);
  }
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>PCS-PG851.06-A3 &middot; Sinterizado N&deg; ${a.id}</title><style>
    body { font-family: Arial, sans-serif; font-size: 12px; color: #111; margin: 14px 18px; }
    h1 { font-size: 16px; margin: 0; } h2 { font-size: 13px; margin: 14px 0 6px; background: #eee; padding: 3px 6px; }
    .cab { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #111; padding-bottom: 4px; margin-bottom: 8px; }
    .f { display: flex; gap: 8px; margin-bottom: 6px; } .c { border-bottom: 1px solid #555; padding: 2px 4px; min-height: 30px; }
    .c span { display: block; font-size: 10px; color: #555; } .c b { font-weight: 600; white-space: pre-wrap; }
    table { border-collapse: collapse; width: 100%; } td, th { border: 1px solid #555; padding: 3px 6px; font-size: 11px; height: 17px; } th { background: #eee; }
    .avisos { border-left: 3px solid #f90; padding: 4px 10px 4px 24px; margin: 8px 0; font-size: 11px; }
    .dib { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; } .dib .fig { text-align: center; font-size: 11px; }
    .sn-svg-perfil { width: 190px; height: auto; }
    .sn-vista { position: relative; width: 260px; aspect-ratio: 150 / 130; } .sn-vista svg { position: absolute; inset: 0; width: 100%; height: 100%; }
    .sn-medpar { position: absolute; transform: translate(-50%, -50%); width: 18%; display: flex; flex-direction: column; gap: 2px; }
    .sn-med-v { box-sizing: border-box; width: 100%; text-align: center; font-size: 11px; font-weight: 600; border-bottom: 1px solid #111; min-height: 14px; white-space: nowrap; }
    .sn-med-v::before { content: attr(data-l); font-size: 8px; font-weight: 400; color: #666; margin-right: 3px; }
    .hoja2 { page-break-before: always; }
    @media print { body { margin: 8mm; } }
  </style></head><body>
    <div class="cab"><h1>Planilla de Control de Sinterizado</h1><span>PCS-PG851.06-A3 &middot; Sinterizado N&deg; ${a.id} &middot; ${a.estado === 'finalizado' ? 'Finalizado' : 'En curso'}</span></div>
    <h2>Hoja 1 &middot; DESARME</h2>
    ${fila(c('Fecha', f(a.desarme_fecha)), c('Cantidad de coladas', v(a.coladas)), c('&Oslash; final (mm)', v(a.diametro_final)), c('Altura del crisol (mm)', v(a.altura)), c('Altura de la conicidad (mm)', v(a.altura_conicidad)))}
    ${fila(c('Aspecto general', v(a.aspecto)))}
    ${fila(c('Espesor m&iacute;nimo (mm) &mdash; zona en el gr&aacute;fico con ROJO', v(a.espesor_min)), c('Filtraciones total &mdash; VERDE', v(a.filtraciones)), c('Manta oscura / quemada &mdash; AZUL', v(a.manta_oscura)))}
    ${fila(c('Se tom&oacute; fotograf&iacute;a', v(a.foto)), c('Archivo', v(a.foto_archivo), 2))}
    ${fila(c('Polvo refractario en contacto con la manta cer&aacute;mica', v(a.polvo), 2), c('Cantidad aprox. (mm)', v(a.polvo_mm)))}
    <div class="dib"><div class="fig">${_snSvgPerfil(a.marcas, false, null)}Perfil (todos los puntos, sin atenuar ninguno)</div>
      ${(a.marcas.alturas || []).map((altura, idx) => `<div class="fig">${_snVistaHtml(altura, idx, false, null, null, true)}${_snNum(altura.mm)} mm</div>`).join('') || '<div class="fig">Sin mediciones cargadas</div>'}</div>
    <div style="font-size:10px;margin-top:4px"><span style="color:${_SN_MARCAS.espesor.color}">&#9679;</span> espesor m&iacute;nimo &nbsp; <span style="color:${_SN_MARCAS.filtracion.color}">&#9679;</span> filtraciones &nbsp; <span style="color:${_SN_MARCAS.manta.color}">&#9679;</span> manta oscura / quemada (puntos del perfil) &nbsp;&middot;&nbsp; Mediciones: en cada flecha, ref. = espesor del refractario y polvo = espesor del polvo (mm)</div>

    <div class="hoja2"><h2>Hoja 2 &middot; ARMADO</h2>
    ${fila(c('Fecha', f(a.armado_fecha)), c('Orificio de la antena revisado (no demasiado grande)', ok(a.antena_orificio_ok), 2))}
    ${fila(c('Control de formaleta &mdash; Medidas', v(a.formaleta_medidas), 2), c('Revisado', ok(a.formaleta_medidas_ok)), c('Rebabado', v(a.formaleta_rebabado), 2), c('Revisado', ok(a.formaleta_rebabado_ok)))}
    ${fila(c('Revis&oacute; centrado', v(a.centrado_reviso)), c('V&deg;B&deg; Resp. de Fusi&oacute;n', v(a.centrado_vb_fusion)), c('V&deg;B&deg; Resp. de Producci&oacute;n', v(a.centrado_vb_produccion)))}
    ${['piso', 'pared', 'corona'].map(seccion => {
      const filasB = ((a.bolsas && a.bolsas[seccion]) || []).map(b => `<tr><td>${v(b.cantidad)}</td><td>${v(b.producto)}</td><td>${v(b.lote)}</td></tr>`).join('')
        || '<tr><td colspan="3">Sin bolsas cargadas</td></tr>';
      return `<h2 style="margin-top:8px">Bolsas &mdash; ${_esc(_SN_ETIQUETA_BOLSA[seccion])}</h2>
        <table><thead><tr><th style="width:70px">Bolsas</th><th>Producto</th><th style="width:140px">Lote</th></tr></thead><tbody>${filasB}</tbody></table>`;
    }).join('')}
    ${fila(c('V&deg;B&deg; colocaci&oacute;n de enduido, formaleta y revestimiento &mdash; Resp. de Producci&oacute;n', v(a.paredes_vb_produccion)))}
    <ul class="avisos"><li>El piso tiene que quedar de &asymp; 18 cm.</li><li>La cantidad de bolsas depende del di&aacute;metro inferior del horno sin refractario.</li><li>Modificar la altura de la formaleta seg&uacute;n el espesor del piso.</li><li>Verificar la soldadura de la costura de la formaleta.</li></ul>
    <table><thead><tr><th style="width:70px">HORA</th><th style="width:70px">POT.</th><th style="width:90px">TEMP. (&deg;C)</th><th>OBSERVACIONES</th></tr></thead><tbody>${filasLect.join('')}</tbody></table>
    ${fila(c('Observaciones', v(a.observaciones)))}
    </div>
    <script>window.onload = function () { setTimeout(function () { window.print(); }, 250); };<\/script>
  </body></html>`;
  const w = window.open('', '_blank');
  if (!w) { alert('El navegador bloqueó la ventana de impresión. Permití las ventanas emergentes para este sitio.'); return; }
  w.document.write(html);
  w.document.close();
}

// ── Pestaña Procedimiento ────────────────────────────────────────────────────
// Antes embebía el contenido entero del módulo de Procedimientos acá adentro
// (_procRegistrar + _procRenderPagina) -- pedido explícito del usuario:
// sacar eso y dejar solo un VÍNCULO al procedimiento real, que ya tiene su
// propia pantalla completa (ver/editar/aprobar) en el módulo general. Más
// simple, y de paso evita que esta pestaña dependa de duplicar la máquina
// de edición/aprobación de otro módulo.
//
// _snCargarProcPg85106 se llama una sola vez por sesión (junto con la curva
// de referencia y el catálogo de refractarios, en el Promise.all de
// showSinterizado), así que el render en sí queda sincrónico, igual que el
// resto de las pestañas. El código 'sinterizado_pg85106' sigue hardcodeado
// acá -- es la clave estable que usa este módulo (ver el comentario de la
// tabla en main.py); si alguna vez se edita el código de ESTE procedimiento
// en particular desde "Editar código", hay que actualizar este literal a
// mano (los anexos que lo referencian desde OTROS procedimientos sí se
// actualizan solos, ver _proc_actualizar_referencias_codigo en main.py --
// esto es distinto porque es un literal en el código fuente, no un dato en
// la base).
async function _snCargarProcPg85106() {
  _SN.procPg85106 = await _procCargarPorCodigo('sinterizado_pg85106');
}
function _snRenderProcedimiento() {
  const p = _SN.procPg85106;
  if (!p) return '<div class="loading">Cargando...</div>';
  if (p.error) return `<div class="card"><div class="sn-vacio" style="color:var(--red)">${_esc(p.error)}</div></div>`;
  _procEstilos(); // solo para la insignia azul del código (_procCodigoBadgeHtml)
  return `<div class="card">
    <div class="card-title">${_esc(p.titulo)} ${_procCodigoBadgeHtml(p.codigo)}</div>
    <div class="sn-hint" style="margin-bottom:10px">Este procedimiento se edita y se aprueba en el m&oacute;dulo de Procedimientos, no desde ac&aacute;.</div>
    <button type="button" class="page-btn" onclick="go('procedimiento_detalle', '${_esc(p.codigo)}')">&#8594; Ver procedimiento completo</button>
  </div>`;
}

// ── Pestaña Indicador ────────────────────────────────────────────────────────
// PG 851.06: "seguimiento de cantidad y frecuencia de preparacion de horno
// nuevo". Cuenta las planillas FINALIZADAS (un crisol a medio armar todavia no
// es un horno nuevo preparado).
function _snDias(a, b) { return Math.round((new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00')) / 86400000); }

function _snRenderIndicador() {
  if (_SN.errorLista) return `<div class="card"><div class="sn-vacio" style="color:var(--red)">${_esc(_SN.errorLista)}</div></div>`;
  const fin = _SN.lista.filter(r => r.estado === 'finalizado')
    .map(r => ({ r, fecha: _snFechaPrep(r) })).filter(x => x.fecha).sort((x, y) => x.fecha < y.fecha ? -1 : x.fecha > y.fecha ? 1 : x.r.id - y.r.id);
  const enCurso = _SN.lista.filter(r => r.estado === 'en_curso').length;
  const hoy = _fechaISO(new Date());
  const anio = hoy.slice(0, 4);
  const intervalos = fin.slice(1).map((x, i) => _snDias(fin[i].fecha, x.fecha));
  const prom = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
  const intProm = prom(intervalos);
  const coladas = fin.map(x => x.r.coladas).filter(x => x != null);
  const colProm = prom(coladas);
  const ultima = fin.length ? fin[fin.length - 1].fecha : null;
  const kpi = (n, t) => `<div class="sn-kpi"><b>${n}</b><span>${t}</span></div>`;
  const meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
  const anios = [...new Set([anio, ...fin.map(x => x.fecha.slice(0, 4))])].sort().reverse();
  const tabla = anios.map(y => {
    const cnt = Array(12).fill(0);
    fin.forEach(x => { if (x.fecha.slice(0, 4) === y) cnt[Number(x.fecha.slice(5, 7)) - 1]++; });
    return `<tr><td><b>${y}</b></td>${cnt.map(n => `<td style="text-align:center;${n ? 'font-weight:700' : 'color:var(--muted)'}">${n || '·'}</td>`).join('')}<td style="text-align:center"><b>${cnt.reduce((s, n) => s + n, 0)}</b></td></tr>`;
  }).join('');
  const detalle = fin.slice().reverse().map(x => {
    const i = fin.indexOf(x);
    return `<tr class="tr-link" onclick="_snAbrir(${x.r.id})"><td><b>N&deg; ${x.r.id}</b></td><td>${_esc(fmtFechaCorta(x.fecha))}</td>
      <td style="text-align:right">${i > 0 ? _snDias(fin[i - 1].fecha, x.fecha) + ' d' : '—'}</td><td style="text-align:right">${_snNum(x.r.coladas)}</td><td style="text-align:right">${_snNum(x.r.espesor_min)}</td></tr>`;
  }).join('');
  return `
    <div class="sn-kpis">
      ${kpi(fin.length, 'crisoles nuevos preparados (finalizados)')}
      ${kpi(fin.filter(x => x.fecha.slice(0, 4) === anio).length, 'en ' + anio)}
      ${kpi(intProm == null ? '—' : Math.round(intProm) + ' d', 'intervalo promedio entre preparaciones')}
      ${kpi(ultima ? _snDias(ultima, hoy) + ' d' : '—', 'desde la última preparación' + (ultima ? ' (' + fmtFechaCorta(ultima) + ')' : ''))}
      ${kpi(colProm == null ? '—' : Math.round(colProm * 10) / 10, 'coladas promedio por crisol (Hoja 1)')}
    </div>
    ${enCurso ? `<div style="font-size:12px;color:var(--muted);margin-bottom:10px">${enCurso} planilla${enCurso > 1 ? 's' : ''} en curso: no cuenta${enCurso > 1 ? 'n' : ''} hasta que se finalice${enCurso > 1 ? 'n' : ''}.</div>` : ''}
    <div class="card sn-card"><div class="card-title">Preparaciones por mes</div><div class="tbl-wrap">
      <table><thead><tr><th>A&ntilde;o</th>${meses.map(m => `<th style="text-align:center">${m}</th>`).join('')}<th style="text-align:center">Total</th></tr></thead><tbody>${tabla}</tbody></table></div></div>
    <div class="card sn-card"><div class="card-title">Detalle</div><div class="tbl-wrap">
      ${fin.length ? `<table><thead><tr><th>N&deg;</th><th>Fecha</th><th style="text-align:right">Desde la anterior</th><th style="text-align:right">Coladas del crisol</th><th style="text-align:right">Espesor m&iacute;n. (mm)</th></tr></thead><tbody>${detalle}</tbody></table>` : '<div class="sn-vacio">Todavía no hay sinterizados finalizados.</div>'}</div></div>`;
}
